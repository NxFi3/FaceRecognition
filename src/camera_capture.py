"""Low-latency camera capture that always exposes the newest frame.

The capture thread is intentionally tiny: it continuously reads frames and keeps
only the newest one. Processing workers consume snapshots and may skip frames
when inference is slower than the camera, preventing stale-frame buildup.
"""

from __future__ import annotations

import threading
import logging
import time

import cv2

logger = logging.getLogger(__name__)


class LatestFrameCapture:
    """Continuously capture a camera and expose only its newest frame."""

    def __init__(self, camera_id, target_fps, open_camera, raw_bayer=False):
        self.camera_id = int(camera_id)
        self.target_fps = int(target_fps)
        self._open_camera = open_camera
        self._raw_bayer = bool(raw_bayer)

        self._stop_event = threading.Event()
        self._condition = threading.Condition()
        self._cap = None
        self._thread = None
        self._frame = None
        self._sequence = 0
        self._error = None

        self.width = 0
        self.height = 0
        self.fps = 0.0

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return

        logger.info("CAPTURE_START id=%s target_fps=%s raw_bayer=%s", self.camera_id, self.target_fps, self._raw_bayer)
        cap = self._open_camera(self.camera_id, self.target_fps)

        if self._raw_bayer:
            # The deployed ov534 cameras can expose native 8-bit GRBG Bayer.
            # This cuts USB payload from 16 bits/pixel YUYV to 8 bits/pixel
            # without changing spatial resolution. V4L2 defines GRBG as an
            # 8-bit-per-sample Bayer format.
            cap.set(
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc(*"GRBG"),
            )
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)

        self.width = width
        self.height = height
        self.fps = fps
        logger.info("CAPTURE_CONFIGURED id=%s actual=%sx%s fps=%s", self.camera_id, width, height, fps)
        self._cap = cap
        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"camera-capture-{self.camera_id}",
            daemon=True,
        )
        self._thread.start()

    def _capture_loop(self):
        cap = self._cap
        consecutive_failures = 0

        try:
            while not self._stop_event.is_set():
                ok, frame = cap.read()

                if not ok or frame is None:
                    consecutive_failures += 1
                    if consecutive_failures == 1 or consecutive_failures == 10:
                        logger.warning("CAPTURE_READ_FAIL id=%s consecutive=%s", self.camera_id, consecutive_failures)
                    if consecutive_failures >= 10:
                        with self._condition:
                            self._error = (
                                f"Camera {self.camera_id} frame capture failed "
                                f"({consecutive_failures} consecutive failures)."
                            )
                            self._condition.notify_all()
                        # Avoid a hot retry loop while a USB/V4L2 device is
                        # temporarily unavailable.
                        time.sleep(0.01)
                    else:
                        time.sleep(0.002)
                    continue

                consecutive_failures = 0

                if self._raw_bayer:
                    # OpenCV/V4L2 may expose the packed Bayer frame as
                    # shape (1, width*height). Normalize that to 2-D before
                    # demosaicing. The output remains the BGR format expected
                    # by YOLO, YuNet and the rest of the pipeline.
                    if frame.ndim == 2 and frame.shape == (self.height, self.width):
                        bayer = frame
                    elif frame.size == self.width * self.height:
                        bayer = frame.reshape(self.height, self.width)
                    else:
                        with self._condition:
                            self._error = (
                                f"Camera {self.camera_id} returned an unexpected "
                                f"raw Bayer shape: {getattr(frame, 'shape', None)}"
                            )
                            self._condition.notify_all()
                        break

                    frame = cv2.cvtColor(
                        bayer,
                        cv2.COLOR_BayerGRBG2BGR,
                    )

                # Frame ownership transfers to the consumer by reference.
                # The reader never mutates an already-published frame; it only
                # replaces the reference on the next successful capture.
                with self._condition:
                    self._frame = frame
                    self._sequence += 1
                    self._condition.notify_all()
        finally:
            try:
                cap.release()
            except Exception:
                logger.exception("CAPTURE_RELEASE_ERROR id=%s", self.camera_id)

            with self._condition:
                self._cap = None
                self._condition.notify_all()

    def wait_for_frame(self, last_sequence=0, timeout=1.0):
        """Return the newest frame whose sequence is newer than last_sequence."""
        deadline = time.monotonic() + max(0.01, float(timeout))

        with self._condition:
            while (
                self._sequence <= int(last_sequence)
                and not self._stop_event.is_set()
            ):
                if self._error is not None:
                    raise RuntimeError(self._error)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None, None

                self._condition.wait(remaining)

            if self._error is not None and self._frame is None:
                raise RuntimeError(self._error)

            if self._sequence <= int(last_sequence) or self._frame is None:
                return None, None

            return self._sequence, self._frame

    def stop(self, join_timeout=1.5):
        self._stop_event.set()

        thread = self._thread
        if thread is None:
            return

        with self._condition:
            self._condition.notify_all()

        if thread is not threading.current_thread():
            thread.join(max(0.1, float(join_timeout)))

        # V4L2 reads normally unblock when the capture thread observes the
        # stop flag. As a last resort, release the handle to break a blocked
        # driver read before joining again.
        if thread.is_alive():
            cap = self._cap
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            thread.join(0.5)

        if not thread.is_alive():
            self._thread = None
