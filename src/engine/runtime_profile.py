"""Opt-in, bounded runtime observations. No model/configuration/scheduling policy.

Only tools.profile_runtime enables this collector. No I/O occurs on inference
threads. Timings are inclusive and histogram percentiles are upper bounds
(within 10% except the <=1us / overflow bins). No frames or identities stored.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from functools import wraps
import math
import threading
import time
import weakref


class Histogram:
    def __init__(self):
        self.bins = [0] * 256
        self.count = 0
        self.total = 0.0
        self.maximum = 0.0

    def add(self, milliseconds):
        value = max(0.0, float(milliseconds))
        index = min(255, max(0, math.ceil(math.log(max(value, .001) / .001, 1.1))))
        self.bins[index] += 1
        self.count += 1
        self.total += value
        self.maximum = max(self.maximum, value)

    def merge(self, other):
        self.bins = [a + b for a, b in zip(self.bins, other.bins)]
        self.count += other.count
        self.total += other.total
        self.maximum = max(self.maximum, other.maximum)

    def report(self):
        def percentile(fraction):
            target = math.ceil(self.count * fraction)
            seen = 0
            for index, count in enumerate(self.bins):
                seen += count
                if seen >= target:
                    return self.maximum if index == 255 else min(self.maximum, .001 * 1.1 ** index)
            return self.maximum
        return {"count": self.count, "mean_ms": self.total / max(1, self.count),
                "p50_ms": percentile(.5), "p95_ms": percentile(.95),
                "max_ms": self.maximum}


class RuntimeProfile:
    def __init__(self):
        self.enabled = False
        self._local = threading.local()
        self._lock = threading.Lock()
        self._counters = defaultdict(lambda: defaultdict(int))
        self._timings = defaultdict(dict)
        self._gauges = defaultdict(dict)
        self._tracks = {}
        self._details = {}
        self._preview_keys = {}
        self.first_frame = threading.Event()
        self.first_frame_at = None
        self.started_at = time.perf_counter()
        self._previous_at = self.started_at
        self._previous_counts = {}

    def enable(self):
        self.started_at = self._previous_at = time.perf_counter()
        self.enabled = True

    def camera(self):
        return getattr(self._local, "camera", "application")

    @contextmanager
    def scope(self, camera):
        previous = self.camera()
        self._local.camera = str(camera)
        try:
            yield
        finally:
            self._local.camera = previous

    def in_camera(self, camera_getter):
        def decorate(function):
            @wraps(function)
            def wrapped(*args, **kwargs):
                if not self.enabled:
                    return function(*args, **kwargs)
                with self.scope(camera_getter(*args, **kwargs)):
                    return function(*args, **kwargs)
            return wrapped
        return decorate

    def stage(self, name):
        def decorate(function):
            @wraps(function)
            def wrapped(*args, **kwargs):
                return self.call(name, function, *args, **kwargs)
            return wrapped
        return decorate

    def call(self, name, function, *args, **kwargs):
        if not self.enabled:
            return function(*args, **kwargs)
        started = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            self.observe(name, (time.perf_counter() - started) * 1000)

    def observe(self, name, milliseconds):
        if not self.enabled:
            return
        with self._lock:
            timings = self._timings[self.camera()]
            if name not in timings:
                timings[name] = Histogram()
            timings[name].add(milliseconds)

    def count(self, name, amount=1):
        if self.enabled:
            with self._lock:
                self._counters[self.camera()][name] += amount

    def gauge(self, name, value):
        if self.enabled:
            with self._lock:
                self._set_gauge(self.camera(), name, value)

    def _set_gauge(self, camera, name, value):
        previous = self._gauges[camera].get(name, {"peak": value})
        self._gauges[camera][name] = {"current": value, "peak": max(previous["peak"], value)}

    def details(self, **values):
        if self.enabled:
            with self._lock:
                self._details.setdefault(self.camera(), {}).update(values)

    def received(self, sequence, previous, published_at):
        if not self.enabled:
            return
        self._local.frame_started = time.perf_counter()
        self._local.published_at = published_at
        self.count("consumed_frames")
        self.count("skipped_capture_frames", max(0, sequence - previous - 1))
        self.observe("frame_age_at_consume", (self._local.frame_started - published_at) * 1000)

    def frame_done(self):
        if not self.enabled:
            return
        now = time.perf_counter()
        self.count("processed_frames")
        self.observe("frame_processing", (now - self._local.frame_started) * 1000)
        self.observe("frame_age_at_completion", (now - self._local.published_at) * 1000)
        if not self.first_frame.is_set():
            with self._lock:
                if self.first_frame_at is None:
                    self.first_frame_at = now
                    self.first_frame.set()

    def _job_delta(self, camera, kind, pending=0, active=0):
        with self._lock:
            for name in (kind, "downstream"):
                for suffix, delta in (("pending", pending), ("active", active)):
                    key = f"{name}.{suffix}"
                    value = self._gauges[camera].get(key, {"current": 0})["current"] + delta
                    self._set_gauge(camera, key, value)
                # Aggregate peak must be observed simultaneously, not sum of camera peaks.
                for suffix in ("pending", "active"):
                    key = f"{name}.{suffix}"
                    total = sum(v.get(key, {"current": 0})["current"]
                                for c, v in self._gauges.items() if c != "aggregate")
                    self._set_gauge("aggregate", key, total)

    def submit(self, executor, kind, function, *args, **kwargs):
        """Observe the same executor admission; never block, cap, or drop work."""
        if not self.enabled:
            return executor.submit(function, *args, **kwargs)
        camera = self.camera()
        submitted = time.perf_counter()
        self.count(f"{kind}.submitted")
        self._job_delta(camera, kind, pending=1)

        def run():
            with self.scope(camera):
                self._job_delta(camera, kind, pending=-1, active=1)
                self.observe(f"{kind}.queue_delay", (time.perf_counter() - submitted) * 1000)
                try:
                    return self.call(kind, function, *args, **kwargs)
                except BaseException:
                    self.count(f"{kind}.failed")
                    raise
                finally:
                    self._job_delta(camera, kind, active=-1)
                    self.count(f"{kind}.completed")

        try:
            future = executor.submit(run)
        except BaseException:
            self._job_delta(camera, kind, pending=-1)
            self.count(f"{kind}.submit_failed")
            raise

        def cancelled(done):
            if done.cancelled():
                with self.scope(camera):
                    self._job_delta(camera, kind, pending=-1)
                    self.count(f"{kind}.cancelled")
        future.add_done_callback(cancelled)
        return future

    def track_created(self, track):
        if not self.enabled:
            return
        key = id(track)
        camera = self.camera()
        def removed(reference):
            with self._lock:
                entry = self._tracks.get(key)
                if entry is not None and entry[0] is reference:
                    if "face" not in entry[2]:
                        self._counters[camera]["tracks_retired_without_face_identity"] += 1
                    if not entry[2]:
                        self._counters[camera]["tracks_retired_without_any_identity"] += 1
                    del self._tracks[key]
        with self._lock:
            self._tracks[key] = (weakref.ref(track, removed), time.perf_counter(), set(), camera)
        self.count("tracks_created")

    def identity(self, source):
        """Observe public event application, never inspect/change matching rules."""
        def decorate(function):
            @wraps(function)
            def wrapped(engine, track, *args, **kwargs):
                result = function(engine, track, *args, **kwargs)
                if self.enabled and track.student_id is not None:
                    with self._lock:
                        entry = self._tracks.get(id(track))
                        if entry is not None and entry[0]() is track and source not in entry[2]:
                            first_identity = not entry[2]
                            entry[2].add(source)
                            elapsed = (time.perf_counter() - entry[1]) * 1000
                        else:
                            elapsed = None
                    if elapsed is not None:
                        if first_identity:
                            self.observe("time_to_identity.first", elapsed)
                        self.observe(f"time_to_identity.{source}", elapsed)
                        self.count(f"tracks_identified.{source}")
                return result
            return wrapped
        return decorate

    def rendered(self, image_key, published_at, elapsed_ms):
        if not self.enabled:
            return
        camera = self.camera()
        self.count("gui_renders")
        if self._preview_keys.get(camera) == image_key:
            self.count("gui_repeated_renders")
        self._preview_keys[camera] = image_key
        self.observe("gui_render", elapsed_ms)
        self.observe("preview_mailbox_age_at_render", (time.monotonic() - published_at) * 1000)

    def snapshot(self):
        now = time.perf_counter()
        with self._lock:
            # Copy small fixed histograms under lock; quantiles/JSON/I/O run outside it.
            counters = {c: dict(v) for c, v in self._counters.items()}
            gauges = {c: {k: dict(v) for k, v in values.items()} for c, values in self._gauges.items()}
            timings = {}
            for camera, values in self._timings.items():
                timings[camera] = {}
                for name, histogram in values.items():
                    copy = Histogram()
                    copy.merge(histogram)
                    timings[camera][name] = copy
            details = {c: dict(v) for c, v in self._details.items()}
            first_frame_at = self.first_frame_at
            for camera in details:
                live = [entry for entry in self._tracks.values() if entry[3] == camera]
                gauges.setdefault(camera, {})["live_tracks_without_face_identity"] = {
                    "current": sum("face" not in entry[2] for entry in live)}
            # Current sums are meaningful; sums of per-camera peaks are not simultaneous peaks.
            aggregate_gauges = gauges.setdefault("aggregate", {})
            for camera, values in gauges.items():
                if camera == "aggregate":
                    continue
                for key, value in values.items():
                    if key.endswith((".pending", ".active")):
                        continue
                    aggregate_gauges.setdefault(key, {"current": 0})["current"] += value["current"]
        aggregate_counts = defaultdict(int)
        aggregate_timings = {}
        for values in counters.values():
            for key, value in values.items():
                aggregate_counts[key] += value
        for values in timings.values():
            for key, value in values.items():
                aggregate_timings.setdefault(key, Histogram()).merge(value)
        counters["aggregate"] = dict(aggregate_counts)
        timings["aggregate"] = aggregate_timings
        interval = max(now - self._previous_at, 1e-9)
        cameras = {}
        for camera in counters.keys() | timings.keys() | gauges.keys() | details.keys():
            counts = counters.get(camera, {})
            previous = self._previous_counts.get(camera, {})
            cameras[camera] = {
                "counters": counts, "gauges": gauges.get(camera, {}),
                "timings": {k: v.report() for k, v in timings.get(camera, {}).items()},
                "capture_fps": (counts.get("captured_frames", 0) - previous.get("captured_frames", 0)) / interval,
                "processed_fps": (counts.get("processed_frames", 0) - previous.get("processed_frames", 0)) / interval,
                "details": details.get(camera, {}),
            }
        self._previous_counts, self._previous_at = counters, now
        return {"elapsed_s": now - self.started_at, "interval_s": interval,
                "active_elapsed_s": None if first_frame_at is None else now - first_frame_at,
                "cameras": cameras}


profile = RuntimeProfile()
