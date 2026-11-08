"""Low-level camera opening, independent of the Qt app and database."""
from __future__ import annotations

try:
    from ..localization import text as tr
except ImportError:
    from localization import text as tr
import logging
import sys
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
import cv2
import numpy as np

logger = logging.getLogger(__name__)
DEFAULT_CAMERA_TARGET_FPS = 60
DEFAULT_CAMERA_WIDTH = 1280
DEFAULT_CAMERA_HEIGHT = 720

def camera_backends():
    """Return camera APIs in preferred order for this platform."""
    if sys.platform.startswith("win"):
        return [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    if sys.platform.startswith("linux"):
        return [cv2.CAP_V4L2, cv2.CAP_ANY]
    return [cv2.CAP_ANY]

def _surface_ipu3_camera_names():
    """Return libcamera camera names for Intel IPU3 systems, if available."""
    try:
        node = Path("/sys/class/video4linux/video10/name")
        if not node.is_file() or not node.read_text(encoding="utf-8").strip().startswith("ipu3-cio2"):
            return None
        if not shutil.which("gst-launch-1.0"):
            return None
        plugin = subprocess.run(
            ["gst-inspect-1.0", "libcamerasrc"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        if plugin.returncode != 0:
            return None
        return (r"\\_SB_.PCI0.LNK0", r"\\_SB_.PCI0.LNK1")
    except (OSError, subprocess.SubprocessError):
        return None


class _LibcameraCapture:
    """OpenCV-compatible reader for IPU3 cameras through libcamera/GStreamer."""
    provides_bgr = True

    def __init__(self, camera_name, width, height, fps):
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self._frame_bytes = self.width * self.height * 3
        pipeline = [
            "gst-launch-1.0", "-q", "libcamerasrc", f"camera-name={camera_name}",
            "!", f"video/x-raw,width={self.width},height={self.height},framerate={self.fps}/1",
            "!", "videoconvert", "!", "video/x-raw,format=BGR",
            "!", "fdsink", "fd=1", "sync=false",
        ]
        self._stderr = tempfile.TemporaryFile()
        self._process = subprocess.Popen(
            pipeline, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=self._stderr, bufsize=0,
        )
        time.sleep(0.4)
        if self._process.poll() is not None:
            self._stderr.seek(0)
            detail = self._stderr.read().decode("utf-8", errors="replace").strip()
            self._stderr.close()
            message = "libcamera could not start the selected IPU3 camera"
            if detail:
                message += ": " + " | ".join(detail.splitlines()[-4:])
            raise RuntimeError(message)

    def isOpened(self):
        return self._process.poll() is None

    def get(self, prop):
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self.width)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self.height)
        if prop == cv2.CAP_PROP_FPS:
            return float(self.fps)
        return 0.0

    def set(self, prop, value):
        # The libcamera pipeline is configured at process start; Bayer controls
        # are intentionally ignored because this path delivers decoded BGR.
        return True

    def read(self):
        if not self.isOpened() or self._process.stdout is None:
            return False, None
        data = bytearray(self._frame_bytes)
        view = memoryview(data)
        offset = 0
        while offset < self._frame_bytes:
            count = self._process.stdout.readinto(view[offset:])
            if not count:
                return False, None
            offset += count
        frame = np.frombuffer(data, dtype=np.uint8).reshape(
            (self.height, self.width, 3)
        ).copy()
        return True, frame

    def release(self):
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=1)
        if self._process.stdout is not None:
            self._process.stdout.close()
        if self._stderr is not None and not self._stderr.closed:
            self._stderr.close()


def open_camera(camera_id, target_fps=DEFAULT_CAMERA_TARGET_FPS, width=DEFAULT_CAMERA_WIDTH, height=DEFAULT_CAMERA_HEIGHT):
    """Open a camera consistently; callers must inspect actual driver settings."""
    camera_id, target_fps, width, height = int(camera_id), int(target_fps), int(width), int(height)
    ipu3_cameras = _surface_ipu3_camera_names() if sys.platform.startswith("linux") else None
    if ipu3_cameras:
        if camera_id < 0 or camera_id >= len(ipu3_cameras):
            raise RuntimeError(tr("Selected IPU3 camera index is unavailable."))
        actual_fps = min(max(1, target_fps), 30)
        camera_name = ipu3_cameras[camera_id]
        logger.info(
            "CAMERA_OPEN_LIBCAMERA id=%s name=%s requested=%sx%s fps=%s",
            camera_id, camera_name, width, height, actual_fps,
        )
        cap = _LibcameraCapture(camera_name, width, height, actual_fps)
        logger.info(
            "CAMERA_OPEN_OK id=%s backend=libcamera actual=%sx%s fps=%s",
            camera_id, width, height, actual_fps,
        )
        return cap

    backends = camera_backends()
    logger.info("CAMERA_OPEN_START id=%s target_fps=%s requested=%sx%s backends=%s", camera_id, target_fps, width, height, backends)
    for backend in backends:
        logger.info("CAMERA_OPEN_TRY id=%s backend=%s", camera_id, backend)
        cap = cv2.VideoCapture(camera_id, backend)
        if not cap.isOpened():
            logger.warning("CAMERA_OPEN_FAIL id=%s backend=%s isOpened=false", camera_id, backend)
            cap.release()
            continue
        # Preserve the current native-format policy: these PS3 Eye/ov534 drivers
        # may ignore MJPG requests and expose YUYV/Bayer instead.
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, target_fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        logger.info("CAMERA_OPEN_OK id=%s backend=%s actual=%sx%s fps=%s fourcc=%s", camera_id, backend, cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT), cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FOURCC))
        return cap
    logger.error("CAMERA_OPEN_FAILED_ALL id=%s", camera_id)
    raise RuntimeError(tr('Could not open camera.'))


def discover_cameras(target_fps=DEFAULT_CAMERA_TARGET_FPS, requested_width=DEFAULT_CAMERA_WIDTH, requested_height=DEFAULT_CAMERA_HEIGHT, scan_limit=8):
    """Enumerate camera devices and report their current capture capabilities."""
    if sys.platform.startswith("linux"):
        ipu3_cameras = _surface_ipu3_camera_names()
        if ipu3_cameras:
            return [
                {"id": index, "label": f"Internal camera {index + 1} — Surface Go 2 (libcamera/IPU3)"}
                for index in range(len(ipu3_cameras))
            ]
        cameras = []

        for path in sorted(
            Path("/dev").glob("video*"),
            key=lambda item: item.name,
        ):
            suffix = path.name[5:]
            if not suffix.isdigit():
                continue

            camera_id = int(suffix)
            device_name = tr('Video device')

            name_path = Path(
                f"/sys/class/video4linux/{path.name}/name"
            )
            try:
                if name_path.exists():
                    value = name_path.read_text(
                        encoding="utf-8",
                        errors="ignore",
                    ).strip()
                    if value:
                        device_name = value
            except OSError:
                pass

            # Try to read live capture capabilities when the device is
            # available. If the backend is temporarily busy, keep the device
            # in the list anyway so Settings never becomes empty just because
            # another process has the camera open.
            width = requested_width
            height = requested_height
            fps = 0.0
            cap = None

            try:
                for backend in camera_backends():
                    candidate = cv2.VideoCapture(camera_id, backend)
                    if candidate.isOpened():
                        cap = candidate
                        break
                    candidate.release()

                if cap is not None:
                    if target_fps >= 60:
                        try:
                            cap.set(
                                cv2.CAP_PROP_FOURCC,
                                cv2.VideoWriter_fourcc(*"MJPG"),
                            )
                        except Exception:
                            pass

                    cap.set(
                        cv2.CAP_PROP_FRAME_WIDTH,
                        requested_width,
                    )
                    cap.set(
                        cv2.CAP_PROP_FRAME_HEIGHT,
                        requested_height,
                    )
                    cap.set(cv2.CAP_PROP_FPS, target_fps)

                    width = int(
                        cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                    ) or width
                    height = int(
                        cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                    ) or height
                    fps = float(
                        cap.get(cv2.CAP_PROP_FPS) or 0.0
                    )
            except Exception:
                pass
            finally:
                if cap is not None:
                    cap.release()

            fps_label = (
                f"{fps:.0f} FPS"
                if fps > 0
                else tr('FPS auto')
            )

            cameras.append({
                "id": camera_id,
                "label": (
                    tr('Camera {camera} — {device} — {width}x{height} — {fps}').format(camera=camera_id, device=device_name, width=width, height=height, fps=fps_label)
                ),
            })

        return cameras

    cameras = []

    for camera_id in range(scan_limit):
        cap = None
        width = requested_width
        height = requested_height

        try:
            for backend in camera_backends():
                candidate = cv2.VideoCapture(camera_id, backend)
                if candidate.isOpened():
                    cap = candidate
                    break
                candidate.release()

            if cap is None:
                continue

            if target_fps >= 60:
                try:
                    cap.set(
                        cv2.CAP_PROP_FOURCC,
                        cv2.VideoWriter_fourcc(*"MJPG"),
                    )
                except Exception:
                    pass

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, requested_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, requested_height)
            cap.set(cv2.CAP_PROP_FPS, target_fps)

            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or width
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or height
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)

            cameras.append({
                "id": camera_id,
                "label": (
                    tr('Camera {camera} — {width}x{height} — {fps:.0f} FPS').format(camera=camera_id, width=width, height=height, fps=fps)
                ),
            })
        except Exception:
            continue
        finally:
            if cap is not None:
                cap.release()

    return cameras

