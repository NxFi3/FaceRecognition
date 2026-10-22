"""Small runtime controls; no identity or matching policy lives here."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
import threading
import time


@dataclass(frozen=True)
class RuntimeProfile:
    preview_fps: int
    torch_threads: int
    face_workers: int
    downstream_capacity: int
    idle_detection_seconds: float
    max_frame_age: float = 0.25


PROFILES = {
    'low-power': RuntimeProfile(5, 1, 1, 4, 0.5),
    'balanced': RuntimeProfile(10, 1, 1, 6, 0.3),
    'performance': RuntimeProfile(15, 2, 2, 8, 0.2),
}


def profile():
    name = os.environ.get('FACE_RUNTIME_PROFILE', 'balanced')
    if name not in PROFILES:
        raise ValueError(f'Unknown FACE_RUNTIME_PROFILE: {name}')
    return PROFILES[name]


def thread_count(name, default):
    return max(1, min(16, int(os.environ.get(name, default))))


def configure_native_environment():
    # Called before numpy/OpenCV/torch imports; explicit operator overrides win.
    for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
                'NUMEXPR_NUM_THREADS'):
        os.environ.setdefault(key, '1')


class BoundedExecutor:
    """Nonblocking admission with a bound on running + waiting jobs.

    The caller retains important work when admission returns None. Cancellation
    releases capacity as well as completion. No private executor queue access.
    """
    def __init__(self, workers, capacity):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='smart-face')
        self._slots = threading.BoundedSemaphore(capacity)
        self._lock = threading.Lock()
        self._pending = 0

    @property
    def pending(self):
        with self._lock:
            return self._pending

    def submit(self, fn, *args, **kwargs):
        if not self._slots.acquire(blocking=False):
            return None
        with self._lock:
            self._pending += 1
        try:
            future = self._pool.submit(fn, *args, **kwargs)
        except BaseException:
            self._release(None)
            raise
        future.add_done_callback(self._release)
        return future

    def _release(self, _future):
        with self._lock:
            self._pending -= 1
        self._slots.release()

    def shutdown(self, wait=True):
        self._pool.shutdown(wait=wait, cancel_futures=True)


class DetectionSchedule:
    """Back off only empty, quiet scenes; occupied scenes keep legacy cadence.

    Motion is sampled before annotations at 64x36. A periodic full detection
    covers slow arrivals and motion-test misses. No identity state is consulted.
    """
    def __init__(self, interval, idle_seconds):
        self.interval = interval
        self.idle_seconds = idle_seconds
        self.last_detection = None
        self.previous = None

    def due(self, frame, frame_no, tracks, now=None):
        import cv2
        now = time.monotonic() if now is None else now
        small = cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        motion = self.previous is None or cv2.mean(cv2.absdiff(gray, self.previous))[0] > 3.0
        self.previous = gray
        due = (self.last_detection is None or
               (frame_no % self.interval == 0 and (tracks or motion)) or
               (not tracks and now - self.last_detection >= self.idle_seconds))
        if due:
            self.last_detection = now
        return due
