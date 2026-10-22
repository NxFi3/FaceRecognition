"""Single-owner capture with a one-frame mailbox and interruptible reconnects."""
from __future__ import annotations
import logging
import threading
import time
import cv2
from .profiling import metrics


class LatestFrameCapture:
    def __init__(self, camera_id, target_fps, open_camera, raw_bayer=False):
        self.camera_id = int(camera_id)
        self.target_fps = int(target_fps)
        self._open_camera = open_camera
        self._raw_bayer = bool(raw_bayer)
        self._stop_event = threading.Event()
        self._condition = threading.Condition()
        self._cap = self._thread = self._frame = None
        self._sequence = 0
        self._captured_at = 0.0
        self._stop_warned = False
        self.width = self.height = 0
        self.fps = 0.0
        self._prefix = f'capture.{self.camera_id}'

    @staticmethod
    def _release(cap):
        try:
            cap.release()
        except Exception:
            logging.exception("Camera handle release failed")

    def _open(self):
        cap = self._open_camera(self.camera_id, self.target_fps)
        try:
            if self._raw_bayer:
                if not cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'GRBG')):
                    raise RuntimeError('Camera rejected explicitly requested GRBG format')
                cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
            self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            self.fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            return cap
        except Exception:
            self._release(cap)
            raise

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        with self._condition:
            self._frame = None
            self._captured_at = 0.0
        self._stop_event.clear()
        self._stop_warned = False
        # Opening/reopening belongs to the capture thread, including a camera
        # that is unplugged at startup.
        self._cap = None
        self._thread = threading.Thread(target=self._capture_loop,
                                        name=f'camera-capture-{self.camera_id}', daemon=True)
        self._thread.start()

    def _capture_loop(self):
        cap = self._cap
        failures = 0
        backoff = 0.25
        first_open = True
        opened_once = False
        try:
            while not self._stop_event.is_set():
                if cap is None:
                    if not first_open and self._stop_event.wait(backoff):
                        break
                    first_open = False
                    try:
                        cap = self._open()
                        self._cap = cap
                        metrics.count(self._prefix + ".opens")
                        if opened_once:
                            metrics.count(self._prefix + ".reconnects")
                        opened_once = True
                        failures = 0
                    except Exception:
                        logging.warning('Camera %s reconnect failed', self.camera_id, exc_info=True)
                        backoff = min(5.0, backoff * 2)
                        continue
                try:
                    with metrics.stage(self._prefix + '.read'):
                        ok, frame = cap.read()
                    captured_at = time.monotonic()
                    if not ok or frame is None or not frame.size:
                        raise ValueError('Empty camera frame')
                    if self._raw_bayer:
                        if frame.size != self.width * self.height:
                            raise ValueError(f'Unexpected Bayer frame shape: {frame.shape}')
                        with metrics.stage(self._prefix + '.preprocess'):
                            frame = cv2.cvtColor(frame.reshape(self.height, self.width),
                                                 cv2.COLOR_BayerGRBG2BGR)
                    if frame.ndim != 3 or frame.shape[2] != 3 or str(frame.dtype) != "uint8":
                        raise ValueError(f'Unexpected camera frame shape: {frame.shape}')
                except Exception:
                    failures += 1
                    metrics.count(self._prefix + '.failed_reads')
                    if failures >= 10:
                        logging.warning('Camera %s disconnected; reconnecting', self.camera_id)
                        self._release(cap)
                        cap = self._cap = None
                        with self._condition:
                            self._frame = None
                            metrics.gauge(self._prefix + ".depth", 0)
                            self._condition.notify_all()
                    self._stop_event.wait(0.02)
                    continue
                failures = 0
                backoff = 0.25
                with self._condition:
                    self._frame = frame
                    self._captured_at = captured_at
                    self._sequence += 1
                    metrics.gauge(self._prefix + ".depth", 1)
                    self._condition.notify_all()
        finally:
            if cap is not None:
                self._release(cap)
            with self._condition:
                self._cap = self._frame = None
                metrics.gauge(self._prefix + ".depth", 0)
                self._stop_event.set()
                self._condition.notify_all()

    def wait_for_frame(self, last_sequence=0, timeout=1.0, with_timestamp=False):
        """Return newest sequence/frame (optionally monotonic read-completion time).

        Arrays transfer by reference. Consumers must not mutate a published array.
        Read completion is a host timestamp, not a sensor exposure timestamp.
        """
        empty = (None, None, None) if with_timestamp else (None, None)
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while not self._stop_event.is_set():
                if self._frame is not None and self._sequence > int(last_sequence):
                    result = (self._sequence, self._frame)
                    return result + (self._captured_at,) if with_timestamp else result
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
        return empty

    def stop(self, join_timeout=1.5):
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, float(join_timeout)))
        # Never release a VideoCapture concurrently with read(): some native
        # backends crash. A blocked driver needs backend/OS-specific timeouts.
        stopped = thread is None or not thread.is_alive()
        if stopped:
            self._thread = None
        elif not self._stop_warned:
            self._stop_warned = True
            logging.error('Camera %s driver read did not stop within timeout', self.camera_id)
        return stopped
