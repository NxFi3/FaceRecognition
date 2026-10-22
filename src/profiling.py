"""Opt-in, bounded in-memory timing; JSONL I/O happens on a reporter thread."""
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
import atexit
import json
import logging
import os
import threading
import time


class Metrics:
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.lock = threading.Lock()
        self.samples = defaultdict(lambda: deque(maxlen=2048))
        self.counts = Counter()
        self.first_count = {}
        self.gauges = {}
        self.started = time.monotonic()

    def observe(self, name, seconds):
        if self.enabled:
            with self.lock:
                self.samples[name].append(max(0.0, seconds) * 1000)

    def count(self, name, amount=1):
        if self.enabled:
            with self.lock:
                self.counts[name] += amount
                self.first_count.setdefault(name, time.monotonic())

    def gauge(self, name, value):
        if self.enabled:
            with self.lock:
                self.gauges[name] = value

    @contextmanager
    def stage(self, name):
        if not self.enabled:
            yield
            return
        start = time.perf_counter()
        try:
            yield
        finally:
            self.observe(name, time.perf_counter() - start)

    def call(self, name, fn, *args, **kwargs):
        with self.stage(name):
            return fn(*args, **kwargs)

    def snapshot(self):
        with self.lock:
            samples = {k: list(v) for k, v in self.samples.items()}
            result = dict(counts=dict(self.counts), gauges=dict(self.gauges))
            first_count = dict(self.first_count)
        elapsed = time.monotonic() - self.started
        result['elapsed_seconds'] = elapsed
        result['stages_ms'] = {}
        for key, values in samples.items():
            values.sort()
            result['stages_ms'][key] = {
                'samples': len(values), 'p50': values[int((len(values)-1)*0.50)],
                'p95': values[int((len(values)-1)*0.95)],
                'mean': sum(values)/len(values),
            }
        result['processed_fps'] = {k: max(0, v - 1) / max(time.monotonic() - first_count[k], 0.001)
                                   for k, v in result['counts'].items()
                                   if k.endswith('.processed')}
        return result


metrics = Metrics(bool(os.environ.get('FACE_PROFILE_OUTPUT')))


def timed(name):
    def decorate(fn):
        from functools import wraps
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return metrics.call(name, fn, *args, **kwargs)
        return wrapper
    return decorate


class StaleWork(Exception):
    """An optional queued observation expired before using downstream compute."""


def measured_job(queued_at, name, fn, *args, max_queue_delay=None):
    delay = time.monotonic() - queued_at
    metrics.observe(name + '.queue_delay', delay)
    if max_queue_delay is not None and delay > max_queue_delay:
        metrics.count(name + '.expired')
        raise StaleWork()
    return metrics.call(name, fn, *args)


class MeasuredProxy:
    """Measure opaque downstream/event calls without changing their results."""
    def __init__(self, target, prefix):
        self._target, self._prefix = target, prefix

    def __getattr__(self, name):
        value = getattr(self._target, name)
        if not callable(value):
            return value
        return lambda *a, **kw: metrics.call(self._prefix + '.' + name, value, *a, **kw)


def start_reporter():
    if not metrics.enabled:
        return
    output = os.environ['FACE_PROFILE_OUTPUT']
    stop = threading.Event()
    try:
        import psutil
        process = psutil.Process()
        process.cpu_percent()
    except ImportError:
        process = None

    def write():
        result = metrics.snapshot()
        result['timestamp'] = time.time()
        if process is not None:
            result['cpu_percent'] = process.cpu_percent()
            result['rss_mb'] = process.memory_info().rss / (1024 * 1024)
            result['native_threads'] = process.num_threads()
        with open(output, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(result) + '\n')

    def run():
        while not stop.wait(5):
            try:
                write()
            except Exception:
                logging.exception('Runtime profiling output failed')
                return

    thread = threading.Thread(target=run, name='runtime-metrics', daemon=True)
    thread.start()

    def finish():
        stop.set()
        thread.join(1)
        try:
            write()
        except Exception:
            logging.exception('Final runtime profiling output failed')
    atexit.register(finish)


start_reporter()
