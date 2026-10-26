"""Low-level camera opening, independent of the Qt app and database."""
from __future__ import annotations

try:
    from ..localization import text as tr
except ImportError:
    from localization import text as tr
import logging
import sys
from pathlib import Path
import cv2

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

def open_camera(camera_id, target_fps=DEFAULT_CAMERA_TARGET_FPS, width=DEFAULT_CAMERA_WIDTH, height=DEFAULT_CAMERA_HEIGHT):
    """Open a camera consistently; callers must inspect actual driver settings."""
    camera_id, target_fps, width, height = int(camera_id), int(target_fps), int(width), int(height)
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

