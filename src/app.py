import csv
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import cv2
import dlib
import face_recognition_models
import numpy as np
from PySide6.QtCore import QDate, QLibraryInfo, QThread, QTranslator, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QDateEdit,
    QInputDialog,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from database import (
        add_academic_year,
        add_class,
        add_embedding,
        add_major,
        add_student,
        assign_student_class,
        delete_class,
        delete_embeddings,
        delete_major,
        delete_student,
        find_class_by_name,
        find_major_by_name,
        find_student_by_code,
        get_active_academic_year,
        get_attendance,
        get_attendance_stats,
        get_class_report_summary,
        get_daily_report_summary,
        get_recent_attendance,
        get_report_rows,
        get_school_weekdays,
        list_school_day_overrides,
        set_school_day_override,
        set_school_weekdays,
        delete_school_day_override,
        get_setting,
        set_setting,
        get_student,
        get_student_class,
        init_db,
        list_academic_years,
        list_classes,
        list_majors,
        list_students,
        mark_attendance,
        get_current_presence_ids,
        set_active_academic_year,
        set_attendance_status,
        update_academic_year,
        update_class,
        update_major,
        update_student,
        load_students,
    )
except ModuleNotFoundError:
    from .database import (
        add_academic_year,
        add_class,
        add_embedding,
        add_major,
        add_student,
        assign_student_class,
        delete_class,
        delete_embeddings,
        delete_major,
        delete_student,
        find_class_by_name,
        find_major_by_name,
        find_student_by_code,
        get_active_academic_year,
        get_attendance,
        get_attendance_stats,
        get_class_report_summary,
        get_daily_report_summary,
        get_recent_attendance,
        get_report_rows,
        get_school_weekdays,
        list_school_day_overrides,
        set_school_day_override,
        set_school_weekdays,
        delete_school_day_override,
        get_setting,
        set_setting,
        get_student,
        get_student_class,
        init_db,
        list_academic_years,
        list_classes,
        list_majors,
        list_students,
        mark_attendance,
        get_current_presence_ids,
        set_active_academic_year,
        set_attendance_status,
        update_class,
        update_major,
        update_student,
        load_students,
    )

try:
    from auth import (
        change_admin_password,
        password_is_configured,
        set_admin_password,
        verify_admin_password,
    )
except ModuleNotFoundError:
    from .auth import (
        change_admin_password,
        password_is_configured,
        set_admin_password,
        verify_admin_password,
    )

try:
    from .smart_camera_worker import SmartCameraWorker
    from .smart_events import AttendancePresenceState
    from .global_identity import GlobalIdentityManager
except ImportError:
    from smart_camera_worker import SmartCameraWorker
    from smart_events import AttendancePresenceState
    from global_identity import GlobalIdentityManager

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

YUNET_MODEL_FILENAME = "face_detection_yunet_2023mar.onnx"
YUNET_MODEL_PATH = BASE_DIR / YUNET_MODEL_FILENAME

CAMERA_ID = 0
CAMERA_NAME = "camera_0"
DEFAULT_CAMERA_TARGET_FPS = 60
CAMERA_SCAN_LIMIT = 8
MAX_RECOGNITION_JOBS = 2

YUNET_SCORE_THRESHOLD = 0.52
YUNET_NMS_THRESHOLD = 0.30
YUNET_TOP_K = 5000

# Capture more pixels so faces remain usable at longer distances.
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720

# 768 gives the face detector more detail for small/distant faces while
# keeping the detector substantially cheaper than very large inference sizes.
IMAGE_SIZE = 768

# Recognition is intentionally conservative: a single unusually-close
# embedding must not be enough to identify a person.
FACE_DISTANCE_THRESHOLD = 0.50
FACE_STABILITY_THRESHOLD = 0.52

RECOGNITION_INTERVAL = 5
RECOGNITION_CONFIRMATIONS = 3
REVALIDATION_INTERVAL = 60
REVALIDATION_MIN_INTERVAL = 10
REVALIDATION_IOU_THRESHOLD = 0.72

# Do not discard usable distant faces too early.
MIN_FACE_SIZE = 28

# Runtime tracking/UI tuning.
TRACK_IOU_THRESHOLD = 0.20
TRACK_MAX_MISSES = 3

# Enrollment should cover natural variation rather than five near-identical frames.
ENROLLMENT_SAMPLES = 12
ENROLLMENT_SAMPLE_INTERVAL = 1.0
ENROLLMENT_MIN_FACE_SIZE = 80

# Face crops are padded before encoding so important facial context is retained.
FACE_CROP_PADDING = 0.20
SMALL_FACE_THRESHOLD = 160
SMALL_FACE_UPSCALE = 2.5

UI_FRAME_LIMIT = 1.0 / 20.0


def _configure_cpu_runtime():
    """Bound CPU parallelism when multiple camera pipelines share one host."""
    try:
        import torch
    except ImportError:
        return

    device = str(get_setting("person_device", "") or "").strip().lower()
    if device.startswith("cuda"):
        return

    cpu_count = os.cpu_count() or 1
    dual_camera = all(
        str(
            get_setting(
                f"camera_{slot}_enabled",
                "1" if slot == 1 else "0",
            )
            or "0"
        ).strip().lower()
        in {"1", "true", "yes", "on"}
        for slot in (1, 2)
    )

    # The CPU is shared by two independent inference streams. Keeping one
    # inter-op worker and a bounded intra-op pool prevents thread explosion.
    divisor = 4 if dual_camera else 2
    intra_threads = max(1, min(8, cpu_count // divisor))

    try:
        torch.set_num_threads(intra_threads)
    except RuntimeError:
        pass

    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass

    # OpenCV can otherwise create another large native thread pool for
    # preprocessing/color conversion on every camera worker.
    try:
        cv2.setNumThreads(max(1, min(4, cpu_count // divisor)))
    except Exception:
        pass


def _camera_backends():
    if sys.platform.startswith("win"):
        return [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    if sys.platform.startswith("linux"):
        return [cv2.CAP_V4L2, cv2.CAP_ANY]
    return [cv2.CAP_ANY]


def _open_camera(camera_id, target_fps=DEFAULT_CAMERA_TARGET_FPS):
    for backend in _camera_backends():
        cap = cv2.VideoCapture(camera_id, backend)
        if not cap.isOpened():
            cap.release()
            continue

        # The connected ov534 cameras expose raw YUYV/Bayer at 640x480;
        # requesting MJPEG is unsupported and silently falls back. Avoid the
        # failed format negotiation and leave the driver on its native format.
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, target_fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    raise RuntimeError("Could not open camera.")


def _discover_cameras():
    target_fps = int(
        get_setting(
            "camera_target_fps",
            DEFAULT_CAMERA_TARGET_FPS,
        )
    )

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
            device_name = "Video device"

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
            width = CAMERA_WIDTH
            height = CAMERA_HEIGHT
            fps = 0.0
            cap = None

            try:
                for backend in _camera_backends():
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
                        CAMERA_WIDTH,
                    )
                    cap.set(
                        cv2.CAP_PROP_FRAME_HEIGHT,
                        CAMERA_HEIGHT,
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
                else "FPS auto"
            )

            cameras.append({
                "id": camera_id,
                "label": (
                    f"Camera {camera_id} — {device_name} — "
                    f"{width}x{height} — {fps_label}"
                ),
            })

        return cameras

    cameras = []

    for camera_id in range(CAMERA_SCAN_LIMIT):
        cap = None

        try:
            for backend in _camera_backends():
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

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
            cap.set(cv2.CAP_PROP_FPS, target_fps)

            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or CAMERA_WIDTH
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or CAMERA_HEIGHT
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)

            cameras.append({
                "id": camera_id,
                "label": (
                    f"Camera {camera_id} — "
                    f"{width}x{height} — {fps:.0f} FPS"
                ),
            })
        except Exception:
            continue
        finally:
            if cap is not None:
                cap.release()

    return cameras


_FACE_MODELS_LOCAL = threading.local()


def _get_face_models():
    models = getattr(_FACE_MODELS_LOCAL, "models", None)

    if models is None:
        shape_model = Path(
            face_recognition_models.pose_predictor_five_point_model_location()
        )
        encoding_model = Path(
            face_recognition_models.face_recognition_model_location()
        )

        if not shape_model.exists():
            raise FileNotFoundError(f"5-point model not found: {shape_model}")
        if not encoding_model.exists():
            raise FileNotFoundError(
                f"Face encoding model not found: {encoding_model}"
            )

        models = (
            dlib.shape_predictor(str(shape_model)),
            dlib.face_recognition_model_v1(str(encoding_model)),
        )
        _FACE_MODELS_LOCAL.models = models

    return models


def frame_to_qimage(frame):
    """Wrap a BGR OpenCV frame without an extra color-conversion pass."""
    h, w = frame.shape[:2]
    image = QImage(
        frame.data,
        w,
        h,
        int(frame.strides[0]),
        QImage.Format_BGR888,
    )
    return image.copy()


def _prepare_face_crop(frame, bbox):
    x1, y1, x2, y2 = [int(value) for value in bbox]

    frame_h, frame_w = frame.shape[:2]
    face_w = x2 - x1
    face_h = y2 - y1

    if face_w <= 0 or face_h <= 0:
        return None

    pad_x = int(face_w * FACE_CROP_PADDING)
    pad_y = int(face_h * FACE_CROP_PADDING)

    crop_x1 = max(0, x1 - pad_x)
    crop_y1 = max(0, y1 - pad_y)
    crop_x2 = min(frame_w, x2 + pad_x)
    crop_y2 = min(frame_h, y2 + pad_y)

    crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]

    if crop.size == 0:
        return None

    crop = np.ascontiguousarray(
        crop,
        dtype=np.uint8,
    )

    scale = 1.0

    if max(face_w, face_h) < SMALL_FACE_THRESHOLD:
        crop = cv2.resize(
            crop,
            None,
            fx=SMALL_FACE_UPSCALE,
            fy=SMALL_FACE_UPSCALE,
            interpolation=cv2.INTER_CUBIC,
        )
        scale = SMALL_FACE_UPSCALE

    local_bbox = (
        max(0, int((x1 - crop_x1) * scale)),
        max(0, int((y1 - crop_y1) * scale)),
        min(
            crop.shape[1] - 1,
            int((x2 - crop_x1) * scale),
        ),
        min(
            crop.shape[0] - 1,
            int((y2 - crop_y1) * scale),
        ),
    )

    if (
        local_bbox[2] <= local_bbox[0]
        or local_bbox[3] <= local_bbox[1]
    ):
        return None

    return crop, local_bbox


def _encode_face_crop(crop, bbox):
    shape_predictor, face_encoder = _get_face_models()

    x1, y1, x2, y2 = [int(value) for value in bbox]

    rect = dlib.rectangle(
        left=x1,
        top=y1,
        right=x2,
        bottom=y2,
    )

    shape = shape_predictor(
        crop,
        rect,
    )

    descriptor = face_encoder.compute_face_descriptor(
        crop,
        shape,
        0,
    )

    return np.asarray(
        descriptor,
        dtype=np.float32,
    )


def face_embedding(frame, bbox):
    prepared = _prepare_face_crop(
        frame,
        bbox,
    )

    if prepared is None:
        return None

    crop, local_bbox = prepared

    return _encode_face_crop(
        crop,
        local_bbox,
    )


def get_camera_settings():
    camera_id = int(get_setting("camera_id", CAMERA_ID))
    target_fps = int(
        get_setting(
            "camera_target_fps",
            DEFAULT_CAMERA_TARGET_FPS,
        )
    )
    return camera_id, target_fps


def draw_text(frame, text, origin, scale=0.65, color=(255, 255, 255), thickness=2):
    cv2.putText(
        frame,
        text,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )




class FaceMatcher:
    def __init__(self):
        self.students = []
        self.embedding_matrix = np.empty((0, 128), dtype=np.float32)
        self.embedding_norms = np.empty((0,), dtype=np.float32)
        self.student_slices = []
        self.refresh()

    def refresh(self):
        self.students = load_students()

        vectors = []
        owners = []

        for student_index, student in enumerate(self.students):
            for embedding in student["embeddings"]:
                vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
                if vector.shape == (128,):
                    vectors.append(vector)
                    owners.append(student_index)

        if not vectors:
            self.embedding_matrix = np.empty((0, 128), dtype=np.float32)
            self.embedding_norms = np.empty((0,), dtype=np.float32)
            self.student_slices = []
            return

        self.embedding_matrix = np.ascontiguousarray(
            np.vstack(vectors),
            dtype=np.float32,
        )
        self.embedding_norms = np.einsum(
            "ij,ij->i",
            self.embedding_matrix,
            self.embedding_matrix,
        )

        owners = np.asarray(owners, dtype=np.int32)
        self.student_slices = []
        start = 0

        for student_index in range(len(self.students)):
            count = int(np.count_nonzero(owners == student_index))
            if count:
                self.student_slices.append(
                    (student_index, start, start + count)
                )
                start += count

    def match_embedding(self, embedding):
        if self.embedding_matrix.size == 0:
            return None, None, None

        embedding_norm = float(
            np.dot(
                embedding,
                embedding,
            )
        )

        squared = (
            self.embedding_norms
            + embedding_norm
            - 2.0 * (
                self.embedding_matrix @ embedding
            )
        )

        np.maximum(
            squared,
            0.0,
            out=squared,
        )

        candidates = []

        for student_index, start, end in self.student_slices:
            student_sq = squared[start:end]

            if student_sq.size == 0:
                continue

            best_squared = float(
                np.min(student_sq)
            )

            count = min(
                3,
                student_sq.size,
            )

            if count == student_sq.size:
                top3_squared = student_sq
            else:
                top3_squared = np.partition(
                    student_sq,
                    count - 1,
                )[:count]

            best_distance = float(
                np.sqrt(best_squared)
            )

            stability_score = float(
                np.mean(
                    np.sqrt(top3_squared)
                )
            )

            candidates.append(
                (
                    student_index,
                    best_distance,
                    stability_score,
                )
            )

        if not candidates:
            return None, None, None

        candidates.sort(
            key=lambda item: (
                item[1],
                item[2],
            )
        )

        best_index, best_distance, best_stability = candidates[0]

        if (
            best_distance > FACE_DISTANCE_THRESHOLD
            or best_stability > FACE_STABILITY_THRESHOLD
        ):
            return None, best_distance, best_stability

        if len(candidates) > 1:
            _, second_distance, second_stability = candidates[1]

            if (
                second_distance - best_distance < 0.05
                or second_stability - best_stability < 0.04
            ):
                return None, best_distance, best_stability

        return (
            best_index,
            best_distance,
            best_stability,
        )

    def recognize_crop(self, crop, bbox):
        embedding = _encode_face_crop(
            crop,
            bbox,
        )

        return self.match_embedding(
            embedding
        )

    def recognize_crop_with_embedding(self, crop, bbox):
        """Return both the face embedding and its matcher result.

        The smart-camera backlog uses the embedding only in RAM to reject a
        mixed track before voting across its best face samples.
        """
        embedding = _encode_face_crop(crop, bbox)
        return embedding, self.match_embedding(embedding)

    def recognize(self, frame, bbox):
        embedding = face_embedding(
            frame,
            bbox,
        )

        if embedding is None:
            return None, None, None

        return self.match_embedding(
            embedding
        )

class CameraWorker(QThread):
    frame_ready = Signal(QImage)
    attendance_added = Signal(object)
    status_changed = Signal(str)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False

    def stop(self):
        self._running = False

    @staticmethod
    def _iou(a, b):
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        intersection = iw * ih
        if intersection <= 0:
            return 0.0
        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
        union = area_a + area_b - intersection
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _predict_bbox(track, frame_shape):
        x1, y1, x2, y2 = track["bbox"]
        dx, dy, dw, dh = track.get(
            "velocity",
            (0.0, 0.0, 0.0, 0.0),
        )
        width = max(1, int((x2 - x1) + dw))
        height = max(1, int((y2 - y1) + dh))
        frame_h, frame_w = frame_shape[:2]
        nx1 = max(0, min(int(x1 + dx), frame_w - 1))
        ny1 = max(0, min(int(y1 + dy), frame_h - 1))
        nx2 = min(frame_w, nx1 + width)
        ny2 = min(frame_h, ny1 + height)
        track["bbox"] = (nx1, ny1, nx2, ny2)

    def _assign_tracks(self, detections, tracks, next_track_id):
        unmatched = set(range(len(detections)))
        assignments = {}
        candidates = []

        for track_id, track in tracks.items():
            for index, bbox in enumerate(detections):
                score = self._iou(track["bbox"], bbox)
                if score >= TRACK_IOU_THRESHOLD:
                    candidates.append((score, track_id, index))

        candidates.sort(reverse=True)
        used_tracks = set()

        for score, track_id, index in candidates:
            if track_id in used_tracks or index not in unmatched:
                continue
            assignments[index] = track_id
            used_tracks.add(track_id)
            unmatched.remove(index)

        for index in unmatched:
            assignments[index] = next_track_id
            next_track_id += 1

        return assignments, next_track_id

    @staticmethod
    def _recognize_task(matcher, crop, bbox, track_id):
        return track_id, matcher.recognize_crop(crop, bbox)

    def run(self):
        self._running = True
        self.status_changed.emit("Loading face detector...")
        cap = None
        executor = None
        futures = set()

        try:
            if not YUNET_MODEL_PATH.exists():
                raise FileNotFoundError(
                    f"YuNet model not found: {YUNET_MODEL_PATH}"
                )

            detector = cv2.FaceDetectorYN.create(
                str(YUNET_MODEL_PATH),
                "",
                (CAMERA_WIDTH, CAMERA_HEIGHT),
                YUNET_SCORE_THRESHOLD,
                YUNET_NMS_THRESHOLD,
                YUNET_TOP_K,
            )

            camera_id, target_fps = get_camera_settings()
            cap = _open_camera(camera_id, target_fps)

            actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            detector.setInputSize((actual_width, actual_height))

            matcher = FaceMatcher()

            cpu_count = os.cpu_count() or 1
            worker_count = (
                min(
                    MAX_RECOGNITION_JOBS,
                    cpu_count,
                )
                if cpu_count >= 2
                else 1
            )

            if worker_count >= 2:
                executor = ThreadPoolExecutor(
                    max_workers=worker_count,
                    thread_name_prefix="face-recognition",
                )

            tracks = {}
            next_track_id = 1
            frame_count = 0
            frames_since_detection = 0
            fps = 0.0
            fps_counter = 0
            fps_start = time.perf_counter()
            last_preview = 0.0
            preview_interval = 1.0 / 20.0
            detection_interval = 1
            last_detections = []

            self.status_changed.emit(
                f"Camera online • {actual_width}x{actual_height} • "
                f"{actual_fps:.0f} FPS • YuNet • "
                f"{len(matcher.students)} students • "
                f"recognition workers: {worker_count}"
            )

            while self._running:
                ret, frame = cap.read()
                if not ret:
                    self.error.emit("Camera frame failed.")
                    break

                frame_count += 1
                frames_since_detection += 1

                for future in tuple(futures):
                    if not future.done():
                        continue

                    futures.remove(future)
                    try:
                        track_id, result = future.result()
                    except Exception as exc:
                        self.error.emit(
                            f"Recognition worker failed: {exc}"
                        )
                        continue

                    track = tracks.get(track_id)
                    if track is None:
                        continue

                    student_index, distance, stability = result
                    track["recognition_pending"] = False
                    track["last_recognition_frame"] = frame_count

                    if student_index is None:
                        # A weak first frame must not permanently lock the track
                        # as Unknown. Keep trying at the recognition interval.
                        track["recognition_done"] = False
                        track["candidate_index"] = None
                        track["candidate_hits"] = 0

                        if track["student_index"] is None:
                            track["distance"] = distance
                            track["stability"] = stability
                    else:
                        track["recognition_done"] = True
                        track["student_index"] = student_index
                        track["distance"] = distance
                        track["stability"] = stability
                        track["candidate_index"] = student_index
                        track["candidate_hits"] = 1
                        track["last_validation_frame"] = frame_count
                        track["validated_bbox"] = track["bbox"]

                if frames_since_detection >= detection_interval:
                    frames_since_detection = 0

                    _, detections = detector.detect(frame)
                    last_detections = []

                    if detections is not None:
                        for detection in detections:
                            x, y, w, h = detection[:4]
                            bbox = (
                                max(0, int(x)),
                                max(0, int(y)),
                                min(frame.shape[1], int(x + w)),
                                min(frame.shape[0], int(y + h)),
                            )
                            if (
                                bbox[2] - bbox[0] >= MIN_FACE_SIZE
                                and bbox[3] - bbox[1] >= MIN_FACE_SIZE
                            ):
                                last_detections.append(bbox)

                    assignments, next_track_id = self._assign_tracks(
                        last_detections,
                        tracks,
                        next_track_id,
                    )

                    new_tracks = {}

                    for index, bbox in enumerate(last_detections):
                        track_id = assignments[index]
                        state = tracks.get(
                            track_id,
                            {
                                "bbox": bbox,
                                "velocity": (
                                    0.0,
                                    0.0,
                                    0.0,
                                    0.0,
                                ),
                                "student_index": None,
                                "distance": None,
                                "stability": None,
                                "candidate_index": None,
                                "candidate_hits": 0,
                                "last_recognition_frame": -RECOGNITION_INTERVAL,
                                "last_validation_frame": -REVALIDATION_INTERVAL,
                                "validated_bbox": None,
                                "recognition_pending": False,
                                "recognition_done": False,
                                "attendance_marked": False,
                                "misses": 0,
                            },
                        )

                        old_bbox = state["bbox"]
                        old_w = old_bbox[2] - old_bbox[0]
                        old_h = old_bbox[3] - old_bbox[1]
                        new_w = bbox[2] - bbox[0]
                        new_h = bbox[3] - bbox[1]

                        if state["misses"] == 0:
                            measured = (
                                bbox[0] - old_bbox[0],
                                bbox[1] - old_bbox[1],
                                new_w - old_w,
                                new_h - old_h,
                            )
                            previous = state.get(
                                "velocity",
                                (0.0, 0.0, 0.0, 0.0),
                            )
                            state["velocity"] = tuple(
                                0.5 * old_v + 0.5 * new_v
                                for old_v, new_v in zip(
                                    previous,
                                    measured,
                                )
                            )

                        state["bbox"] = bbox
                        state["misses"] = 0
                        new_tracks[track_id] = state

                    for track_id, state in tracks.items():
                        if track_id not in new_tracks:
                            state["misses"] += 1
                            if state["misses"] <= TRACK_MAX_MISSES:
                                new_tracks[track_id] = state

                    tracks = new_tracks
                else:
                    for state in tracks.values():
                        if state["misses"] == 0:
                            self._predict_bbox(
                                state,
                                frame.shape,
                            )

                for track_id, track in list(tracks.items()):
                    if track["misses"] > 0:
                        continue

                    x1, y1, x2, y2 = track["bbox"]
                    if (
                        x2 - x1 < MIN_FACE_SIZE
                        or y2 - y1 < MIN_FACE_SIZE
                    ):
                        continue

                    # Recognition is scheduled by track state:
                    # - Unknown tracks retry every RECOGNITION_INTERVAL frames.
                    # - Known tracks revalidate only after enough time or movement.
                    should_recognize = track["student_index"] is None

                    if not should_recognize:
                        validated_bbox = track.get("validated_bbox")
                        moved_since_validation = (
                            validated_bbox is not None
                            and self._iou(
                                validated_bbox,
                                track["bbox"],
                            ) < REVALIDATION_IOU_THRESHOLD
                        )
                        should_recognize = (
                            frame_count - track["last_validation_frame"]
                            >= REVALIDATION_INTERVAL
                            or (
                                moved_since_validation
                                and frame_count
                                - track["last_validation_frame"]
                                >= REVALIDATION_MIN_INTERVAL
                            )
                        )

                    can_submit_recognition = (
                        frame_count - track["last_recognition_frame"]
                        >= RECOGNITION_INTERVAL
                    )

                    if (
                        should_recognize
                        and can_submit_recognition
                        and not track["recognition_pending"]
                        and matcher.embedding_matrix.size != 0
                        and (
                            executor is None
                            or len(futures) < MAX_RECOGNITION_JOBS
                        )
                    ):
                        prepared = _prepare_face_crop(
                            frame,
                            (x1, y1, x2, y2),
                        )

                        if prepared is not None:
                            face_crop, local_bbox = prepared
                            track["recognition_pending"] = True

                            if executor is None:
                                student_index, distance, stability = (
                                    matcher.recognize_crop(
                                        face_crop,
                                        local_bbox,
                                    )
                                )

                                track["recognition_pending"] = False
                                track["last_recognition_frame"] = frame_count

                                if student_index is None:
                                    track["recognition_done"] = False
                                    track["candidate_index"] = None
                                    track["candidate_hits"] = 0
                                    if track["student_index"] is None:
                                        track["distance"] = distance
                                        track["stability"] = stability
                                else:
                                    track["recognition_done"] = True
                                    track["student_index"] = student_index
                                    track["distance"] = distance
                                    track["stability"] = stability
                                    track["candidate_index"] = student_index
                                    track["candidate_hits"] = 1
                                    track["last_validation_frame"] = frame_count
                                    track["validated_bbox"] = track["bbox"]
                            else:
                                futures.add(
                                    executor.submit(
                                        self._recognize_task,
                                        matcher,
                                        face_crop,
                                        local_bbox,
                                        track_id,
                                    )
                                )

                    if (
                        track["recognition_done"]
                        and track["student_index"] is not None
                    ):
                        if track["candidate_hits"] < RECOGNITION_CONFIRMATIONS:
                            track["candidate_hits"] += 1

                        if (
                            track["candidate_hits"]
                            >= RECOGNITION_CONFIRMATIONS
                            and not track["attendance_marked"]
                        ):
                            track["attendance_marked"] = True
                            student = matcher.students[
                                track["student_index"]
                            ]

                            inserted = mark_attendance(
                                student["id"],
                                f"camera_{camera_id}",
                            )

                            if inserted:
                                self.attendance_added.emit({
                                    "student_id": student["id"],
                                    "student_code": student["student_code"],
                                    "name": student["name"],
                                    "time": datetime.now().strftime("%H:%M:%S"),
                                    "camera": f"camera_{camera_id}",
                                })

                    if track["recognition_pending"]:
                        label = "Scanning..."
                        box_color = (255, 180, 0)
                    elif track["student_index"] is not None:
                        student = matcher.students[track["student_index"]]
                        label = (
                            f"{student['name']} "
                            f"[{track['distance']:.2f}/"
                            f"{track['stability']:.2f}]"
                        )
                        box_color = (0, 220, 80)
                    else:
                        label = "Unknown"
                        box_color = (0, 140, 255)

                    cv2.rectangle(
                        frame,
                        (x1, y1),
                        (x2, y2),
                        box_color,
                        2,
                    )
                    draw_text(
                        frame,
                        label,
                        (x1, max(25, y1 - 10)),
                        0.58,
                        box_color,
                        2,
                    )
                    draw_text(
                        frame,
                        f"ID: {track_id}",
                        (x1, min(frame.shape[0] - 8, y2 + 20)),
                        0.48,
                        box_color,
                        1,
                    )

                tracks = {
                    track_id: state
                    for track_id, state in tracks.items()
                    if state["misses"] <= TRACK_MAX_MISSES
                }

                if not last_detections:
                    detection_interval = 3
                elif len(last_detections) <= 2:
                    detection_interval = 2
                else:
                    detection_interval = 1

                fps_counter += 1
                elapsed = time.perf_counter() - fps_start
                if elapsed >= 1.0:
                    fps = fps_counter / elapsed
                    fps_counter = 0
                    fps_start = time.perf_counter()

                draw_text(frame, f"FPS: {fps:.1f}", (15, 28), 0.68)
                draw_text(
                    frame,
                    f"CAM: {actual_fps:.0f}",
                    (15, 54),
                    0.52,
                )

                now = time.perf_counter()
                if now - last_preview >= preview_interval:
                    self.frame_ready.emit(frame_to_qimage(frame))
                    last_preview = now

        except Exception as exc:
            self.error.emit(str(exc))
            self.status_changed.emit("Camera stopped")
        finally:
            if executor is not None:
                executor.shutdown(
                    wait=True,
                    cancel_futures=True,
                )

            if cap is not None:
                cap.release()

            self._running = False
class EnrollmentWorker(QThread):
    frame_ready = Signal(QImage)
    progress = Signal(int, int)
    capture_done = Signal(object)
    error = Signal(str)

    def __init__(self, camera_id=None, target_fps=None, parent=None):
        super().__init__(parent)
        self._running = False
        self.camera_id = camera_id
        self.target_fps = target_fps

    def stop(self):
        self._running = False

    def run(self):
        self._running = True
        cap = None

        try:
            if not YUNET_MODEL_PATH.exists():
                raise FileNotFoundError(
                    f"مدل YuNet پیدا نشد: {YUNET_MODEL_PATH}"
                )

            detector = cv2.FaceDetectorYN.create(
                str(YUNET_MODEL_PATH),
                "",
                (CAMERA_WIDTH, CAMERA_HEIGHT),
                YUNET_SCORE_THRESHOLD,
                YUNET_NMS_THRESHOLD,
                YUNET_TOP_K,
            )

            camera_id, target_fps = get_camera_settings()
            if self.camera_id is not None:
                camera_id = int(self.camera_id)
            if self.target_fps is not None:
                target_fps = int(self.target_fps)
            cap = _open_camera(camera_id, target_fps)

            _get_face_models()

            actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            detector.setInputSize((actual_width, actual_height))

            embeddings = []
            last_sample = 0.0

            while self._running and len(embeddings) < ENROLLMENT_SAMPLES:
                ret, frame = cap.read()
                if not ret:
                    raise RuntimeError("دریافت تصویر از دوربین ناموفق بود.")

                _, detections = detector.detect(frame)
                faces = []

                if detections is not None:
                    for detection in detections:
                        x, y, w, h = detection[:4]
                        bbox = (
                            max(0, int(x)),
                            max(0, int(y)),
                            min(frame.shape[1], int(x + w)),
                            min(frame.shape[0], int(y + h)),
                        )
                        if (
                            bbox[2] - bbox[0] >= ENROLLMENT_MIN_FACE_SIZE
                            and bbox[3] - bbox[1] >= ENROLLMENT_MIN_FACE_SIZE
                        ):
                            faces.append(bbox)

                if len(faces) == 1:
                    bbox = faces[0]
                    now = time.time()
                    if now - last_sample >= ENROLLMENT_SAMPLE_INTERVAL:
                        embedding = face_embedding(frame, bbox)
                        if embedding is not None:
                            embeddings.append(embedding)
                            last_sample = now
                            self.progress.emit(
                                len(embeddings),
                                ENROLLMENT_SAMPLES,
                            )

                    x1, y1, x2, y2 = bbox
                    cv2.rectangle(
                        frame,
                        (x1, y1),
                        (x2, y2),
                        (0, 220, 80),
                        2,
                    )
                elif len(faces) == 0:
                    draw_text(
                        frame,
                        "No face detected",
                        (20, 40),
                        0.72,
                        (0, 80, 255),
                        2,
                    )
                else:
                    draw_text(
                        frame,
                        "Only one person should be in front of the camera",
                        (20, 40),
                        0.62,
                        (0, 80, 255),
                        2,
                    )

                draw_text(
                    frame,
                    f"Samples: {len(embeddings)}/{ENROLLMENT_SAMPLES}",
                    (20, frame.shape[0] - 20),
                    0.68,
                )
                self.frame_ready.emit(frame_to_qimage(frame))

            self.capture_done.emit(
                embeddings if len(embeddings) == ENROLLMENT_SAMPLES else None
            )

        except Exception as exc:
            self.error.emit(str(exc))
            self.capture_done.emit(None)
        finally:
            if cap is not None:
                cap.release()
            self._running = False

class EnrollmentDialog(QDialog):
    def __init__(self, show_student_fields=True, student_id=None, parent=None):
        super().__init__(parent)

        self.show_student_fields = show_student_fields
        self.student_id = student_id
        self.embeddings = None
        self.worker = None

        self.setModal(True)
        self.setWindowTitle(
            "افزودن دانش‌آموز" if show_student_fields else "ثبت دوباره چهره"
        )
        self.resize(960, 720)

        root = QVBoxLayout(self)

        if show_student_fields:
            form = QFormLayout()

            self.code_edit = QLineEdit()
            self.name_edit = QLineEdit()

            self.code_edit.setPlaceholderText("کد دانش‌آموزی")
            self.name_edit.setPlaceholderText("نام و نام خانوادگی")

            self.class_combo = QComboBox()
            self.class_combo.addItem("بدون کلاس", None)

            active = get_active_academic_year()
            if active:
                for (
                    class_id,
                    class_name,
                    grade,
                    major_name,
                    year_id,
                    year_name,
                    student_count,
                ) in list_classes(active[0]):
                    label = f"{class_name} — پایه {grade}"
                    if major_name:
                        label += f" — {major_name}"
                    self.class_combo.addItem(label, class_id)

            form.addRow("کد دانش‌آموزی", self.code_edit)
            form.addRow("نام", self.name_edit)
            form.addRow("کلاس", self.class_combo)
            root.addLayout(form)

        camera_row = QHBoxLayout()
        camera_label = QLabel("دوربین ثبت چهره")
        camera_label.setObjectName("fieldLabel")
        self.camera_combo = QComboBox()
        self.refresh_camera_button = QPushButton("بازخوانی")
        self.refresh_camera_button.clicked.connect(self.refresh_enrollment_cameras)
        camera_row.addWidget(camera_label)
        camera_row.addWidget(self.camera_combo, 1)
        camera_row.addWidget(self.refresh_camera_button)
        root.addLayout(camera_row)

        self.enrollment_camera_status = QLabel()
        self.enrollment_camera_status.setObjectName("muted")
        self.enrollment_camera_status.setWordWrap(True)
        root.addWidget(self.enrollment_camera_status)

        self.refresh_enrollment_cameras()

        if not show_student_fields:
            title = QLabel(
                "۱۲ نمونه جدید ثبت می‌شود. بین نمونه‌ها کمی حرکت طبیعی داشته باشید."
            )
            title.setObjectName("sectionTitle")
            root.addWidget(title)

        self.preview = QLabel("پیش‌نمایش دوربین")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(860, 500)
        self.preview.setObjectName("cameraPreview")
        root.addWidget(self.preview, 1)

        self.progress_label = QLabel(
            "آماده ثبت ۱۲ نمونه."
        )
        self.progress_label.setObjectName("muted")
        root.addWidget(self.progress_label)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("شروع ثبت")
        self.cancel_button = QPushButton("لغو")

        self.start_button.clicked.connect(self.start_capture)
        self.cancel_button.clicked.connect(self.reject)

        buttons.addStretch()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)
        root.addLayout(buttons)

    def refresh_enrollment_cameras(self):
        selected_id = self.camera_combo.currentData() if self.camera_combo.count() else None
        cameras = _discover_cameras()

        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()

        for camera in cameras:
            self.camera_combo.addItem(camera["label"], camera["id"])

        if cameras:
            preferred_id = (
                selected_id
                if selected_id is not None
                else int(get_setting("camera_1_id", get_setting("camera_id", CAMERA_ID)))
            )
            index = self.camera_combo.findData(preferred_id)
            self.camera_combo.setCurrentIndex(index if index >= 0 else 0)
            self.camera_combo.setEnabled(True)
            self.refresh_camera_button.setEnabled(True)
            self.enrollment_camera_status.setText(
                f"{len(cameras)} دوربین پیدا شد • دوربین انتخاب‌شده برای همین ثبت استفاده می‌شود."
            )
        else:
            self.camera_combo.addItem("هیچ دوربینی پیدا نشد", None)
            self.camera_combo.setEnabled(False)
            self.refresh_camera_button.setEnabled(True)
            self.enrollment_camera_status.setText(
                "هیچ دوربینی در دسترس نیست. اتصال دوربین را بررسی کنید و «بازخوانی» را بزنید."
            )

        self.camera_combo.blockSignals(False)

    def start_capture(self):
        if self.camera_combo.currentData() is None:
            QMessageBox.warning(
                self,
                "دوربین یافت نشد",
                "ابتدا یک دوربین معتبر برای ثبت چهره انتخاب کنید.",
            )
            return

        if self.show_student_fields:
            if not self.code_edit.text().strip():
                QMessageBox.warning(
                    self,
                    "اطلاعات ناقص",
                    "کد دانش‌آموزی الزامی است.",
                )
                return

            if not self.name_edit.text().strip():
                QMessageBox.warning(
                    self,
                    "اطلاعات ناقص",
                    "نام دانش‌آموز الزامی است.",
                )
                return

        self.start_button.setEnabled(False)
        self.progress_label.setText(
            "دوربین فعال شد؛ روبه‌روی دوربین بایستید و کمی حرکت طبیعی داشته باشید."
        )

        selected_camera_id = int(self.camera_combo.currentData())
        selected_fps = int(
            get_setting(
                "camera_1_target_fps",
                get_setting("camera_target_fps", DEFAULT_CAMERA_TARGET_FPS),
            )
        )

        self.worker = EnrollmentWorker(
            camera_id=selected_camera_id,
            target_fps=selected_fps,
        )
        self.worker.frame_ready.connect(self.update_preview)
        self.worker.progress.connect(self.update_progress)
        self.worker.capture_done.connect(self.capture_finished)
        self.worker.error.connect(self.capture_error)
        self.worker.start()

    def update_preview(self, image):
        pixmap = QPixmap.fromImage(image)
        self.preview.setPixmap(
            pixmap.scaled(
                self.preview.size(),
                Qt.KeepAspectRatio,
                Qt.FastTransformation,
            )
        )

    def update_progress(self, current, total):
        self.progress_label.setText(
            f"ثبت شد: {current} از {total}"
        )

    def capture_error(self, message):
        QMessageBox.critical(self, "خطای ثبت", message)
        self.start_button.setEnabled(True)

    def capture_finished(self, embeddings):
        if embeddings is None:
            self.progress_label.setText("ثبت لغو شد یا کامل نشد.")
            self.start_button.setEnabled(True)
            return

        self.embeddings = embeddings
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(5000)

        self.progress_label.setText("ثبت چهره با موفقیت تمام شد.")
        self.accept()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        event.accept()


def confirm_action(parent, title, message, confirm_text="تأیید", cancel_text="لغو"):
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(message)

    confirm_button = box.addButton(
        confirm_text,
        QMessageBox.AcceptRole,
    )
    cancel_button = box.addButton(
        cancel_text,
        QMessageBox.RejectRole,
    )

    box.setDefaultButton(cancel_button)
    box.exec()

    return box.clickedButton() is confirm_button


class AdminPasswordDialog(QDialog):
    def __init__(self, mode="login", parent=None):
        super().__init__(parent)
        self.mode = mode
        self.attempts = 0
        self.setModal(True)
        self.resize(430, 250)

        titles = {
            "login": "ورود مدیر",
            "setup": "تنظیم رمز مدیر",
            "change": "تغییر رمز مدیر",
        }
        self.setWindowTitle(titles[mode])

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel(
            "سامانه حضور و غیاب مدرسه"
            if mode == "login"
            else (
                "برای شروع یک رمز مدیریتی تعیین کنید."
                if mode == "setup"
                else "رمز مدیریتی را به‌روزرسانی کنید."
            )
        )
        title.setObjectName("sectionTitle")
        root.addWidget(title)

        if mode == "login":
            message = QLabel("برای ورود به سامانه، رمز مدیر لازم است.")
            message.setObjectName("muted")
            message.setWordWrap(True)
            root.addWidget(message)

            self.password_edit = QLineEdit()
            self.password_edit.setEchoMode(QLineEdit.Password)
            self.password_edit.setPlaceholderText("رمز مدیر")
            self.password_edit.returnPressed.connect(self.submit_login)
            root.addWidget(self.password_edit)

        elif mode == "setup":
            message = QLabel("رمز باید حداقل ۸ کاراکتر داشته باشد.")
            message.setObjectName("muted")
            root.addWidget(message)

            self.password_edit = QLineEdit()
            self.password_edit.setEchoMode(QLineEdit.Password)
            self.password_edit.setPlaceholderText("رمز جدید")
            root.addWidget(self.password_edit)

            self.confirm_edit = QLineEdit()
            self.confirm_edit.setEchoMode(QLineEdit.Password)
            self.confirm_edit.setPlaceholderText("تکرار رمز")
            self.confirm_edit.returnPressed.connect(self.submit_setup)
            root.addWidget(self.confirm_edit)

        else:
            self.current_edit = QLineEdit()
            self.current_edit.setEchoMode(QLineEdit.Password)
            self.current_edit.setPlaceholderText("رمز فعلی")
            root.addWidget(self.current_edit)

            self.password_edit = QLineEdit()
            self.password_edit.setEchoMode(QLineEdit.Password)
            self.password_edit.setPlaceholderText("رمز جدید")
            root.addWidget(self.password_edit)

            self.confirm_edit = QLineEdit()
            self.confirm_edit.setEchoMode(QLineEdit.Password)
            self.confirm_edit.setPlaceholderText("تکرار رمز جدید")
            self.confirm_edit.returnPressed.connect(self.submit_change)
            root.addWidget(self.confirm_edit)

        buttons = QHBoxLayout()
        buttons.addStretch()

        cancel = QPushButton("لغو")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        self.submit_button = QPushButton(
            "ورود"
            if mode == "login"
            else ("ثبت رمز" if mode == "setup" else "تغییر رمز")
        )

        if mode == "login":
            self.submit_button.clicked.connect(self.submit_login)
        elif mode == "setup":
            self.submit_button.clicked.connect(self.submit_setup)
        else:
            self.submit_button.clicked.connect(self.submit_change)

        buttons.addWidget(self.submit_button)
        root.addLayout(buttons)

        self.password_edit.setFocus()

    def submit_login(self):
        if verify_admin_password(self.password_edit.text()):
            self.accept()
            return

        self.attempts += 1
        self.password_edit.clear()

        if self.attempts >= 5:
            QMessageBox.critical(
                self,
                "دسترسی رد شد",
                "تعداد تلاش‌های ناموفق بیش از حد مجاز شد.",
            )
            self.reject()
        else:
            QMessageBox.warning(
                self,
                "رمز نادرست",
                f"رمز اشتباه است. {5 - self.attempts} تلاش باقی مانده.",
            )

    def submit_setup(self):
        password = self.password_edit.text()
        confirm = self.confirm_edit.text()

        if len(password) < 8:
            QMessageBox.warning(
                self,
                "رمز کوتاه است",
                "رمز باید حداقل ۸ کاراکتر باشد.",
            )
            return

        if password != confirm:
            QMessageBox.warning(
                self,
                "عدم تطابق",
                "دو رمز یکسان نیستند.",
            )
            return

        set_admin_password(password)
        self.accept()

    def submit_change(self):
        current = self.current_edit.text()
        new_password = self.password_edit.text()
        confirm = self.confirm_edit.text()

        if len(new_password) < 8:
            QMessageBox.warning(
                self,
                "رمز کوتاه است",
                "رمز جدید باید حداقل ۸ کاراکتر باشد.",
            )
            return

        if new_password != confirm:
            QMessageBox.warning(
                self,
                "عدم تطابق",
                "دو رمز جدید یکسان نیستند.",
            )
            return

        try:
            changed = change_admin_password(current, new_password)
        except ValueError as exc:
            QMessageBox.warning(self, "خطا", str(exc))
            return

        if not changed:
            QMessageBox.warning(
                self,
                "رمز نادرست",
                "رمز فعلی اشتباه است.",
            )
            self.current_edit.clear()
            self.current_edit.setFocus()
            return

        self.accept()


class SchoolCalendarDialog(QDialog):
    WEEKDAYS = [
        (5, "شنبه"),
        (6, "یکشنبه"),
        (0, "دوشنبه"),
        (1, "سه‌شنبه"),
        (2, "چهارشنبه"),
        (3, "پنجشنبه"),
        (4, "جمعه"),
    ]

    def __init__(self, selected_year_id=None, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle("تقویم مدرسه")
        self.resize(1020, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("تقویم مدرسه")
        title.setObjectName("pageTitle")
        title.setStyleSheet("font-size: 23px;")
        header.addWidget(title)

        header.addStretch()

        header.addWidget(QLabel("سال تحصیلی"))
        self.year_combo = QComboBox()

        for year_id, name, start_date, end_date, is_active in list_academic_years():
            label = name + (" — فعال" if is_active else "")
            self.year_combo.addItem(label, year_id)

        target = selected_year_id
        active = get_active_academic_year()
        if target is None and active:
            target = active[0]

        index = self.year_combo.findData(target)
        if index >= 0:
            self.year_combo.setCurrentIndex(index)

        self.year_combo.currentIndexChanged.connect(
            lambda _: self.load_year()
        )
        header.addWidget(self.year_combo)
        root.addLayout(header)

        subtitle = QLabel(
            "روزهای عادی مدرسه را یک‌بار برای کل سال تحصیلی تنظیم کنید؛ "
            "تعطیلی یا روز جبرانی فقط از طریق استثناها تغییر می‌کند."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        weekly_box = QFrame()
        weekly_box.setObjectName("panel")
        weekly_layout = QVBoxLayout(weekly_box)
        weekly_layout.setContentsMargins(18, 16, 18, 16)
        weekly_layout.setSpacing(12)

        weekly_title = QLabel("روزهای عادی هفته")
        weekly_title.setObjectName("sectionTitle")
        weekly_layout.addWidget(weekly_title)

        checks_layout = QHBoxLayout()
        checks_layout.setSpacing(8)

        self.weekday_checks = {}
        self.weekday_group = QButtonGroup(self)
        self.weekday_group.setExclusive(False)

        for weekday, label in self.WEEKDAYS:
            check = QPushButton(label)
            check.setCheckable(True)
            check.setMinimumHeight(40)
            check.setMinimumWidth(82)
            check.setCursor(Qt.PointingHandCursor)
            check.setStyleSheet(
                """
                QPushButton {
                    background: #f4f7fa;
                    color: #526174;
                    border: 1px solid #cbd6e2;
                    border-radius: 9px;
                    padding: 8px 12px;
                }
                QPushButton:hover {
                    background: #e9f0f7;
                }
                QPushButton:checked {
                    background: #dbe9f8;
                    color: #1e5a9f;
                    border: 1px solid #78a7d5;
                    font-weight: 700;
                }
                """
            )
            self.weekday_checks[weekday] = check
            self.weekday_group.addButton(check, weekday)
            checks_layout.addWidget(check)

        checks_layout.addStretch()
        weekly_layout.addLayout(checks_layout)

        save_weekly = QPushButton("ذخیره روزهای هفتگی")
        save_weekly.setMinimumHeight(38)
        save_weekly.clicked.connect(self.save_weekly)
        weekly_layout.addWidget(save_weekly, 0, Qt.AlignRight)

        root.addWidget(weekly_box)

        exceptions_title = QLabel("استثناهای تقویم")
        exceptions_title.setObjectName("sectionTitle")
        root.addWidget(exceptions_title)

        content = QHBoxLayout()
        content.setSpacing(14)

        editor_box = QFrame()
        editor_box.setObjectName("panel")
        editor_box.setMinimumWidth(380)
        editor_layout = QVBoxLayout(editor_box)
        editor_layout.setContentsMargins(18, 16, 18, 16)
        editor_layout.setSpacing(12)

        editor_heading = QLabel("ثبت استثنا")
        editor_heading.setObjectName("sectionTitle")
        editor_layout.addWidget(editor_heading)

        editor_hint = QLabel(
            "برای یک تاریخ خاص مشخص کنید مدرسه تعطیل است یا آن روز باید روز مدرسه محسوب شود."
        )
        editor_hint.setObjectName("muted")
        editor_hint.setWordWrap(True)
        editor_layout.addWidget(editor_hint)

        form = QFormLayout()
        form.setSpacing(12)

        self.override_date = QDateEdit()
        self.override_date.setCalendarPopup(True)
        self.override_date.setDisplayFormat("yyyy/MM/dd")
        self.override_date.setMinimumHeight(38)

        form.addRow("تاریخ", self.override_date)
        editor_layout.addLayout(form)

        status_label = QLabel("وضعیت این تاریخ")
        status_label.setObjectName("statTitle")
        editor_layout.addWidget(status_label)

        status_layout = QHBoxLayout()
        status_layout.setSpacing(8)

        self.override_status_group = QButtonGroup(self)
        self.override_status_group.setExclusive(True)

        self.override_holiday_button = QPushButton("تعطیل")
        self.override_school_button = QPushButton("روز مدرسه")

        for button in (
            self.override_holiday_button,
            self.override_school_button,
        ):
            button.setCheckable(True)
            button.setMinimumHeight(42)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                """
                QPushButton {
                    background: #f4f7fa;
                    color: #526174;
                    border: 1px solid #cbd6e2;
                    border-radius: 9px;
                    padding: 9px 14px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background: #e9f0f7;
                }
                QPushButton:checked {
                    background: #e4edf9;
                    color: #1e5a9f;
                    border: 1px solid #78a7d5;
                }
                """
            )
            self.override_status_group.addButton(button)
            status_layout.addWidget(button, 1)

        self.override_holiday_button.setChecked(True)
        editor_layout.addLayout(status_layout)

        note_label = QLabel("توضیح")
        note_label.setObjectName("statTitle")
        editor_layout.addWidget(note_label)

        self.override_note = QLineEdit()
        self.override_note.setMinimumHeight(40)
        self.override_note.setPlaceholderText(
            "مثلاً تعطیلی رسمی، آلودگی هوا یا روز جبرانی"
        )
        editor_layout.addWidget(self.override_note)

        editor_layout.addStretch()

        save_override = QPushButton("ثبت یا به‌روزرسانی استثنا")
        save_override.setMinimumHeight(42)
        save_override.clicked.connect(self.save_override)

        clear_override = QPushButton("پاک‌کردن فرم")
        clear_override.setMinimumHeight(38)
        clear_override.clicked.connect(self.clear_override_form)

        editor_layout.addWidget(save_override)
        editor_layout.addWidget(clear_override)

        content.addWidget(editor_box, 1)

        list_box = QFrame()
        list_box.setObjectName("panel")
        list_layout = QVBoxLayout(list_box)
        list_layout.setContentsMargins(18, 16, 18, 16)
        list_layout.setSpacing(10)

        list_heading = QLabel("استثناهای ثبت‌شده")
        list_heading.setObjectName("sectionTitle")
        list_layout.addWidget(list_heading)

        self.override_table = QTableWidget(0, 4)
        self.override_table.setHorizontalHeaderLabels(
            ["شناسه", "تاریخ", "وضعیت", "توضیح"]
        )
        self.override_table.setColumnHidden(0, True)
        self.override_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.override_table.setSelectionMode(QTableWidget.SingleSelection)
        self.override_table.verticalHeader().setVisible(False)
        self.override_table.setAlternatingRowColors(True)
        self.override_table.horizontalHeader().setStretchLastSection(True)
        self.override_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self.override_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self.override_table.itemSelectionChanged.connect(
            self.load_selected_override
        )
        list_layout.addWidget(self.override_table, 1)

        delete_button = QPushButton("حذف استثنای انتخاب‌شده")
        delete_button.setMinimumHeight(40)
        delete_button.clicked.connect(self.delete_override)
        list_layout.addWidget(delete_button, 0, Qt.AlignLeft)

        content.addWidget(list_box, 2)
        root.addLayout(content, 1)

        footer = QHBoxLayout()
        footer.addStretch()

        close_button = QPushButton("بستن")
        close_button.setMinimumWidth(100)
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)

        root.addLayout(footer)

        self.load_year()

    def current_year_id(self):
        return self.year_combo.currentData()

    def load_year(self):
        year_id = self.current_year_id()
        if not year_id:
            return

        weekdays = get_school_weekdays(year_id)
        for weekday, check in self.weekday_checks.items():
            check.setChecked(weekday in weekdays)

        self.clear_override_form()
        self.refresh_overrides()

        year = next(
            (row for row in list_academic_years() if row[0] == year_id),
            None,
        )
        if year and year[2]:
            self.override_date.setDate(
                QDate.fromString(year[2], "yyyy-MM-dd")
            )

    def save_weekly(self):
        year_id = self.current_year_id()
        weekdays = [
            weekday
            for weekday, check in self.weekday_checks.items()
            if check.isChecked()
        ]

        try:
            set_school_weekdays(year_id, weekdays)
            QMessageBox.information(
                self,
                "تقویم مدرسه",
                "روزهای عادی هفته ذخیره شد.",
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "ذخیره برنامه ناموفق بود",
                str(exc),
            )

    def refresh_overrides(self):
        year_id = self.current_year_id()
        rows = list_school_day_overrides(year_id)
        self.override_table.setRowCount(len(rows))

        for row, (override_id, date, is_school_day, note) in enumerate(rows):
            values = [
                str(override_id),
                date,
                "روز مدرسه" if is_school_day else "تعطیل",
                note or "—",
            ]
            for col, value in enumerate(values):
                self.override_table.setItem(
                    row,
                    col,
                    QTableWidgetItem(value),
                )

    def load_selected_override(self):
        row = self.override_table.currentRow()
        if row < 0:
            return

        id_item = self.override_table.item(row, 0)
        date_item = self.override_table.item(row, 1)
        status_item = self.override_table.item(row, 2)
        note_item = self.override_table.item(row, 3)

        if not id_item or not date_item or not status_item:
            return

        self.override_date.setDate(
            QDate.fromString(date_item.text(), "yyyy-MM-dd")
        )
        is_school_day = status_item.text() == "روز مدرسه"
        self.override_school_button.setChecked(is_school_day)
        self.override_holiday_button.setChecked(not is_school_day)
        self.override_note.setText(
            "" if not note_item or note_item.text() == "—"
            else note_item.text()
        )

    def clear_override_form(self):
        year_id = self.current_year_id()
        if year_id:
            year = next(
                (row for row in list_academic_years() if row[0] == year_id),
                None,
            )
            if year and year[2]:
                self.override_date.setDate(
                    QDate.fromString(year[2], "yyyy-MM-dd")
                )

        self.override_holiday_button.setChecked(True)
        self.override_note.clear()
        self.override_table.clearSelection()

    def save_override(self):
        year_id = self.current_year_id()
        if not year_id:
            return

        date = self.override_date.date().toString("yyyy-MM-dd")

        try:
            set_school_day_override(
                year_id,
                date,
                self.override_school_button.isChecked(),
                self.override_note.text(),
            )
            self.refresh_overrides()
            self.clear_override_form()
        except Exception as exc:
            QMessageBox.critical(
                self,
                "ثبت استثنا ناموفق بود",
                str(exc),
            )

    def delete_override(self):
        row = self.override_table.currentRow()
        if row < 0:
            QMessageBox.information(
                self,
                "انتخاب استثنا",
                "ابتدا یک استثنا را از جدول انتخاب کنید.",
            )
            return

        item = self.override_table.item(row, 0)
        if not item:
            return

        if not confirm_action(
            self,
            "حذف استثنا",
            "استثنای انتخاب‌شده حذف شود؟",
            "حذف",
            "لغو",
        ):
            return

        try:
            delete_school_day_override(int(item.text()))
            self.refresh_overrides()
            self.clear_override_form()
        except Exception as exc:
            QMessageBox.critical(
                self,
                "حذف استثنا ناموفق بود",
                str(exc),
            )


class AcademicYearDialog(QDialog):
    def __init__(self, existing=None, parent=None):
        super().__init__(parent)

        self.existing = existing
        self.setModal(True)
        self.resize(520, 290)
        self.setWindowTitle(
            "ویرایش سال تحصیلی" if existing else "افزودن سال تحصیلی"
        )

        root = QVBoxLayout(self)

        title = QLabel("سال تحصیلی را با تاریخ واقعی شروع و پایان تعریف کنید.")
        title.setObjectName("sectionTitle")
        title.setWordWrap(True)
        root.addWidget(title)

        form = QFormLayout()

        self.start_date = QDateEdit()
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("yyyy/MM/dd")
        self.start_date.setMinimumDate(QDate(2000, 1, 1))
        self.start_date.setMaximumDate(QDate(2100, 12, 31))

        self.end_date = QDateEdit()
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("yyyy/MM/dd")
        self.end_date.setMinimumDate(QDate(2000, 1, 2))
        self.end_date.setMaximumDate(QDate(2100, 12, 31))

        today = QDate.currentDate()
        start_year = today.year() if today.month() >= 9 else today.year() - 1

        self.start_date.setDate(QDate(start_year, 9, 1))
        self.end_date.setDate(QDate(start_year + 1, 6, 30))

        if existing:
            _, _, existing_start, existing_end, _ = existing

            if existing_start:
                parsed_start = QDate.fromString(existing_start, "yyyy-MM-dd")
                if parsed_start.isValid():
                    self.start_date.setDate(parsed_start)

            if existing_end:
                parsed_end = QDate.fromString(existing_end, "yyyy-MM-dd")
                if parsed_end.isValid():
                    self.end_date.setDate(parsed_end)

        self.start_date.dateChanged.connect(self.on_start_date_changed)

        form.addRow("تاریخ شروع", self.start_date)
        form.addRow("تاریخ پایان", self.end_date)

        self.generated_name = QLabel()
        self.generated_name.setObjectName("muted")
        form.addRow("عنوان سال", self.generated_name)

        root.addLayout(form)

        self.range_hint = QLabel()
        self.range_hint.setObjectName("muted")
        self.range_hint.setWordWrap(True)
        root.addWidget(self.range_hint)

        buttons = QHBoxLayout()
        buttons.addStretch()

        cancel = QPushButton("لغو")
        save = QPushButton("ذخیره")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.validate_and_accept)

        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

        self.update_year_preview()

    def on_start_date_changed(self, date):
        if date >= self.end_date.date():
            suggested_end = QDate(date.year() + 1, 6, 30)
            if suggested_end > date:
                self.end_date.setDate(suggested_end)

        self.end_date.setMinimumDate(
            date.addDays(1)
        )
        self.update_year_preview()

    def update_year_preview(self):
        start = self.start_date.date()
        end = self.end_date.date()
        self.generated_name.setText(
            f"{start.year()}-{end.year()}"
        )

        if end > start:
            days = start.daysTo(end)
            self.range_hint.setText(
                f"بازه انتخاب‌شده: {days} روز"
            )
        else:
            self.range_hint.setText("تاریخ پایان باید بعد از شروع باشد.")

    def validate_and_accept(self):
        start = self.start_date.date()
        end = self.end_date.date()

        if not start.isValid() or not end.isValid():
            QMessageBox.warning(
                self,
                "تاریخ نامعتبر",
                "هر دو تاریخ را به‌درستی انتخاب کنید.",
            )
            return

        if end <= start:
            QMessageBox.warning(
                self,
                "بازه نامعتبر",
                "تاریخ پایان باید بعد از تاریخ شروع باشد.",
            )
            return

        self.accept()

    def values(self):
        return (
            self.start_date.date().toString("yyyy-MM-dd"),
            self.end_date.date().toString("yyyy-MM-dd"),
        )


class ClassDialog(QDialog):
    def __init__(self, academic_years, majors, existing=None, parent=None):
        super().__init__(parent)

        self.setModal(True)
        self.resize(560, 340)
        self.setWindowTitle("ویرایش کلاس" if existing else "افزودن کلاس")

        root = QVBoxLayout(self)
        root.setSpacing(14)

        title = QLabel(
            "اطلاعات کلاس را وارد کنید."
            if not existing
            else "اطلاعات کلاس را ویرایش کنید."
        )
        title.setObjectName("sectionTitle")
        root.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignRight | Qt.AlignTop)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("مثلاً کلاس ۱۰۱")

        self.grade_edit = QLineEdit()
        self.grade_edit.setPlaceholderText("مثلاً دهم")

        self.major_combo = QComboBox()
        self.major_combo.addItem("بدون رشته", None)
        for major_id, name, _ in majors:
            self.major_combo.addItem(name, major_id)

        self.year_combo = QComboBox()
        self.year_combo.addItem("انتخاب سال تحصیلی", None)

        for year_id, name, start_date, end_date, is_active in academic_years:
            date_text = ""
            if start_date and end_date:
                date_text = f" • {start_date} تا {end_date}"

            label = f"{name}{date_text}"
            if is_active:
                label += " • فعال"

            self.year_combo.addItem(label, year_id)

        active = get_active_academic_year()
        if active:
            active_index = self.year_combo.findData(active[0])
            if active_index >= 0:
                self.year_combo.setCurrentIndex(active_index)

        form.addRow("نام کلاس", self.name_edit)
        form.addRow("پایه", self.grade_edit)
        form.addRow("رشته", self.major_combo)
        form.addRow("سال تحصیلی", self.year_combo)
        root.addLayout(form)

        hint = QLabel(
            "نام کلاس، پایه و سال تحصیلی الزامی هستند. "
            "یک کلاس با نام تکراری در یک سال دوباره ساخته نمی‌شود."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch()

        cancel = QPushButton("لغو")
        save = QPushButton("افزودن کلاس" if not existing else "ذخیره تغییرات")

        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.validate_and_accept)

        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

        if existing:
            class_id, name, grade, major_name, year_id, year_name, count = existing
            self.name_edit.setText(name)
            self.grade_edit.setText(grade)

            if major_name:
                index = self.major_combo.findText(major_name)
                if index >= 0:
                    self.major_combo.setCurrentIndex(index)

            index = self.year_combo.findData(year_id)
            if index >= 0:
                self.year_combo.setCurrentIndex(index)

        self.name_edit.setFocus()

    def validate_and_accept(self):
        name = self.name_edit.text().strip()
        grade = self.grade_edit.text().strip()
        year_id = self.year_combo.currentData()

        if not name:
            QMessageBox.warning(
                self,
                "اطلاعات ناقص",
                "نام کلاس را وارد کنید.",
            )
            self.name_edit.setFocus()
            return

        if not grade:
            QMessageBox.warning(
                self,
                "اطلاعات ناقص",
                "پایه را وارد کنید.",
            )
            self.grade_edit.setFocus()
            return

        if year_id is None:
            QMessageBox.warning(
                self,
                "سال تحصیلی",
                "یک سال تحصیلی انتخاب کنید.",
            )
            self.year_combo.setFocus()
            return

        self.accept()

    def values(self):
        return (
            self.name_edit.text().strip(),
            self.grade_edit.text().strip(),
            self.major_combo.currentData(),
            self.year_combo.currentData(),
        )


class ManualAttendanceDialog(QDialog):
    STATUS_OPTIONS = [
        ("حاضر", "present"),
        ("دیرکرد", "late"),
        ("موجه", "excused"),
        ("غایب", "absent"),
    ]

    def __init__(self, student_name, current_status, current_notes="", parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.resize(480, 250)
        self.setWindowTitle("ویرایش وضعیت حضور")

        root = QVBoxLayout(self)
        title = QLabel(student_name)
        title.setObjectName("sectionTitle")
        root.addWidget(title)

        form = QFormLayout()
        self.status_combo = QComboBox()
        for label, value in self.STATUS_OPTIONS:
            self.status_combo.addItem(label, value)

        index = self.status_combo.findData(current_status)
        if index >= 0:
            self.status_combo.setCurrentIndex(index)

        self.notes_edit = QLineEdit(current_notes)
        self.notes_edit.setPlaceholderText("توضیح اختیاری")

        form.addRow("وضعیت", self.status_combo)
        form.addRow("توضیح", self.notes_edit)
        root.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("لغو")
        save = QPushButton("ذخیره")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

    def values(self):
        return self.status_combo.currentData(), self.notes_edit.text().strip()


class AttendanceTrendChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = []
        self.setMinimumHeight(240)

    def set_data(self, points):
        self.points = list(points or [])
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(52, 18, -18, -34)

        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setPen(QPen(QColor("#e2e8f0"), 1))

        for i in range(5):
            y = rect.top() + (rect.height() * i / 4)
            painter.drawLine(
                int(rect.left()),
                int(y),
                int(rect.right()),
                int(y),
            )

        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor("#718096"))

        if not self.points:
            painter.drawText(
                self.rect(),
                Qt.AlignCenter,
                "داده‌ای برای نمایش وجود ندارد",
            )
            return

        values = [float(item[5]) for item in self.points]
        max_value = max(100.0, max(values))
        min_value = 0.0

        for i, value in enumerate((100, 75, 50, 25, 0)):
            y = rect.top() + rect.height() * (1 - value / max_value)
            painter.drawText(
                8,
                int(y + 4),
                f"{int(value)}٪",
            )

        path = QPainterPath()
        for index, point in enumerate(self.points):
            value = point[5]
            x = (
                rect.left()
                if len(self.points) == 1
                else rect.left()
                + rect.width() * index / (len(self.points) - 1)
            )
            y = rect.bottom() - (
                (value - min_value)
                / max(1.0, max_value - min_value)
            ) * rect.height()

            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        painter.setPen(QPen(QColor("#2f80ed"), 3))
        painter.drawPath(path)

        painter.setBrush(QColor("#2f80ed"))
        painter.setPen(Qt.NoPen)

        for index, point in enumerate(self.points):
            value = point[5]
            x = (
                rect.left()
                if len(self.points) == 1
                else rect.left()
                + rect.width() * index / (len(self.points) - 1)
            )
            y = rect.bottom() - (
                (value - min_value)
                / max(1.0, max_value - min_value)
            ) * rect.height()

            painter.drawEllipse(int(x - 4), int(y - 4), 8, 8)

        painter.setPen(QColor("#718096"))
        for index, point in enumerate(self.points):
            if index not in {
                0,
                len(self.points) // 2,
                len(self.points) - 1,
            }:
                continue

            x = (
                rect.left()
                if len(self.points) == 1
                else rect.left()
                + rect.width() * index / (len(self.points) - 1)
            )
            label = point[0][5:]
            painter.drawText(
                int(x - 35),
                self.height() - 10,
                label,
            )


class ClassAttendanceChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.setMinimumHeight(240)

    def set_data(self, rows):
        self.rows = list(rows or [])
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))

        rect = self.rect().adjusted(95, 18, -20, -35)

        if not self.rows:
            painter.setPen(QColor("#718096"))
            painter.drawText(
                self.rect(),
                Qt.AlignCenter,
                "داده‌ای برای نمایش وجود ندارد",
            )
            return

        visible = self.rows[:8]
        count = len(visible)
        gap = 10
        bar_height = max(
            14,
            int((rect.height() - gap * (count - 1)) / count),
        )

        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor("#526174"))

        for index, row in enumerate(visible):
            _, name, _, student_count, present, late, absent, excused, percent = row

            y = int(rect.top() + index * (bar_height + gap))
            label = name[:18]
            painter.drawText(
                10,
                y + bar_height - 3,
                label,
            )

            back = rect.adjusted(
                0,
                0,
                0,
                0,
            )
            back.setTop(y)
            back.setBottom(y + bar_height)
            back.setLeft(95)
            back.setRight(rect.right())

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#e7edf3"))
            painter.drawRoundedRect(back, 7, 7)

            width = int(back.width() * min(100.0, max(0.0, percent)) / 100.0)
            bar = back
            bar.setRight(back.left() + width)

            painter.setBrush(QColor("#36a269"))
            painter.drawRoundedRect(bar, 7, 7)

            painter.setPen(QColor("#526174"))
            painter.drawText(
                back.right() - 45,
                y + bar_height - 3,
                f"{percent:.0f}٪",
            )


class StatusDonutChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = {
            "present": 0,
            "late": 0,
            "absent": 0,
            "excused": 0,
        }
        self.setMinimumHeight(240)

    def set_data(self, data):
        self.data = {
            key: int(data.get(key, 0))
            for key in self.data
        }
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))

        total = sum(self.data.values())
        if total <= 0:
            painter.setPen(QColor("#718096"))
            painter.drawText(
                self.rect(),
                Qt.AlignCenter,
                "داده‌ای برای نمایش وجود ندارد",
            )
            return

        colors = {
            "present": "#36a269",
            "late": "#e0a13a",
            "absent": "#df5b5b",
            "excused": "#6b8fd6",
        }
        labels = {
            "present": "حاضر",
            "late": "دیرکرد",
            "absent": "غایب",
            "excused": "موجه",
        }

        # Reserve a separate area for the legend so its text never sits
        # on top of the donut, even when the report window is resized.
        legend_width = 170
        chart_width = max(180, self.width() - legend_width - 35)
        pie_size = max(
            110,
            min(chart_width - 15, self.height() - 35),
        )
        pie_x = int((chart_width - pie_size) / 2)
        pie_y = int((self.height() - pie_size) / 2)

        start_angle = 0
        for key in ("present", "late", "absent", "excused"):
            value = self.data[key]
            if value <= 0:
                continue

            span = int(360 * 16 * value / total)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(colors[key]))
            painter.drawPie(
                pie_x,
                pie_y,
                pie_size,
                pie_size,
                start_angle,
                span,
            )
            start_angle += span

        hole = max(60, int(pie_size * 0.54))
        hole_x = int(
            pie_x + (pie_size - hole) / 2
        )
        hole_y = int(
            pie_y + (pie_size - hole) / 2
        )

        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(hole_x, hole_y, hole, hole)

        painter.setPen(QColor("#253244"))
        painter.setFont(QFont("Segoe UI", 13, QFont.Bold))
        painter.drawText(
            hole_x,
            hole_y,
            hole,
            hole,
            Qt.AlignCenter,
            str(total),
        )

        legend_x = chart_width + 20
        legend_y = max(
            40,
            int(self.height() / 2) - 56,
        )
        painter.setFont(QFont("Segoe UI", 10))

        for key in ("present", "late", "absent", "excused"):
            painter.setBrush(QColor(colors[key]))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(
                legend_x,
                legend_y - 10,
                12,
                12,
                3,
                3,
            )
            painter.setPen(QColor("#526174"))
            painter.drawText(
                legend_x + 21,
                legend_y,
                f"{labels[key]}: {self.data[key]}",
            )
            legend_y += 28


class StableScrollArea(QScrollArea):
    """Scrollable page that keeps its vertical position across window resizes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setAlignment(Qt.AlignTop)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def resizeEvent(self, event):
        scroll_bar = self.verticalScrollBar()
        value = scroll_bar.value()
        super().resizeEvent(event)
        QTimer.singleShot(
            0,
            lambda: scroll_bar.setValue(min(value, scroll_bar.maximum())),
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        init_db()

        self.setWindowTitle("سامانه حضور و غیاب مدرسه")
        self.resize(1450, 900)

        self.camera_workers = {}
        self.camera_worker = None  # Backwards-compatible alias for Camera 1.
        self._dual_camera_fps_cap = 30
        self._camera_supervisor_enabled = True
        self._camera_restart_pending = False
        self._shutting_down = False

        # Preview frames use a latest-value mailbox rather than queued GUI
        # events. This prevents a slow GUI from displaying stale camera frames.
        self._latest_camera_images = {}
        self._latest_camera_images_lock = threading.Lock()
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(33)  # ~30 Hz GUI refresh
        self._preview_timer.timeout.connect(self._render_latest_camera_previews)

        self.presence_state = AttendancePresenceState()
        # Restore only students whose latest movement event says they are
        # currently inside. Attendance rows alone cannot distinguish someone
        # who already exited from someone still present.
        try:
            self.presence_state.present.update(get_current_presence_ids())
        except Exception:
            pass
        self.global_identity_manager = GlobalIdentityManager(
            presence_state=self.presence_state,
            setting_callback=get_setting,
        )
        self.current_page = 0

        self.build_ui()
        self.refresh_all()

        # Configure CPU threading before any YOLO/OSNet inference objects start.
        _configure_cpu_runtime()
        self.start_camera()

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(235)

        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(18, 22, 18, 18)
        side_layout.setSpacing(8)

        title = QLabel("حضور و غیاب")
        title.setObjectName("brand")
        side_layout.addWidget(title)

        subtitle = QLabel("سامانه مدیریت مدرسه")
        subtitle.setObjectName("brandSub")
        side_layout.addWidget(subtitle)

        side_layout.addSpacing(24)

        self.nav_buttons = []
        navigation = (
            ("داشبورد", 0),
            ("دانش‌آموزان", 1),
            ("کلاس‌ها", 2),
            ("رشته‌ها", 3),
            ("حضور و غیاب", 4),
            ("گزارش‌ها", 5),
            ("تنظیمات", 6),
        )

        for text, index in navigation:
            button = QPushButton(text)
            button.setCheckable(True)
            button.clicked.connect(
                lambda checked=False, i=index: self.show_page(i)
            )
            self.nav_buttons.append(button)
            side_layout.addWidget(button)

        side_layout.addStretch()

        self.change_password_button = QPushButton("تغییر رمز مدیر")
        self.change_password_button.clicked.connect(self.change_admin_password)
        side_layout.addWidget(self.change_password_button)

        self.camera_status = QLabel("دوربین در حال راه‌اندازی...")
        self.camera_status.setObjectName("sidebarStatus")
        self.camera_status.setWordWrap(True)
        side_layout.addWidget(self.camera_status)

        main_layout.addWidget(sidebar)

        content = QFrame()
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(30, 24, 30, 30)

        self.page_title = QLabel()
        self.page_title.setObjectName("pageTitle")
        content_layout.addWidget(self.page_title)

        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)

        self.build_dashboard_page()
        self.build_students_page()
        self.build_classes_page()
        self.build_majors_page()
        self.build_attendance_page()
        self.build_reports_page()
        self.build_settings_page()

        self._wrap_pages_in_scroll_areas()

        main_layout.addWidget(content, 1)
        self.show_page(0)

    def _wrap_pages_in_scroll_areas(self):
        """Make every content page usable on short/narrow windows."""
        for index in range(self.stack.count()):
            page = self.stack.widget(index)
            if isinstance(page, QScrollArea):
                continue

            self.stack.removeWidget(page)

            scroll = StableScrollArea()
            scroll.setObjectName("pageScrollArea")
            scroll.setWidget(page)

            self.stack.insertWidget(index, scroll)

    def show_page(self, index):
        if not self.stack or not (0 <= index < self.stack.count()):
            return

        self.current_page = index
        self.stack.setCurrentIndex(index)

        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)

        titles = (
            "داشبورد",
            "دانش‌آموزان",
            "کلاس‌ها",
            "رشته‌ها",
            "حضور و غیاب",
            "گزارش‌ها",
            "تنظیمات",
        )
        self.page_title.setText(titles[index])

    def make_card(self, title):
        card = QFrame()
        card.setObjectName("statCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)

        title_label = QLabel(title)
        title_label.setObjectName("statTitle")
        value_label = QLabel("0")
        value_label.setObjectName("statValue")

        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return card, value_label

    def build_dashboard_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(16)

        cards = QHBoxLayout()

        card, self.total_students_label = self.make_card("کل دانش‌آموزان")
        cards.addWidget(card)

        card, self.present_label = self.make_card("حاضر امروز")
        cards.addWidget(card)

        card, self.absent_label = self.make_card("غایب امروز")
        cards.addWidget(card)

        card, self.late_label = self.make_card("دیرکرد امروز")
        cards.addWidget(card)

        layout.addLayout(cards)

        camera_row = QHBoxLayout()
        camera_row.setSpacing(16)

        camera_box = QFrame()
        camera_box.setObjectName("panel")
        camera_layout = QVBoxLayout(camera_box)
        camera_title = QLabel("نمای زنده دوربین‌ها")
        camera_title.setAlignment(Qt.AlignRight)
        camera_layout.addWidget(camera_title)

        preview_row = QHBoxLayout()
        preview_row.setSpacing(10)

        self.camera_previews = {}
        for slot in (1, 2):
            preview_box = QFrame()
            preview_box.setObjectName("cameraPreviewBox")
            preview_layout = QVBoxLayout(preview_box)
            preview_title = QLabel(f"Camera {slot}")
            preview_title.setObjectName("muted")
            preview_layout.addWidget(preview_title)

            preview = QLabel(
                "Camera 2 غیرفعال است." if slot == 2 else "در انتظار دوربین..."
            )
            preview.setAlignment(Qt.AlignCenter)
            preview.setMinimumSize(400, 280)
            preview.setObjectName("cameraPreview")
            preview_layout.addWidget(preview, 1)

            self.camera_previews[slot] = preview
            preview_row.addWidget(preview_box, 1)

        self.camera_preview = self.camera_previews[1]
        camera_layout.addLayout(preview_row, 1)
        camera_row.addWidget(camera_box, 3)

        recent_box = QFrame()
        recent_box.setObjectName("panel")
        recent_layout = QVBoxLayout(recent_box)
        recent_title = QLabel("آخرین ورودها")
        recent_title.setObjectName("sectionTitle")
        recent_layout.addWidget(recent_title)

        self.recent_table = QTableWidget(0, 4)
        self.recent_table.setHorizontalHeaderLabels(
            ["دانش‌آموز", "کلاس", "زمان", "وضعیت"]
        )
        self.configure_table(self.recent_table)
        recent_layout.addWidget(self.recent_table)
        camera_row.addWidget(recent_box, 1)

        layout.addLayout(camera_row, 1)

        lower = QHBoxLayout()
        class_box = QFrame()
        class_box.setObjectName("panel")
        class_layout = QVBoxLayout(class_box)
        title = QLabel("وضعیت کلاس‌ها")
        title.setObjectName("sectionTitle")
        class_layout.addWidget(title)

        self.dashboard_classes = QTableWidget(0, 5)
        self.dashboard_classes.setHorizontalHeaderLabels(
            ["کلاس", "رشته", "کل", "حاضر", "درصد"]
        )
        self.configure_table(self.dashboard_classes)
        class_layout.addWidget(self.dashboard_classes)
        lower.addWidget(class_box, 2)

        absent_box = QFrame()
        absent_box.setObjectName("panel")
        absent_layout = QVBoxLayout(absent_box)
        absent_title = QLabel("غایبان امروز")
        absent_title.setObjectName("sectionTitle")
        absent_layout.addWidget(absent_title)

        self.dashboard_absent = QTableWidget(0, 3)
        self.dashboard_absent.setHorizontalHeaderLabels(
            ["نام", "کلاس", "رشته"]
        )
        self.configure_table(self.dashboard_absent)
        absent_layout.addWidget(self.dashboard_absent)
        lower.addWidget(absent_box, 1)

        layout.addLayout(lower)
        self.stack.addWidget(page)

    def build_students_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        toolbar = QHBoxLayout()
        self.student_search = QLineEdit()
        self.student_search.setPlaceholderText("جست‌وجو بر اساس نام یا کد...")
        self.student_search.textChanged.connect(self.filter_students_table)

        buttons = [
            ("افزودن دانش‌آموز", self.add_student_dialog),
            ("ویرایش", self.edit_selected_student),
            ("ثبت دوباره چهره", self.reenroll_selected_student),
            ("حذف", self.delete_selected_student),
            ("ورود Excel/CSV", self.import_students),
            ("بازخوانی", self.refresh_students),
        ]

        toolbar.addWidget(self.student_search, 1)
        for label, handler in buttons:
            button = QPushButton(label)
            button.clicked.connect(handler)
            toolbar.addWidget(button)

        layout.addLayout(toolbar)

        self.students_table = QTableWidget(0, 8)
        self.students_table.setHorizontalHeaderLabels(
            ["شناسه", "کد", "نام", "پایه", "رشته", "کلاس", "چهره", "ایجاد"]
        )
        self.configure_table(self.students_table)
        self.students_table.setColumnHidden(0, True)
        self.students_table.doubleClicked.connect(
            lambda index: self.edit_selected_student()
        )
        layout.addWidget(self.students_table, 1)

        self.stack.addWidget(page)

    def build_classes_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        toolbar = QHBoxLayout()

        toolbar.addWidget(QLabel("سال تحصیلی"))
        self.class_year_combo = QComboBox()
        self.class_year_combo.currentIndexChanged.connect(
            lambda _: self.refresh_classes()
        )
        toolbar.addWidget(self.class_year_combo, 1)

        for label, handler in (
            ("افزودن سال", self.add_academic_year_dialog),
            ("ویرایش سال", self.edit_academic_year_dialog),
            ("فعال‌کردن سال", self.set_active_year),
            ("تقویم مدرسه", self.open_school_calendar_dialog),
            ("افزودن کلاس", self.add_class_dialog),
            ("ویرایش", self.edit_selected_class),
            ("حذف", self.delete_selected_class),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            toolbar.addWidget(button)

        layout.addLayout(toolbar)

        self.classes_table = QTableWidget(0, 7)
        self.classes_table.setHorizontalHeaderLabels(
            ["شناسه", "کلاس", "پایه", "رشته", "سال تحصیلی", "دانش‌آموز", "وضعیت"]
        )
        self.configure_table(self.classes_table)
        self.classes_table.setColumnHidden(0, True)
        layout.addWidget(self.classes_table, 1)

        self.stack.addWidget(page)

    def build_majors_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        toolbar = QHBoxLayout()
        for label, handler in (
            ("افزودن رشته", self.add_major_dialog),
            ("ویرایش", self.edit_selected_major),
            ("حذف", self.delete_selected_major),
            ("بازخوانی", self.refresh_majors),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.majors_table = QTableWidget(0, 3)
        self.majors_table.setHorizontalHeaderLabels(
            ["شناسه", "رشته", "ایجاد"]
        )
        self.configure_table(self.majors_table)
        self.majors_table.setColumnHidden(0, True)
        layout.addWidget(self.majors_table, 1)

        self.stack.addWidget(page)

    def build_attendance_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        toolbar = QHBoxLayout()
        self.attendance_date = QDateEdit()
        self.attendance_date.setCalendarPopup(True)
        self.attendance_date.setDate(QDate.currentDate())
        self.attendance_date.dateChanged.connect(
            lambda _: self.refresh_attendance()
        )

        self.attendance_major_combo = QComboBox()
        self.attendance_class_combo = QComboBox()

        self.attendance_major_combo.currentIndexChanged.connect(
            self.on_attendance_major_changed
        )
        self.attendance_class_combo.currentIndexChanged.connect(
            lambda _: self.refresh_attendance()
        )

        toolbar.addWidget(QLabel("تاریخ"))
        toolbar.addWidget(self.attendance_date)
        toolbar.addWidget(QLabel("رشته"))
        toolbar.addWidget(self.attendance_major_combo, 1)
        toolbar.addWidget(QLabel("کلاس"))
        toolbar.addWidget(self.attendance_class_combo, 1)

        edit_button = QPushButton("ویرایش وضعیت")
        edit_button.clicked.connect(self.edit_selected_attendance)
        toolbar.addWidget(edit_button)

        refresh_button = QPushButton("بازخوانی")
        refresh_button.clicked.connect(self.refresh_attendance)
        toolbar.addWidget(refresh_button)

        layout.addLayout(toolbar)

        self.attendance_summary = QLabel()
        self.attendance_summary.setObjectName("muted")
        layout.addWidget(self.attendance_summary)

        self.attendance_table = QTableWidget(0, 9)
        self.attendance_table.setHorizontalHeaderLabels(
            [
                "شناسه",
                "کد",
                "نام",
                "کلاس",
                "وضعیت",
                "زمان",
                "دوربین",
                "توضیح",
                "منبع",
            ]
        )
        self.configure_table(self.attendance_table)
        self.attendance_table.setColumnHidden(0, True)
        self.attendance_table.doubleClicked.connect(
            lambda index: self.edit_selected_attendance()
        )
        layout.addWidget(self.attendance_table, 1)

        self.stack.addWidget(page)

    def build_reports_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)

        filter_box = QFrame()
        filter_box.setObjectName("panel")
        filter_layout = QHBoxLayout(filter_box)
        filter_layout.setContentsMargins(16, 14, 16, 14)

        self.report_start = QDateEdit()
        self.report_start.setCalendarPopup(True)
        self.report_start.setDisplayFormat("yyyy/MM/dd")
        self.report_start.setDate(QDate.currentDate().addDays(-6))

        self.report_end = QDateEdit()
        self.report_end.setCalendarPopup(True)
        self.report_end.setDisplayFormat("yyyy/MM/dd")
        self.report_end.setDate(QDate.currentDate())
        self.report_start.dateChanged.connect(lambda _: self.refresh_report())
        self.report_end.dateChanged.connect(lambda _: self.refresh_report())

        self.report_major_combo = QComboBox()
        self.report_class_combo = QComboBox()

        self.report_major_combo.currentIndexChanged.connect(
            self.on_report_major_changed
        )
        self.report_class_combo.currentIndexChanged.connect(
            lambda _: self.refresh_report()
        )

        filter_layout.addWidget(QLabel("از"))
        filter_layout.addWidget(self.report_start)
        filter_layout.addWidget(QLabel("تا"))
        filter_layout.addWidget(self.report_end)
        filter_layout.addWidget(QLabel("رشته"))
        filter_layout.addWidget(self.report_major_combo, 1)
        filter_layout.addWidget(QLabel("کلاس"))
        filter_layout.addWidget(self.report_class_combo, 1)

        today_button = QPushButton("امروز")
        today_button.clicked.connect(lambda: self.set_report_range(0))
        week_button = QPushButton("۷ روز")
        week_button.clicked.connect(lambda: self.set_report_range(6))
        month_button = QPushButton("۳۰ روز")
        month_button.clicked.connect(lambda: self.set_report_range(29))
        report_button = QPushButton("به‌روزرسانی")
        report_button.clicked.connect(self.refresh_report)

        csv_button = QPushButton("خروجی CSV")
        csv_button.clicked.connect(lambda: self.export_report("csv"))
        xlsx_button = QPushButton("خروجی Excel")
        xlsx_button.clicked.connect(lambda: self.export_report("xlsx"))

        for button in (
            today_button,
            week_button,
            month_button,
            report_button,
            csv_button,
            xlsx_button,
        ):
            filter_layout.addWidget(button)

        layout.addWidget(filter_box)

        self.report_summary = QLabel()
        self.report_summary.setObjectName("muted")
        layout.addWidget(self.report_summary)

        cards = QHBoxLayout()
        card, self.report_total_label = self.make_card("دانش‌آموزان")
        cards.addWidget(card)
        card, self.report_avg_label = self.make_card("میانگین حضور")
        cards.addWidget(card)
        card, self.report_absent_days_label = self.make_card("کل غیبت")
        cards.addWidget(card)
        card, self.report_late_days_label = self.make_card("کل دیرکرد")
        cards.addWidget(card)
        layout.addLayout(cards)

        chart_row = QHBoxLayout()
        trend_panel = QFrame()
        trend_panel.setObjectName("panel")
        trend_layout = QVBoxLayout(trend_panel)
        title = QLabel("روند درصد حضور روزانه")
        title.setObjectName("sectionTitle")
        trend_layout.addWidget(title)
        self.report_trend_chart = AttendanceTrendChart()
        trend_layout.addWidget(self.report_trend_chart)
        chart_row.addWidget(trend_panel, 2)

        class_panel = QFrame()
        class_panel.setObjectName("panel")
        class_layout = QVBoxLayout(class_panel)
        title = QLabel("مقایسه کلاس‌ها")
        title.setObjectName("sectionTitle")
        class_layout.addWidget(title)
        self.report_class_chart = ClassAttendanceChart()
        class_layout.addWidget(self.report_class_chart)
        chart_row.addWidget(class_panel, 2)

        status_panel = QFrame()
        status_panel.setObjectName("panel")
        status_layout = QVBoxLayout(status_panel)
        title = QLabel("توزیع وضعیت")
        title.setObjectName("sectionTitle")
        status_layout.addWidget(title)
        self.report_status_chart = StatusDonutChart()
        status_layout.addWidget(self.report_status_chart)
        chart_row.addWidget(status_panel, 1)

        layout.addLayout(chart_row)

        details_panel = QFrame()
        details_panel.setObjectName("panel")
        details_layout = QVBoxLayout(details_panel)

        details_title = QLabel("جزئیات دانش‌آموزان")
        details_title.setObjectName("sectionTitle")
        details_layout.addWidget(details_title)

        self.report_table = QTableWidget(0, 11)
        self.report_table.setHorizontalHeaderLabels(
            [
                "شناسه",
                "کد",
                "نام",
                "پایه",
                "رشته",
                "کلاس",
                "حاضر",
                "دیرکرد",
                "غیبت",
                "موجه",
                "درصد حضور",
            ]
        )
        self.configure_table(self.report_table)
        self.report_table.setColumnHidden(0, True)
        details_layout.addWidget(self.report_table, 1)

        layout.addWidget(details_panel, 2)

        self.stack.addWidget(page)
    def configure_table(self, table):
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setMinimumHeight(260)


    def build_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)

        camera_panel = QFrame()
        camera_panel.setObjectName("panel")
        camera_layout = QVBoxLayout(camera_panel)

        camera_title = QLabel("تنظیمات دوربین")
        camera_title.setObjectName("sectionTitle")
        camera_layout.addWidget(camera_title)

        camera_form = QFormLayout()

        self.settings_camera1_enabled = QCheckBox("فعال")
        self.settings_camera1_enabled.setChecked(True)
        self.settings_camera1_combo = QComboBox()

        self.settings_camera2_enabled = QCheckBox("فعال")
        self.settings_camera2_enabled.setChecked(False)
        self.settings_camera2_combo = QComboBox()

        self.settings_camera_combo = self.settings_camera1_combo
        self.settings_refresh_cameras_button = QPushButton("بازخوانی دوربین‌ها")
        self.settings_refresh_cameras_button.clicked.connect(
            self.refresh_camera_choices
        )

        camera1_row = QHBoxLayout()
        camera1_row.addWidget(self.settings_camera1_enabled)
        camera1_row.addWidget(self.settings_camera1_combo, 1)
        camera1_row.addWidget(self.settings_refresh_cameras_button)

        camera2_row = QHBoxLayout()
        camera2_row.addWidget(self.settings_camera2_enabled)
        camera2_row.addWidget(self.settings_camera2_combo, 1)

        self.settings_fps_combo = QComboBox()
        self.settings_fps_combo.addItem("30 FPS", 30)
        self.settings_fps_combo.addItem(
            "60 FPS (در صورت پشتیبانی دوربین)",
            60,
        )

        camera_form.addRow("دوربین ۱", camera1_row)
        camera_form.addRow("دوربین ۲", camera2_row)
        camera_form.addRow("نرخ هدف هر دوربین", self.settings_fps_combo)
        camera_layout.addLayout(camera_form)

        info = QLabel(
            "رزولوشن 1280×720 درخواست می‌شود. نرخ 60 فقط وقتی واقعی است که "
            "خود دوربین و درایور آن این حالت را پشتیبانی کنند."
        )
        info.setObjectName("muted")
        info.setWordWrap(True)
        camera_layout.addWidget(info)
        layout.addWidget(camera_panel)

        gate_panel = QFrame()
        gate_panel.setObjectName("panel")
        gate_layout = QVBoxLayout(gate_panel)

        gate_title = QLabel("ورودی / خروجی و خط مجازی هر دوربین")
        gate_title.setObjectName("sectionTitle")
        gate_layout.addWidget(gate_title)

        def add_camera_gate_controls(slot, title):
            box = QFrame()
            box.setObjectName("cameraGateBox")
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(16, 14, 16, 14)
            box_layout.setSpacing(10)

            header = QHBoxLayout()
            label = QLabel(title)
            label.setObjectName("cameraGateTitle")
            header.addWidget(label)
            header.addStretch(1)
            box_layout.addLayout(header)

            role = QComboBox()
            role.addItem("ورودی", "entry")
            role.addItem("خروجی", "exit")
            role.addItem("فقط مشاهده", "observe")

            orientation = QComboBox()
            orientation.addItem("عمودی — چپ / راست", "vertical")
            orientation.addItem("افقی — بالا / پایین", "horizontal")

            position = QDoubleSpinBox()
            position.setRange(5.0, 95.0)
            position.setDecimals(1)
            position.setSingleStep(1.0)
            position.setSuffix(" %")

            direction = QComboBox()
            direction.addItem("مثبت", "positive")
            direction.addItem("منفی", "negative")

            cooldown = QSpinBox()
            cooldown.setRange(5, 300)
            cooldown.setSuffix(" فریم")

            fields = [
                ("نقش دوربین", role),
                ("جهت خط", orientation),
                ("موقعیت خط", position),
                ("جهت عبور اصلی", direction),
                ("فاصله ضدتکرار", cooldown),
            ]
            grid = QGridLayout()
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(7)

            for index, (caption, widget) in enumerate(fields):
                row = 0 if index < 3 else 2
                col = index if index < 3 else index - 3

                caption_label = QLabel(caption)
                caption_label.setObjectName("fieldLabel")
                grid.addWidget(caption_label, row, col)
                grid.addWidget(widget, row + 1, col)
                grid.setColumnStretch(col, 1)

            box_layout.addLayout(grid)

            setattr(self, f"settings_camera{slot}_role", role)
            setattr(self, f"settings_camera{slot}_line_orientation", orientation)
            setattr(self, f"settings_camera{slot}_line_position", position)
            setattr(self, f"settings_camera{slot}_line_direction", direction)
            setattr(self, f"settings_camera{slot}_line_cooldown", cooldown)

            gate_layout.addWidget(box)

        add_camera_gate_controls(1, "دوربین ۱")
        add_camera_gate_controls(2, "دوربین ۲")

        gate_info = QLabel(
            "نقش «ورودی» یا «خروجی» تعیین می‌کند همان جهت اصلی عبور، ورود یا خروج ثبت شود. "
            "دوربین‌های دوگانه از همین تنظیمات برای اتصال یک Track بین دو دوربین استفاده می‌کنند."
        )
        gate_info.setObjectName("muted")
        gate_info.setWordWrap(True)
        gate_layout.addWidget(gate_info)
        layout.addWidget(gate_panel)

        global_panel = QFrame()
        global_panel.setObjectName("globalIdentityPanel")
        global_layout = QVBoxLayout(global_panel)
        global_layout.setContentsMargins(18, 16, 18, 16)
        global_layout.setSpacing(12)

        global_title = QLabel("Identity Link بین دوربین‌ها")
        global_title.setObjectName("globalIdentityTitle")
        global_layout.addWidget(global_title)

        self.settings_global_link_enabled = QCheckBox("فعال بودن اتصال بین دوربین‌ها")
        self.settings_global_link_enabled.setChecked(True)
        self.settings_global_link_enabled.setObjectName("globalIdentityCheck")

        self.settings_global_link_window = QDoubleSpinBox()
        self.settings_global_link_window.setRange(1.0, 120.0)
        self.settings_global_link_window.setDecimals(1)
        self.settings_global_link_window.setSingleStep(1.0)
        self.settings_global_link_window.setSuffix(" ثانیه")

        self.settings_global_link_similarity = QDoubleSpinBox()
        self.settings_global_link_similarity.setRange(0.70, 0.97)
        self.settings_global_link_similarity.setDecimals(3)
        self.settings_global_link_similarity.setSingleStep(0.01)

        global_grid = QGridLayout()
        global_grid.setHorizontalSpacing(14)
        global_grid.setVerticalSpacing(6)

        global_grid.addWidget(
            self.settings_global_link_enabled, 0, 0, 2, 1
        )

        window_label = QLabel("پنجره Hand-off")
        window_label.setObjectName("fieldLabel")
        global_grid.addWidget(window_label, 0, 1)
        global_grid.addWidget(self.settings_global_link_window, 1, 1)

        similarity_label = QLabel("حداقل شباهت Re-ID")
        similarity_label.setObjectName("fieldLabel")
        global_grid.addWidget(similarity_label, 0, 2)
        global_grid.addWidget(self.settings_global_link_similarity, 1, 2)

        global_grid.setColumnStretch(0, 2)
        global_grid.setColumnStretch(1, 1)
        global_grid.setColumnStretch(2, 1)
        global_layout.addLayout(global_grid)

        global_info = QLabel(
            "اگر دانش‌آموز قبلاً با چهره شناسایی شده باشد، ظاهر ثبت‌شده‌اش به یک حافظه مشترک "
            "می‌رود. Track ناشناس دوربین دیگر فقط وقتی به او وصل می‌شود که شباهت کافی باشد "
            "و یا دانش‌آموز هنوز حاضر باشد یا Hand-off زمانی معقولی وجود داشته باشد."
        )
        global_info.setObjectName("muted")
        global_info.setWordWrap(True)
        global_layout.addWidget(global_info)
        layout.addWidget(global_panel)

        attendance_panel = QFrame()
        attendance_panel.setObjectName("panel")
        attendance_layout = QVBoxLayout(attendance_panel)

        attendance_title = QLabel("قوانین حضور و غیاب")
        attendance_title.setObjectName("sectionTitle")
        attendance_layout.addWidget(attendance_title)

        attendance_form = QFormLayout()
        self.settings_school_start = QLineEdit()
        self.settings_school_start.setPlaceholderText("08:00")
        self.settings_school_end = QLineEdit()
        self.settings_school_end.setPlaceholderText("15:00")
        self.settings_late_events = QCheckBox(
            "ثبت دیرکرد هنگام ورود بعد از زمان شروع"
        )
        self.settings_early_events = QCheckBox(
            "ثبت خروج زودهنگام قبل از زمان پایان"
        )
        self.settings_late_events.setChecked(True)
        self.settings_early_events.setChecked(True)

        attendance_form.addRow("شروع مدرسه", self.settings_school_start)
        attendance_form.addRow("پایان مدرسه", self.settings_school_end)
        attendance_form.addRow("دیرکرد", self.settings_late_events)
        attendance_form.addRow("خروج زودهنگام", self.settings_early_events)
        attendance_layout.addLayout(attendance_form)
        layout.addWidget(attendance_panel)

        reid_panel = QFrame()
        reid_panel.setObjectName("panel")
        reid_layout = QVBoxLayout(reid_panel)

        reid_title = QLabel("Tracking و Re-ID")
        reid_title.setObjectName("sectionTitle")
        reid_layout.addWidget(reid_title)

        reid_form = QFormLayout()
        self.settings_reid_enabled = QCheckBox("فعال باشد")
        self.settings_reid_enabled.setChecked(True)

        self.settings_reid_device = QComboBox()
        self.settings_reid_device.addItem(
            "خودکار (GPU در صورت وجود)",
            "",
        )
        self.settings_reid_device.addItem("CPU", "cpu")
        self.settings_reid_device.addItem("CUDA / GPU", "cuda")

        self.settings_reid_interval = QSpinBox()
        self.settings_reid_interval.setRange(3, 30)
        self.settings_reid_interval.setSuffix(" فریم")
        self.settings_reid_interval.setToolTip(
            "در CPU حداقل فاصله 10 فریم اعمال می‌شود تا سیستم مدرسه سبک‌تر بماند."
        )

        reid_form.addRow("Re-ID", self.settings_reid_enabled)
        reid_form.addRow("دستگاه", self.settings_reid_device)
        reid_form.addRow(
            "فاصله به‌روزرسانی ظاهر",
            self.settings_reid_interval,
        )
        reid_layout.addLayout(reid_form)
        layout.addWidget(reid_panel)

        self.settings_camera_status = QLabel()
        self.settings_camera_status.setObjectName("muted")
        layout.addWidget(self.settings_camera_status)

        apply_button = QPushButton("ذخیره و راه‌اندازی مجدد دوربین")
        apply_button.clicked.connect(self.apply_camera_settings)
        layout.addWidget(apply_button)

        layout.addStretch(1)
        self.stack.addWidget(page)
        self.load_smart_settings()

    def load_smart_settings(self):
        if not hasattr(self, "settings_camera1_role"):
            return

        current_id = int(get_setting("camera_id", CAMERA_ID))
        camera1_id = int(get_setting("camera_1_id", current_id))
        camera2_id = int(get_setting("camera_2_id", "-1"))
        camera1_enabled = str(
            get_setting("camera_1_enabled", "1") or "1"
        ).lower() in {"1", "true", "yes", "on"}
        camera2_enabled = str(
            get_setting("camera_2_enabled", "0") or "0"
        ).lower() in {"1", "true", "yes", "on"}
        current_fps = int(
            get_setting("camera_target_fps", DEFAULT_CAMERA_TARGET_FPS)
        )

        school_start = str(
            get_setting("school_start_time", "08:00") or "08:00"
        )
        school_end = str(
            get_setting("school_end_time", "15:00") or "15:00"
        )
        late_enabled = str(
            get_setting("enable_late_events", "1") or "1"
        ).lower() in {"1", "true", "yes", "on"}
        early_enabled = str(
            get_setting("enable_early_events", "1") or "1"
        ).lower() in {"1", "true", "yes", "on"}
        reid_enabled = str(
            get_setting("reid_enabled", "1") or "1"
        ).lower() in {"1", "true", "yes", "on"}
        reid_device = str(get_setting("person_device", "") or "")
        reid_interval = int(get_setting("reid_interval", "6"))

        global_enabled = str(
            get_setting("global_identity_enabled", "1") or "1"
        ).lower() in {"1", "true", "yes", "on"}
        global_window = float(
            get_setting("global_identity_window_sec", "15")
        )
        global_similarity = float(
            get_setting("global_identity_similarity", "0.82")
        )

        if self.settings_camera1_combo.count() == 0:
            self.refresh_camera_choices()
        else:
            index = self.settings_camera1_combo.findData(camera1_id)
            if index >= 0:
                self.settings_camera1_combo.setCurrentIndex(index)
            index = self.settings_camera2_combo.findData(camera2_id)
            if index >= 0:
                self.settings_camera2_combo.setCurrentIndex(index)

        self.settings_camera1_enabled.setChecked(camera1_enabled)
        self.settings_camera2_enabled.setChecked(camera2_enabled)

        fps_index = self.settings_fps_combo.findData(current_fps)
        if fps_index >= 0:
            self.settings_fps_combo.setCurrentIndex(fps_index)

        for slot in (1, 2):
            role_default = "entry" if slot == 1 else "exit"
            orientation_default = get_setting(
                "line_orientation" if slot == 1 else "camera_2_line_orientation",
                "vertical",
            )
            position_default = get_setting(
                "line_position" if slot == 1 else "camera_2_line_position",
                "0.50",
            )
            direction_default = get_setting(
                "line_enter_direction" if slot == 1 else "camera_2_line_direction",
                "positive",
            )
            cooldown_default = get_setting(
                "line_cooldown_frames" if slot == 1 else "camera_2_line_cooldown",
                "25",
            )

            role = str(
                get_setting(f"camera_{slot}_role", role_default)
                or role_default
            )
            orientation = str(
                get_setting(
                    f"camera_{slot}_line_orientation",
                    orientation_default,
                )
                or orientation_default
            )
            position = float(
                get_setting(
                    f"camera_{slot}_line_position",
                    position_default,
                )
            ) * 100.0
            direction = str(
                get_setting(
                    f"camera_{slot}_line_direction",
                    direction_default,
                )
                or direction_default
            )
            cooldown = int(
                get_setting(
                    f"camera_{slot}_line_cooldown",
                    cooldown_default,
                )
            )

            role_widget = getattr(self, f"settings_camera{slot}_role")
            orientation_widget = getattr(
                self, f"settings_camera{slot}_line_orientation"
            )
            position_widget = getattr(
                self, f"settings_camera{slot}_line_position"
            )
            direction_widget = getattr(
                self, f"settings_camera{slot}_line_direction"
            )
            cooldown_widget = getattr(
                self, f"settings_camera{slot}_line_cooldown"
            )

            index = role_widget.findData(role)
            if index >= 0:
                role_widget.setCurrentIndex(index)

            index = orientation_widget.findData(orientation)
            if index >= 0:
                orientation_widget.setCurrentIndex(index)

            position_widget.setValue(max(5.0, min(95.0, position)))

            index = direction_widget.findData(direction)
            if index >= 0:
                direction_widget.setCurrentIndex(index)

            cooldown_widget.setValue(max(5, min(300, cooldown)))

        self.settings_school_start.setText(school_start)
        self.settings_school_end.setText(school_end)
        self.settings_late_events.setChecked(late_enabled)
        self.settings_early_events.setChecked(early_enabled)
        self.settings_reid_enabled.setChecked(reid_enabled)

        index = self.settings_reid_device.findData(reid_device)
        self.settings_reid_device.setCurrentIndex(max(0, index))

        self.settings_reid_interval.setValue(
            max(3, min(30, reid_interval))
        )
        self.settings_global_link_enabled.setChecked(global_enabled)
        self.settings_global_link_window.setValue(
            max(1.0, min(120.0, global_window))
        )
        self.settings_global_link_similarity.setValue(
            max(0.70, min(0.97, global_similarity))
        )

    def refresh_camera_choices(self):
        if not hasattr(self, "settings_camera1_combo"):
            return

        camera1_id = int(
            get_setting(
                "camera_1_id",
                get_setting("camera_id", CAMERA_ID),
            )
        )
        camera2_id = int(get_setting("camera_2_id", "-1"))

        was_running = bool(self.camera_workers)
        if was_running:
            self.stop_camera()

        try:
            cameras = _discover_cameras()
        finally:
            if was_running:
                self.start_camera()

        for combo, selected_id in (
            (self.settings_camera1_combo, camera1_id),
            (self.settings_camera2_combo, camera2_id),
        ):
            combo.blockSignals(True)
            combo.clear()

            for camera in cameras:
                combo.addItem(camera["label"], camera["id"])

            if not cameras:
                combo.addItem("هیچ دوربینی پیدا نشد", None)
                combo.setEnabled(False)
            else:
                combo.setEnabled(True)
                index = combo.findData(selected_id)
                if index >= 0:
                    combo.setCurrentIndex(index)

            combo.blockSignals(False)

        self.settings_camera_status.setText(
            f"{len(cameras)} دوربین پیدا شد • دوربین ۲ در صورت فعال‌سازی باید Device جدا داشته باشد."
        )

    def apply_camera_settings(self):
        camera1_id = self.settings_camera1_combo.currentData()
        camera2_id = self.settings_camera2_combo.currentData()

        if self.settings_camera1_enabled.isChecked() and camera1_id is None:
            QMessageBox.warning(
                self,
                "دوربین ۱ یافت نشد",
                "برای فعال بودن دوربین ۱ یک Device معتبر انتخاب کنید.",
            )
            return

        if self.settings_camera2_enabled.isChecked() and camera2_id is None:
            QMessageBox.warning(
                self,
                "دوربین ۲ یافت نشد",
                "برای فعال بودن دوربین ۲ یک Device معتبر انتخاب کنید.",
            )
            return

        try:
            camera1_id = int(camera1_id) if camera1_id is not None else -1
            camera2_id = int(camera2_id) if camera2_id is not None else -1
            target_fps = int(self.settings_fps_combo.currentData())
            camera_gate_values = {}
            for slot in (1, 2):
                camera_gate_values[slot] = {
                    "role": getattr(
                        self, f"settings_camera{slot}_role"
                    ).currentData(),
                    "line_orientation": getattr(
                        self, f"settings_camera{slot}_line_orientation"
                    ).currentData(),
                    "line_position": (
                        float(
                            getattr(
                                self,
                                f"settings_camera{slot}_line_position",
                            ).value()
                        )
                        / 100.0
                    ),
                    "line_direction": getattr(
                        self, f"settings_camera{slot}_line_direction"
                    ).currentData(),
                    "line_cooldown": int(
                        getattr(
                            self,
                            f"settings_camera{slot}_line_cooldown",
                        ).value()
                    ),
                }

            reid_interval = int(self.settings_reid_interval.value())
            global_window = float(self.settings_global_link_window.value())
            global_similarity = float(
                self.settings_global_link_similarity.value()
            )
            school_start = self.settings_school_start.text().strip()
            school_end = self.settings_school_end.text().strip()
            datetime.strptime(school_start, "%H:%M")
            datetime.strptime(school_end, "%H:%M")
        except (TypeError, ValueError):
            QMessageBox.warning(
                self,
                "تنظیمات نامعتبر",
                "زمان‌ها باید به شکل HH:MM باشند و مقادیر انتخاب‌شده معتبر باشند.",
            )
            return

        if (
            self.settings_camera1_enabled.isChecked()
            and self.settings_camera2_enabled.isChecked()
            and camera1_id == camera2_id
        ):
            QMessageBox.warning(
                self,
                "دوربین تکراری",
                "دوربین ۱ و دوربین ۲ نمی‌توانند یک Device یکسان داشته باشند.",
            )
            return

        set_setting("camera_id", camera1_id if camera1_id >= 0 else CAMERA_ID)
        set_setting("camera_target_fps", target_fps)
        set_setting("camera_1_id", camera1_id)
        set_setting("camera_2_id", camera2_id)
        set_setting(
            "camera_1_enabled",
            int(self.settings_camera1_enabled.isChecked()),
        )
        set_setting(
            "camera_2_enabled",
            int(self.settings_camera2_enabled.isChecked()),
        )
        set_setting("camera_1_target_fps", target_fps)
        set_setting("camera_2_target_fps", target_fps)

        for slot, values in camera_gate_values.items():
            set_setting(f"camera_{slot}_role", values["role"])
            set_setting(
                f"camera_{slot}_line_orientation",
                values["line_orientation"],
            )
            set_setting(
                f"camera_{slot}_line_position",
                f"{values['line_position']:.4f}",
            )
            set_setting(
                f"camera_{slot}_line_direction",
                values["line_direction"],
            )
            set_setting(
                f"camera_{slot}_line_cooldown",
                values["line_cooldown"],
            )

        # Keep the original single-camera keys compatible with older builds.
        set_setting(
            "line_orientation",
            camera_gate_values[1]["line_orientation"],
        )
        set_setting(
            "line_position",
            f"{camera_gate_values[1]['line_position']:.4f}",
        )
        set_setting(
            "line_enter_direction",
            camera_gate_values[1]["line_direction"],
        )
        set_setting("line_cooldown_frames", camera_gate_values[1]["line_cooldown"])
        set_setting("school_start_time", school_start)
        set_setting("school_end_time", school_end)
        set_setting(
            "enable_late_events",
            int(self.settings_late_events.isChecked()),
        )
        set_setting(
            "enable_early_events",
            int(self.settings_early_events.isChecked()),
        )
        set_setting(
            "reid_enabled",
            int(self.settings_reid_enabled.isChecked()),
        )
        set_setting(
            "person_device",
            self.settings_reid_device.currentData() or "",
        )
        set_setting("reid_interval", reid_interval)
        set_setting(
            "global_identity_enabled",
            int(self.settings_global_link_enabled.isChecked()),
        )
        set_setting("global_identity_window_sec", f"{global_window:.1f}")
        set_setting("global_identity_similarity", f"{global_similarity:.3f}")

        try:
            self.restart_camera()
        except Exception as exc:
            QMessageBox.critical(
                self,
                "راه‌اندازی دوربین ناموفق بود",
                str(exc),
            )
            return

        enabled_slots = [
            slot
            for slot, enabled in (
                (1, self.settings_camera1_enabled.isChecked()),
                (2, self.settings_camera2_enabled.isChecked()),
            )
            if enabled
        ]
        role_labels = {
            "entry": "ورودی",
            "exit": "خروجی",
            "observe": "مشاهده",
        }
        role_summary = " / ".join(
            f"دوربین {slot}: {role_labels.get(camera_gate_values[slot]['role'], camera_gate_values[slot]['role'])}"
            for slot in enabled_slots
        ) or "بدون دوربین"
        self.settings_camera_status.setText(
            f"تنظیمات ذخیره شد • {len(enabled_slots)} دوربین فعال • "
            f"{role_summary} • Global Link {'فعال' if self.settings_global_link_enabled.isChecked() else 'خاموش'}"
        )

    def start_camera(self):
        self._camera_supervisor_enabled = True
        if self.camera_workers and any(
            worker.isRunning() for worker in self.camera_workers.values()
        ):
            return

        enabled_slots = []
        for slot in (1, 2):
            enabled = str(
                get_setting(
                    f"camera_{slot}_enabled",
                    "1" if slot == 1 else "0",
                )
                or "0"
            ).lower() in {"1", "true", "yes", "on"}

            default_id = (
                get_setting("camera_id", CAMERA_ID)
                if slot == 1
                else -1
            )
            camera_id = int(
                get_setting(f"camera_{slot}_id", default_id)
            )

            if not enabled or camera_id < 0:
                continue

            enabled_slots.append(slot)

            target_fps = int(
                get_setting(
                    f"camera_{slot}_target_fps",
                    get_setting(
                        "camera_target_fps",
                        DEFAULT_CAMERA_TARGET_FPS,
                    ),
                )
            )

            # With two USB2 cameras active, 30 FPS capture preserves the
            # full 640x480 spatial quality while avoiding a needless 60 FPS
            # transfer rate that the CPU analysis pipeline cannot consume.
            if len(
                [
                    s for s in (1, 2)
                    if str(
                        get_setting(
                            f"camera_{s}_enabled",
                            "1" if s == 1 else "0",
                        )
                        or "0"
                    ).strip().lower()
                    in {"1", "true", "yes", "on"}
                ]
            ) >= 2:
                target_fps = min(target_fps, self._dual_camera_fps_cap)

            role = str(
                get_setting(
                    f"camera_{slot}_role",
                    "entry" if slot == 1 else "exit",
                )
                or ("entry" if slot == 1 else "exit")
            )
            line_orientation = str(
                get_setting(
                    f"camera_{slot}_line_orientation",
                    get_setting("line_orientation", "vertical"),
                )
                or "vertical"
            )
            line_position = float(
                get_setting(
                    f"camera_{slot}_line_position",
                    get_setting("line_position", "0.50"),
                )
            )
            line_direction = str(
                get_setting(
                    f"camera_{slot}_line_direction",
                    get_setting(
                        "line_enter_direction",
                        "positive",
                    ),
                )
                or "positive"
            )
            line_cooldown = int(
                get_setting(
                    f"camera_{slot}_line_cooldown",
                    get_setting("line_cooldown_frames", "25"),
                )
            )

            worker = SmartCameraWorker(
                camera_slot=slot,
                camera_config={
                    "camera_id": camera_id,
                    "target_fps": target_fps,
                    "role": role,
                    "line_orientation": line_orientation,
                    "line_position": line_position,
                    "line_direction": line_direction,
                    "line_cooldown": line_cooldown,
                    # Both installed ov534 cameras share USB2. Native 8-bit
                    # Bayer halves the video payload while retaining 640x480.
                    "raw_bayer": len(enabled_slots) >= 2
                    and sys.platform.startswith("linux"),
                },
                presence_state=self.presence_state,
                identity_manager=self.global_identity_manager,
            )
            worker.frame_ready.connect(
                self._store_camera_preview,
                Qt.DirectConnection,
            )
            worker.attendance_added.connect(self.attendance_event)
            worker.status_changed.connect(
                lambda message, s=slot: self.update_camera_status(
                    f"Camera {s}: {message}"
                )
            )
            worker.error.connect(
                lambda message, s=slot: self.camera_error(
                    f"Camera {s}: {message}"
                )
            )
            worker.finished.connect(
                lambda s=slot, w=worker: self._camera_worker_finished(s, w)
            )
            worker.start()
            self.camera_workers[slot] = worker

        self.camera_worker = self.camera_workers.get(1)
        if not self._preview_timer.isActive():
            self._preview_timer.start()

        if hasattr(self, "camera_previews") and 2 not in self.camera_workers:
            self.camera_previews[2].setText("Camera 2 غیرفعال است.")

    def _camera_worker_finished(self, slot, worker):
        """Restart the whole camera pipeline after an unexpected worker stop."""
        if self.camera_workers.get(slot) is not worker:
            return

        self.camera_workers.pop(slot, None)
        if self.camera_worker is worker:
            self.camera_worker = None

        if not self._camera_supervisor_enabled or self._shutting_down:
            return

        self.camera_error(
            f"Camera {slot}: worker stopped unexpectedly; restarting cameras."
        )
        if self._camera_restart_pending:
            return

        self._camera_restart_pending = True

        def restart():
            self._camera_restart_pending = False
            if self._shutting_down or not self._camera_supervisor_enabled:
                return
            self._camera_supervisor_enabled = False
            self.stop_camera()
            self._camera_supervisor_enabled = True
            self.start_camera()

        QTimer.singleShot(1500, restart)

    def stop_camera(self):
        self._camera_supervisor_enabled = False
        workers = list(self.camera_workers.values())
        for worker in workers:
            if worker.isRunning():
                worker.stop()
                worker.wait(5000)

        self.camera_workers.clear()
        self.camera_worker = None

    def restart_camera(self):
        self.stop_camera()
        self._camera_supervisor_enabled = True
        self.start_camera()

    def _store_camera_preview(self, camera_slot, image):
        # This slot is deliberately a DirectConnection: it does not touch any
        # QWidget. It only replaces a single latest-value mailbox while holding
        # a small Python lock. The GUI thread consumes that mailbox on a timer.
        with self._latest_camera_images_lock:
            self._latest_camera_images[int(camera_slot)] = image

    def _render_latest_camera_previews(self):
        with self._latest_camera_images_lock:
            latest = dict(self._latest_camera_images)

        previews = getattr(self, "camera_previews", {})
        fallback = getattr(self, "camera_preview", None)

        for camera_slot, image in latest.items():
            preview = previews.get(int(camera_slot), fallback)
            if preview is None or image is None:
                continue

            pixmap = QPixmap.fromImage(image)
            preview.setPixmap(
                pixmap.scaled(
                    preview.size(),
                    Qt.KeepAspectRatio,
                    Qt.FastTransformation,
                )
            )

    def update_camera_preview(self, camera_slot, image):
        # Backwards-compatible entry point for callers outside the new mailbox.
        self._store_camera_preview(camera_slot, image)

    def update_camera_status(self, message):
        self.camera_status.setText(message)

    def camera_error(self, message):
        self.camera_status.setText(f"خطای دوربین: {message}")

    def attendance_event(self, event):
        self.refresh_dashboard()
        if self.current_page == 4:
            self.refresh_attendance()

        self.camera_status.setText(
            f"ثبت شد: {event['name']} • {event['time']}"
        )

    def refresh_all(self):
        self.refresh_dashboard()
        self.refresh_students()
        self.refresh_classes()
        self.refresh_majors()
        self.refresh_attendance_filters()
        self.refresh_attendance()
        self.refresh_report_filters()
        self.refresh_report()

    def refresh_dashboard(self):
        total, present, absent, late, excused = get_attendance_stats()

        self.total_students_label.setText(str(total))
        self.present_label.setText(str(present))
        self.absent_label.setText(str(absent))
        self.late_label.setText(str(late))

        recent = get_recent_attendance(12)
        self.recent_table.setRowCount(len(recent))
        for row, (code, name, timestamp, class_name, status) in enumerate(recent):
            self.recent_table.setItem(row, 0, QTableWidgetItem(f"{name} ({code})"))
            self.recent_table.setItem(row, 1, QTableWidgetItem(class_name or "بدون کلاس"))
            self.recent_table.setItem(row, 2, QTableWidgetItem(timestamp.split(" ")[-1]))
            self.recent_table.setItem(row, 3, QTableWidgetItem(self.status_label(status)))

        classes = list_classes(
            get_active_academic_year()[0]
            if get_active_academic_year()
            else None
        )
        self.dashboard_classes.setRowCount(len(classes))
        for row, (
            class_id,
            class_name,
            grade,
            major_name,
            year_id,
            year_name,
            student_count,
        ) in enumerate(classes):
            class_rows = get_attendance(
                class_id=class_id,
            )
            class_total = len(class_rows)
            class_present = sum(r[5] == "present" for r in class_rows)
            class_late = sum(r[5] == "late" for r in class_rows)
            percent = (
                ((class_present + class_late) / class_total) * 100
                if class_total
                else 0
            )

            vals = [
                class_name,
                major_name or "—",
                str(class_total),
                str(class_present + class_late),
                f"{percent:.1f}%",
            ]
            for col, value in enumerate(vals):
                self.dashboard_classes.setItem(
                    row,
                    col,
                    QTableWidgetItem(value),
                )

        today_rows = get_attendance()
        absent_rows = [r for r in today_rows if r[5] == "absent"]
        self.dashboard_absent.setRowCount(len(absent_rows))
        for row, record in enumerate(absent_rows):
            _, code, name, timestamp, camera, status, notes, class_name, major_name = record
            vals = [name, class_name or "بدون کلاس", major_name or "—"]
            for col, value in enumerate(vals):
                self.dashboard_absent.setItem(
                    row,
                    col,
                    QTableWidgetItem(value),
                )

    def status_label(self, status):
        return {
            "present": "حاضر",
            "late": "دیرکرد",
            "excused": "موجه",
            "absent": "غایب",
        }.get(status, "غایب")

    def refresh_students(self):
        rows = list_students()
        self.students_table.setRowCount(len(rows))

        for row, (
            student_id,
            code,
            name,
            face_count,
            created_at,
            class_name,
            major_name,
            grade,
        ) in enumerate(rows):
            values = [
                str(student_id),
                code,
                name,
                grade or "—",
                major_name or "—",
                class_name or "بدون کلاس",
                str(face_count),
                created_at,
            ]
            for col, value in enumerate(values):
                self.students_table.setItem(row, col, QTableWidgetItem(value))

        self.filter_students_table(self.student_search.text())

    def filter_students_table(self, text):
        text = text.strip().lower()
        for row in range(self.students_table.rowCount()):
            code = self.students_table.item(row, 1)
            name = self.students_table.item(row, 2)
            haystack = " ".join(
                item.text() if item else ""
                for item in (code, name)
            ).lower()
            self.students_table.setRowHidden(row, text not in haystack)

    def selected_student_id(self):
        row = self.students_table.currentRow()
        item = self.students_table.item(row, 0) if row >= 0 else None
        if item is None:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def add_student_dialog(self):
        self.stop_camera()
        dialog = EnrollmentDialog(show_student_fields=True, parent=self)

        try:
            if dialog.exec() != QDialog.Accepted:
                return

            try:
                student_id = add_student(
                    dialog.code_edit.text().strip(),
                    dialog.name_edit.text().strip(),
                    dialog.class_combo.currentData(),
                )
                for embedding in dialog.embeddings:
                    add_embedding(student_id, embedding)
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "افزودن دانش‌آموز ناموفق بود",
                    str(exc),
                )
                return

            QMessageBox.information(
                self,
                "انجام شد",
                f"{dialog.name_edit.text().strip()} با {len(dialog.embeddings)} نمونه چهره ثبت شد.",
            )
            self.refresh_all()
        finally:
            self.start_camera()

    def edit_selected_student(self):
        student_id = self.selected_student_id()
        if student_id is None:
            QMessageBox.information(self, "انتخاب دانش‌آموز", "ابتدا یک دانش‌آموز را انتخاب کنید.")
            return

        student = get_student(student_id)
        if student is None:
            return

        _, old_code, old_name, _ = student
        dialog = QDialog(self)
        dialog.setWindowTitle("ویرایش دانش‌آموز")
        dialog.resize(520, 270)

        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        code_edit = QLineEdit(old_code)
        name_edit = QLineEdit(old_name)
        class_combo = QComboBox()
        class_combo.addItem("بدون کلاس", None)

        active = get_active_academic_year()
        current_class = get_student_class(student_id)

        if active:
            for item in list_classes(active[0]):
                class_id, class_name, grade, major_name, year_id, year_name, count = item
                label = f"{class_name} — پایه {grade}"
                if major_name:
                    label += f" — {major_name}"
                class_combo.addItem(label, class_id)

        if current_class:
            index = class_combo.findData(current_class[0])
            if index >= 0:
                class_combo.setCurrentIndex(index)

        form.addRow("کد دانش‌آموزی", code_edit)
        form.addRow("نام", name_edit)
        form.addRow("کلاس", class_combo)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch()
        save = QPushButton("ذخیره")
        cancel = QPushButton("لغو")
        save.clicked.connect(dialog.accept)
        cancel.clicked.connect(dialog.reject)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        if not code_edit.text().strip() or not name_edit.text().strip():
            QMessageBox.warning(self, "اطلاعات ناقص", "کد و نام الزامی هستند.")
            return

        try:
            selected_class = class_combo.currentData()
            if selected_class is None and current_class:
                # No-class is represented by leaving the current enrollment untouched in
                # older records; users can assign another class from the next edit.
                update_student(student_id, code_edit.text(), name_edit.text())
            else:
                update_student(
                    student_id,
                    code_edit.text(),
                    name_edit.text(),
                    selected_class,
                )

            self.refresh_all()
            self.restart_camera()
        except Exception as exc:
            QMessageBox.critical(self, "ویرایش ناموفق بود", str(exc))

    def reenroll_selected_student(self):
        student_id = self.selected_student_id()
        if student_id is None:
            QMessageBox.information(self, "انتخاب دانش‌آموز", "ابتدا یک دانش‌آموز را انتخاب کنید.")
            return

        student = get_student(student_id)
        if student is None:
            return

        name = student[2]
        if not confirm_action(
            self,
            "ثبت دوباره چهره",
            f"نمونه‌های چهره {name} جایگزین شوند؟",
            "جایگزین کن",
            "لغو",
        ):
            return

        self.stop_camera()
        dialog = EnrollmentDialog(
            show_student_fields=False,
            student_id=student_id,
            parent=self,
        )

        try:
            if dialog.exec() != QDialog.Accepted:
                return

            delete_embeddings(student_id)
            for embedding in dialog.embeddings:
                add_embedding(student_id, embedding)

            QMessageBox.information(
                self,
                "انجام شد",
                f"{len(dialog.embeddings)} نمونه جدید برای {name} ذخیره شد.",
            )
            self.refresh_all()
        finally:
            self.start_camera()

    def delete_selected_student(self):
        student_id = self.selected_student_id()
        if student_id is None:
            QMessageBox.information(self, "انتخاب دانش‌آموز", "ابتدا یک دانش‌آموز را انتخاب کنید.")
            return

        student = get_student(student_id)
        if student is None:
            return

        name = student[2]
        code = student[1]
        if not confirm_action(
            self,
            "حذف دانش‌آموز",
            f"{name} ({code}) حذف شود؟\n\nسوابق چهره و حضور او نیز حذف می‌شود.",
            "حذف",
            "لغو",
        ):
            return

        try:
            delete_student(student_id)
            self.refresh_all()
            self.restart_camera()
        except Exception as exc:
            QMessageBox.critical(self, "حذف ناموفق بود", str(exc))

    def refresh_year_combo(self):
        current = self.class_year_combo.currentData()
        active = get_active_academic_year()

        self.class_year_combo.blockSignals(True)
        self.class_year_combo.clear()

        for year_id, name, start_date, end_date, is_active in list_academic_years():
            label = name
            if start_date and end_date:
                label += f" • {start_date} تا {end_date}"
            if is_active:
                label += " • فعال"
            self.class_year_combo.addItem(label, year_id)

        target = current
        if target is None and active:
            target = active[0]

        if target is not None:
            index = self.class_year_combo.findData(target)
            if index >= 0:
                self.class_year_combo.setCurrentIndex(index)

        self.class_year_combo.blockSignals(False)

    def open_school_calendar_dialog(self):
        year_id = self.class_year_combo.currentData()
        if not year_id:
            active = get_active_academic_year()
            year_id = active[0] if active else None

        if not year_id:
            QMessageBox.information(
                self,
                "تقویم مدرسه",
                "ابتدا یک سال تحصیلی بسازید.",
            )
            return

        dialog = SchoolCalendarDialog(
            selected_year_id=year_id,
            parent=self,
        )
        dialog.exec()
        self.refresh_all()

    def add_academic_year_dialog(self):
        dialog = AcademicYearDialog(parent=self)

        if dialog.exec() != QDialog.Accepted:
            return

        start_date, end_date = dialog.values()

        make_active = confirm_action(
            self,
            "فعال‌سازی سال تحصیلی",
            f"سال {start_date[:4]}-{end_date[:4]} به‌عنوان سال فعال انتخاب شود؟",
            "فعال کن",
            "فعلاً نه",
        )

        try:
            year_id = add_academic_year(
                start_date,
                end_date,
                active=make_active,
            )

            self.refresh_all()

            index = self.class_year_combo.findData(year_id)
            if index >= 0:
                self.class_year_combo.setCurrentIndex(index)

        except Exception as exc:
            QMessageBox.critical(
                self,
                "افزودن سال ناموفق بود",
                str(exc),
            )

    def edit_academic_year_dialog(self):
        year_id = self.class_year_combo.currentData()
        if not year_id:
            QMessageBox.information(
                self,
                "انتخاب سال",
                "ابتدا یک سال تحصیلی را انتخاب کنید.",
            )
            return

        existing = next(
            (
                item
                for item in list_academic_years()
                if item[0] == year_id
            ),
            None,
        )

        if existing is None:
            return

        dialog = AcademicYearDialog(existing=existing, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return

        start_date, end_date = dialog.values()

        try:
            update_academic_year(
                year_id,
                start_date,
                end_date,
            )
            self.refresh_all()
        except Exception as exc:
            QMessageBox.critical(
                self,
                "ویرایش سال ناموفق بود",
                str(exc),
            )

    def set_active_year(self):
        year_id = self.class_year_combo.currentData()
        if not year_id:
            return
        try:
            set_active_academic_year(year_id)
            self.refresh_all()
        except Exception as exc:
            QMessageBox.critical(self, "تغییر سال ناموفق بود", str(exc))

    def open_school_calendar_dialog(self):
        year_id = self.class_year_combo.currentData()

        if not year_id:
            active = get_active_academic_year()
            year_id = active[0] if active else None

        if not year_id:
            QMessageBox.information(
                self,
                "تقویم مدرسه",
                "ابتدا یک سال تحصیلی بسازید.",
            )
            return

        dialog = SchoolCalendarDialog(
            selected_year_id=year_id,
            parent=self,
        )
        dialog.exec()
        self.refresh_all()

    def selected_class_id(self):
        row = self.classes_table.currentRow()
        item = self.classes_table.item(row, 0) if row >= 0 else None
        if item is None:
            return None
        return int(item.text())

    def add_class_dialog(self):
        years = list_academic_years()
        majors = list_majors()
        if not years:
            QMessageBox.warning(self, "سال تحصیلی", "ابتدا یک سال تحصیلی بسازید.")
            return

        dialog = ClassDialog(years, majors, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return

        name, grade, major_id, year_id = dialog.values()
        if not name:
            QMessageBox.warning(self, "اطلاعات ناقص", "نام کلاس الزامی است.")
            return

        try:
            existing = next(
                (
                    item
                    for item in list_classes(year_id)
                    if item[1].strip().casefold() == name.casefold()
                ),
                None,
            )

            if existing is not None:
                QMessageBox.warning(
                    self,
                    "کلاس تکراری",
                    f"کلاس «{name}» در این سال تحصیلی از قبل وجود دارد.",
                )
                return

            class_id = add_class(
                name,
                grade,
                major_id,
                year_id,
            )

            self.refresh_all()

            QMessageBox.information(
                self,
                "کلاس اضافه شد",
                f"«{name}» با موفقیت ثبت شد.",
            )

            index = self.classes_table.findItems(
                name,
                Qt.MatchExactly,
            )
            if index:
                self.classes_table.selectRow(
                    index[0].row()
                )

            return class_id

        except Exception as exc:
            message = str(exc)

            if "UNIQUE constraint failed" in message:
                message = "این نام کلاس برای این سال تحصیلی قبلاً ثبت شده است."

            QMessageBox.critical(
                self,
                "افزودن کلاس ناموفق بود",
                message,
            )

    def edit_selected_class(self):
        class_id = self.selected_class_id()
        if class_id is None:
            QMessageBox.information(self, "انتخاب کلاس", "ابتدا یک کلاس را انتخاب کنید.")
            return

        rows = list_classes()
        existing = next((row for row in rows if row[0] == class_id), None)
        if existing is None:
            return

        dialog = ClassDialog(list_academic_years(), list_majors(), existing, self)
        if dialog.exec() != QDialog.Accepted:
            return

        name, grade, major_id, year_id = dialog.values()
        try:
            update_class(class_id, name, grade, major_id, year_id)
            self.refresh_all()
        except Exception as exc:
            QMessageBox.critical(self, "ویرایش کلاس ناموفق بود", str(exc))

    def delete_selected_class(self):
        class_id = self.selected_class_id()
        if class_id is None:
            QMessageBox.information(self, "انتخاب کلاس", "ابتدا یک کلاس را انتخاب کنید.")
            return

        if not confirm_action(
            self,
            "حذف کلاس",
            "کلاس حذف شود؟ فقط کلاس‌های خالی قابل حذف هستند.",
            "حذف",
            "لغو",
        ):
            return

        try:
            delete_class(class_id)
            self.refresh_all()
        except Exception as exc:
            QMessageBox.critical(self, "حذف کلاس ناموفق بود", str(exc))

    def refresh_classes(self):
        self.refresh_year_combo()
        year_id = self.class_year_combo.currentData()
        rows = list_classes(year_id)
        self.classes_table.setRowCount(len(rows))

        active = get_active_academic_year()
        active_id = active[0] if active else None

        for row, item in enumerate(rows):
            class_id, name, grade, major_name, year_id, year_name, count = item
            status = "فعال" if year_id == active_id else ""
            values = [
                str(class_id),
                name,
                grade or "—",
                major_name or "—",
                year_name,
                str(count),
                status,
            ]
            for col, value in enumerate(values):
                self.classes_table.setItem(row, col, QTableWidgetItem(value))

    def selected_major_id(self):
        row = self.majors_table.currentRow()
        item = self.majors_table.item(row, 0) if row >= 0 else None
        if item is None:
            return None
        return int(item.text())

    def add_major_dialog(self):
        name, ok = QInputDialog.getText(self, "افزودن رشته", "نام رشته:")
        if not ok or not name.strip():
            return
        try:
            add_major(name)
            self.refresh_all()
        except Exception as exc:
            QMessageBox.critical(self, "افزودن رشته ناموفق بود", str(exc))

    def edit_selected_major(self):
        major_id = self.selected_major_id()
        if major_id is None:
            QMessageBox.information(self, "انتخاب رشته", "ابتدا یک رشته را انتخاب کنید.")
            return

        rows = list_majors()
        existing = next((row for row in rows if row[0] == major_id), None)
        if not existing:
            return

        name, ok = QInputDialog.getText(
            self,
            "ویرایش رشته",
            "نام رشته:",
            text=existing[1],
        )
        if not ok or not name.strip():
            return

        try:
            update_major(major_id, name)
            self.refresh_all()
        except Exception as exc:
            QMessageBox.critical(self, "ویرایش رشته ناموفق بود", str(exc))

    def delete_selected_major(self):
        major_id = self.selected_major_id()
        if major_id is None:
            QMessageBox.information(self, "انتخاب رشته", "ابتدا یک رشته را انتخاب کنید.")
            return

        if not confirm_action(
            self,
            "حذف رشته",
            "رشته حذف شود؟ اگر به کلاسی متصل باشد حذف نمی‌شود.",
            "حذف",
            "لغو",
        ):
            return

        try:
            delete_major(major_id)
            self.refresh_all()
        except Exception as exc:
            QMessageBox.critical(self, "حذف رشته ناموفق بود", str(exc))

    def refresh_majors(self):
        rows = list_majors()
        self.majors_table.setRowCount(len(rows))
        for row, (major_id, name, created_at) in enumerate(rows):
            values = [str(major_id), name, created_at]
            for col, value in enumerate(values):
                self.majors_table.setItem(row, col, QTableWidgetItem(value))

    def populate_major_combo(self, combo):
        combo.blockSignals(True)
        current = combo.currentData()
        combo.clear()
        combo.addItem("همه رشته‌ها", None)
        for major_id, name, _ in list_majors():
            combo.addItem(name, major_id)
        if current is not None:
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def populate_class_combo(self, combo, major_id=None):
        combo.blockSignals(True)
        current = combo.currentData()
        combo.clear()
        combo.addItem("همه کلاس‌ها", None)

        active = get_active_academic_year()
        if active:
            for item in list_classes(active[0]):
                class_id, class_name, grade, major_name, year_id, year_name, count = item
                if major_id and (not major_name or not self.major_id_for_class(class_id, major_id)):
                    continue
                label = f"{class_name} — پایه {grade}"
                if major_name:
                    label += f" — {major_name}"
                combo.addItem(label, class_id)

        if current is not None:
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def major_id_for_class(self, class_id, major_id):
        for item in list_classes():
            if item[0] == class_id:
                name = item[3]
                for mid, major_name, _ in list_majors():
                    if mid == major_id:
                        return name == major_name
        return False

    def refresh_attendance_filters(self):
        self.populate_major_combo(self.attendance_major_combo)
        self.populate_class_combo(
            self.attendance_class_combo,
            self.attendance_major_combo.currentData(),
        )

    def on_attendance_major_changed(self):
        self.populate_class_combo(
            self.attendance_class_combo,
            self.attendance_major_combo.currentData(),
        )
        self.refresh_attendance()

    def refresh_attendance(self):
        date = self.attendance_date.date().toString("yyyy-MM-dd")
        major_id = self.attendance_major_combo.currentData()
        class_id = self.attendance_class_combo.currentData()

        rows = get_attendance(date, class_id, major_id)
        self.attendance_table.setRowCount(len(rows))

        for row, record in enumerate(rows):
            student_id, code, name, timestamp, camera, status, notes, class_name, major_name = record
            values = [
                str(student_id),
                code,
                name,
                class_name or "بدون کلاس",
                self.status_label(status),
                timestamp.split(" ")[-1] if timestamp else "—",
                camera or "—",
                notes or "",
                "ثبت دستی" if camera == "manual" else "دوربین",
            ]
            for col, value in enumerate(values):
                self.attendance_table.setItem(row, col, QTableWidgetItem(value))

        total, present, absent, late, excused = get_attendance_stats(
            date,
            class_id,
            major_id,
        )
        self.attendance_summary.setText(
            f"کل: {total} • حاضر: {present} • دیرکرد: {late} • موجه: {excused} • غایب: {absent}"
        )

    def selected_attendance_student_id(self):
        row = self.attendance_table.currentRow()
        item = self.attendance_table.item(row, 0) if row >= 0 else None
        if item is None:
            return None
        return int(item.text())

    def edit_selected_attendance(self):
        student_id = self.selected_attendance_student_id()
        if student_id is None:
            QMessageBox.information(self, "انتخاب دانش‌آموز", "ابتدا یک دانش‌آموز را انتخاب کنید.")
            return

        row = self.attendance_table.currentRow()
        current_status = {
            "حاضر": "present",
            "دیرکرد": "late",
            "موجه": "excused",
            "غایب": "absent",
        }.get(self.attendance_table.item(row, 4).text(), "absent")
        notes = self.attendance_table.item(row, 7).text() if self.attendance_table.item(row, 7) else ""

        name = self.attendance_table.item(row, 2).text()
        dialog = ManualAttendanceDialog(name, current_status, notes, self)
        if dialog.exec() != QDialog.Accepted:
            return

        status, notes = dialog.values()
        date = self.attendance_date.date().toString("yyyy-MM-dd")

        try:
            set_attendance_status(
                student_id,
                date,
                status,
                notes,
            )
            self.refresh_dashboard()
            self.refresh_attendance()
        except Exception as exc:
            QMessageBox.critical(self, "ویرایش حضور ناموفق بود", str(exc))

    def refresh_report_filters(self):
        self.populate_major_combo(self.report_major_combo)
        self.populate_class_combo(
            self.report_class_combo,
            self.report_major_combo.currentData(),
        )

    def on_report_major_changed(self):
        self.populate_class_combo(
            self.report_class_combo,
            self.report_major_combo.currentData(),
        )
        self.refresh_report()

    def set_report_range(self, days_back):
        end = QDate.currentDate()
        start = end.addDays(-days_back)

        self.report_start.blockSignals(True)
        self.report_end.blockSignals(True)
        self.report_start.setDate(start)
        self.report_end.setDate(end)
        self.report_start.blockSignals(False)
        self.report_end.blockSignals(False)

        self.refresh_report()

    def refresh_report(self):
        start = self.report_start.date().toString("yyyy-MM-dd")
        end = self.report_end.date().toString("yyyy-MM-dd")

        if start > end:
            QMessageBox.warning(
                self,
                "بازه نامعتبر",
                "تاریخ شروع باید قبل از تاریخ پایان باشد.",
            )
            return

        class_id = self.report_class_combo.currentData()
        major_id = self.report_major_combo.currentData()

        rows = get_report_rows(
            start,
            end,
            class_id,
            major_id,
        )

        self.report_table.setRowCount(len(rows))

        total_present = 0
        total_late = 0
        total_absent = 0
        total_excused = 0

        for row, record in enumerate(rows):
            (
                student_id,
                code,
                name,
                grade,
                major_name,
                class_name,
                present,
                late,
                absent,
                excused,
                percent,
            ) = record

            total_present += present
            total_late += late
            total_absent += absent
            total_excused += excused

            values = [
                str(student_id),
                code,
                name,
                grade,
                major_name or "—",
                class_name or "بدون کلاس",
                str(present),
                str(late),
                str(absent),
                str(excused),
                f"{percent:.1f}٪",
            ]

            for col, value in enumerate(values):
                self.report_table.setItem(
                    row,
                    col,
                    QTableWidgetItem(value),
                )

        avg = (
            sum(record[10] for record in rows) / len(rows)
            if rows
            else 0.0
        )

        daily = get_daily_report_summary(
            start,
            end,
            class_id,
            major_id,
        )

        class_summary = get_class_report_summary(
            start,
            end,
            major_id,
            class_id,
        )

        self.report_trend_chart.set_data(daily)
        self.report_class_chart.set_data(
            sorted(
                class_summary,
                key=lambda item: item[8],
                reverse=True,
            )
        )
        self.report_status_chart.set_data(
            {
                "present": total_present,
                "late": total_late,
                "absent": total_absent,
                "excused": total_excused,
            }
        )

        self.report_total_label.setText(str(len(rows)))
        self.report_avg_label.setText(f"{avg:.1f}٪")
        self.report_absent_days_label.setText(str(total_absent))
        self.report_late_days_label.setText(str(total_late))

        active_days = len(daily)
        day_note = (
            f" • {active_days} روز دارای ثبت حضور"
            if active_days
            else " • در این بازه هیچ ثبت حضوری وجود ندارد"
        )

        self.report_summary.setText(
            f"بازه {start} تا {end}{day_note} • "
            f"حاضر: {total_present} • "
            f"دیرکرد: {total_late} • "
            f"غیبت: {total_absent} • "
            f"موجه: {total_excused}"
        )


    def export_report(self, fmt):
        start = self.report_start.date().toString("yyyy-MM-dd")
        end = self.report_end.date().toString("yyyy-MM-dd")

        if start > end:
            QMessageBox.warning(
                self,
                "بازه نامعتبر",
                "تاریخ شروع باید قبل از تاریخ پایان باشد.",
            )
            return

        class_id = self.report_class_combo.currentData()
        major_id = self.report_major_combo.currentData()

        rows = get_report_rows(
            start,
            end,
            class_id,
            major_id,
        )
        daily = get_daily_report_summary(
            start,
            end,
            class_id,
            major_id,
        )
        classes = get_class_report_summary(
            start,
            end,
            major_id,
            class_id,
        )

        if not rows:
            QMessageBox.information(
                self,
                "گزارش خالی است",
                "داده‌ای برای این فیلتر وجود ندارد.",
            )
            return

        extension = "csv" if fmt == "csv" else "xlsx"
        default_name = f"گزارش_حضور_{start}_{end}.{extension}"

        path, _ = QFileDialog.getSaveFileName(
            self,
            "ذخیره گزارش",
            str(BASE_DIR / "exports" / default_name),
            "CSV (*.csv)" if fmt == "csv" else "Excel (*.xlsx)",
        )

        if not path:
            return

        Path(path).parent.mkdir(parents=True, exist_ok=True)

        student_headers = [
            "کد دانش‌آموزی",
            "نام",
            "پایه",
            "رشته",
            "کلاس",
            "حاضر",
            "دیرکرد",
            "غیبت",
            "موجه",
            "درصد حضور",
        ]

        try:
            if fmt == "csv":
                with open(
                    path,
                    "w",
                    newline="",
                    encoding="utf-8-sig",
                ) as file:
                    writer = csv.writer(file)
                    writer.writerow(student_headers)
                    for record in rows:
                        writer.writerow(
                            [
                                record[1],
                                record[2],
                                record[3],
                                record[4],
                                record[5],
                                record[6],
                                record[7],
                                record[8],
                                record[9],
                                record[10],
                            ]
                        )
            else:
                from openpyxl import Workbook
                from openpyxl.styles import Alignment, Font
                from openpyxl.utils import get_column_letter

                workbook = Workbook()

                student_sheet = workbook.active
                student_sheet.title = "دانش‌آموزان"

                student_sheet.append(student_headers)
                for record in rows:
                    student_sheet.append(
                        [
                            record[1],
                            record[2],
                            record[3],
                            record[4],
                            record[5],
                            record[6],
                            record[7],
                            record[8],
                            record[9],
                            record[10],
                        ]
                    )

                daily_sheet = workbook.create_sheet("روزانه")
                daily_sheet.append(
                    [
                        "تاریخ",
                        "حاضر",
                        "دیرکرد",
                        "غیبت",
                        "موجه",
                        "درصد حضور",
                    ]
                )
                for record in daily:
                    daily_sheet.append(list(record))

                class_sheet = workbook.create_sheet("کلاس‌ها")
                class_sheet.append(
                    [
                        "کلاس",
                        "رشته",
                        "دانش‌آموز",
                        "حاضر",
                        "دیرکرد",
                        "غیبت",
                        "موجه",
                        "درصد حضور",
                    ]
                )
                for record in classes:
                    class_sheet.append(
                        [
                            record[1],
                            record[2],
                            record[3],
                            record[4],
                            record[5],
                            record[6],
                            record[7],
                            record[8],
                        ]
                    )

                summary_sheet = workbook.create_sheet("خلاصه")
                summary_sheet.append(["شاخص", "مقدار"])
                total_students = len(rows)
                average = (
                    sum(record[10] for record in rows) / total_students
                    if total_students
                    else 0
                )
                summary_sheet.append(["دانش‌آموزان", total_students])
                summary_sheet.append(["میانگین حضور", round(average, 1)])
                summary_sheet.append(
                    [
                        "کل حضور",
                        sum(record[6] for record in rows),
                    ]
                )
                summary_sheet.append(
                    [
                        "کل دیرکرد",
                        sum(record[7] for record in rows),
                    ]
                )
                summary_sheet.append(
                    [
                        "کل غیبت",
                        sum(record[8] for record in rows),
                    ]
                )
                summary_sheet.append(
                    [
                        "کل موجه",
                        sum(record[9] for record in rows),
                    ]
                )

                for sheet in workbook.worksheets:
                    sheet.freeze_panes = "A2"
                    for cell in sheet[1]:
                        cell.font = Font(bold=True)
                        cell.alignment = Alignment(horizontal="center")

                    for column in range(
                        1,
                        sheet.max_column + 1,
                    ):
                        sheet.column_dimensions[
                            get_column_letter(column)
                        ].width = 18

                workbook.save(path)

            QMessageBox.information(
                self,
                "گزارش آماده شد",
                f"گزارش با موفقیت ذخیره شد.\n{path}",
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "ذخیره گزارش ناموفق بود",
                str(exc),
            )


    def import_students(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "انتخاب فایل دانش‌آموزان",
            str(BASE_DIR),
            "Excel (*.xlsx);;CSV (*.csv)",
        )
        if not path:
            return

        rows = []
        try:
            if path.lower().endswith(".csv"):
                with open(path, "r", encoding="utf-8-sig", newline="") as file:
                    rows = list(csv.DictReader(file))
            else:
                from openpyxl import load_workbook
                workbook = load_workbook(path, read_only=True, data_only=True)
                sheet = workbook.active
                values = list(sheet.iter_rows(values_only=True))
                if not values:
                    raise ValueError("فایل خالی است.")
                headers = [str(v).strip() if v is not None else "" for v in values[0]]
                for data_row in values[1:]:
                    rows.append(
                        {
                            headers[i]: data_row[i]
                            for i in range(min(len(headers), len(data_row)))
                            if headers[i]
                        }
                    )

            if not rows:
                raise ValueError("هیچ ردیفی برای ورود پیدا نشد.")

            def normalized(value):
                return (
                    str(value or "")
                    .strip()
                    .lower()
                    .replace(" ", "_")
                    .replace("-", "_")
                )

            aliases = {
                "code": {"student_code", "code", "کد", "کد_دانش_آموزی"},
                "name": {"name", "student_name", "نام", "نام_دانش_آموز"},
                "grade": {"grade", "پایه"},
                "major": {"major", "رشته"},
                "class": {"class", "class_name", "کلاس"},
                "year": {"academic_year", "year", "سال_تحصیلی"},
            }

            imported = 0
            updated = 0
            skipped = 0

            for source_row in rows:
                data = {
                    normalized(key): value
                    for key, value in source_row.items()
                }

                def get_field(alias_key):
                    for key, value in data.items():
                        if key in aliases[alias_key]:
                            return value
                    return ""

                code = str(get_field("code") or "").strip()
                name = str(get_field("name") or "").strip()
                grade = str(get_field("grade") or "").strip()
                major_name = str(get_field("major") or "").strip()
                class_name = str(get_field("class") or "").strip()
                year_name = str(get_field("year") or "").strip()

                if not code or not name:
                    skipped += 1
                    continue

                student = find_student_by_code(code)

                target_class_id = None

                if class_name:
                    if year_name:
                        year = next(
                            (
                                item
                                for item in list_academic_years()
                                if item[1] == year_name
                            ),
                            None,
                        )
                        if year is None:
                            skipped += 1
                            continue
                        else:
                            year_id = year[0]
                    else:
                        active = get_active_academic_year()
                        if active is None:
                            skipped += 1
                            continue
                        year_id = active[0]

                    major_id = None
                    if major_name:
                        major = find_major_by_name(major_name)
                        major_id = (
                            major[0]
                            if major
                            else add_major(major_name)
                        )

                    existing_class = find_class_by_name(
                        class_name,
                        year_id,
                    )
                    if existing_class:
                        target_class_id = existing_class[0]
                    else:
                        target_class_id = add_class(
                            class_name,
                            grade,
                            major_id,
                            year_id,
                        )

                if student is None:
                    student_id = add_student(code, name, target_class_id)
                    imported += 1
                else:
                    student_id = student[0]
                    update_student(
                        student_id,
                        code,
                        name,
                        target_class_id if target_class_id else None,
                    )
                    updated += 1

            self.refresh_all()

            QMessageBox.information(
                self,
                "ورود اطلاعات کامل شد",
                f"جدید: {imported}\nبه‌روزرسانی: {updated}\nردشده: {skipped}",
            )

        except Exception as exc:
            QMessageBox.critical(self, "ورود اطلاعات ناموفق بود", str(exc))

    def change_admin_password(self):
        dialog = AdminPasswordDialog(mode="change", parent=self)
        if dialog.exec() == QDialog.Accepted:
            QMessageBox.information(
                self,
                "انجام شد",
                "رمز مدیر با موفقیت تغییر کرد.",
            )

    def closeEvent(self, event):
        self._shutting_down = True
        self._camera_supervisor_enabled = False
        self.stop_camera()
        event.accept()


def ensure_admin_access():
    if not password_is_configured():
        dialog = AdminPasswordDialog(mode="setup")
        return dialog.exec() == QDialog.Accepted

    login = AdminPasswordDialog(mode="login")
    return login.exec() == QDialog.Accepted


def run_smoke_test():
    if not YUNET_MODEL_PATH.exists():
        raise RuntimeError(f"YuNet model missing: {YUNET_MODEL_PATH}")

    import dlib
    import face_recognition_models

    model_locations = {
        "5-point": face_recognition_models.pose_predictor_five_point_model_location(),
        "face-encoding": face_recognition_models.face_recognition_model_location(),
    }

    for name, model_path in model_locations.items():
        if not Path(model_path).exists():
            raise RuntimeError(
                f"face_recognition model missing ({name}): {model_path}"
            )

    dlib.face_recognition_model_v1(
        model_locations["face-encoding"]
    )

    detector = cv2.FaceDetectorYN.create(
        str(YUNET_MODEL_PATH),
        "",
        (CAMERA_WIDTH, CAMERA_HEIGHT),
        YUNET_SCORE_THRESHOLD,
        YUNET_NMS_THRESHOLD,
        YUNET_TOP_K,
    )
    detector.setInputSize((CAMERA_WIDTH, CAMERA_HEIGHT))

    print("FACE_RECOGNITION_SMOKE_TEST_OK")



def main():
    if "--smoke-test" in sys.argv:
        try:
            run_smoke_test()
            return
        except Exception as exc:
            print(f"FACE_RECOGNITION_SMOKE_TEST_FAILED: {exc}")
            raise SystemExit(1)

    app = QApplication(sys.argv)
    app.setLayoutDirection(Qt.RightToLeft)
    app.setStyle("Fusion")

    # Load Qt's Persian translations when the local Qt installation provides them.
    translator = QTranslator(app)
    translations_path = QLibraryInfo.path(
        QLibraryInfo.LibraryPath.TranslationsPath
    )
    if translator.load("qtbase_fa", translations_path):
        app.installTranslator(translator)

    app.setStyleSheet(
        """
        QWidget {
            font-family: "Noto Sans Arabic", "Vazirmatn", "Segoe UI", sans-serif;
            font-size: 14px;
            color: #253244;
        }

        QMainWindow, QWidget#content {
            background: #f5f7fa;
        }

        QDialog {
            background: #ffffff;
            color: #253244;
        }

        QLabel {
            color: #253244;
            background: transparent;
        }

        #sidebar {
            background: #ffffff;
            border-left: 1px solid #d9e1eb;
        }

        #brand {
            font-size: 21px;
            font-weight: 800;
            color: #172435;
        }

        #brandSub {
            color: #718096;
            font-size: 12px;
        }

        #sidebar QPushButton {
            text-align: right;
            padding: 12px 14px;
            border: none;
            border-radius: 10px;
            color: #526174;
            background: transparent;
        }

        #sidebar QPushButton:hover {
            background: #eef3f8;
            color: #1f2d3d;
        }

        #sidebar QPushButton:checked {
            background: #e4edf9;
            color: #1e5a9f;
            font-weight: 700;
        }

        #sidebarStatus, #muted {
            color: #718096;
        }

        #sidebarStatus {
            font-size: 11px;
            padding: 8px 2px;
        }

        #pageTitle {
            font-size: 29px;
            font-weight: 800;
            color: #172435;
            padding-bottom: 4px;
        }

        #sectionTitle {
            font-size: 16px;
            font-weight: 700;
            color: #26384d;
        }

        #statCard, #panel {
            background: #ffffff;
            border: 1px solid #d9e2ec;
            border-radius: 16px;
        }

        #cameraGateBox {
            background: #f8fafc;
            border: 1px solid #d4dee9;
            border-radius: 12px;
            padding: 1px;
        }

        #cameraGateTitle {
            color: #1e5a9f;
            font-size: 15px;
            font-weight: 800;
        }

        #fieldLabel {
            color: #64748b;
            font-size: 12px;
            font-weight: 700;
            padding-right: 2px;
            padding-bottom: 2px;
        }

        #globalIdentityPanel {
            background: #fbfcfe;
            border: 2px solid #b8cbe0;
            border-radius: 16px;
        }

        #globalIdentityTitle {
            color: #1e5a9f;
            font-size: 17px;
            font-weight: 800;
            padding-bottom: 2px;
        }

        #globalIdentityCheck {
            font-weight: 700;
            color: #334155;
        }

        #pageScrollArea {
            background: transparent;
            border: none;
        }

        #pageScrollArea > QWidget > QWidget {
            background: transparent;
        }

        #statTitle {
            color: #718096;
            font-size: 12px;
        }

        #statValue {
            font-size: 34px;
            font-weight: 800;
            color: #172435;
        }

        #cameraPreview {
            background: #eaf0f5;
            border: 1px solid #d4dde7;
            border-radius: 10px;
            color: #728197;
        }

        QLineEdit, QDateEdit, QComboBox, QSpinBox, QDoubleSpinBox {
            background: #ffffff;
            color: #253244;
            border: 1px solid #cbd6e2;
            border-radius: 9px;
            padding: 8px 10px;
            min-height: 40px;
            selection-background-color: #dbe9f8;
            selection-color: #1e3045;
        }

        QLineEdit:focus, QDateEdit:focus, QComboBox:focus,
        QSpinBox:focus, QDoubleSpinBox:focus {
            border: 1px solid #78a7d5;
            background: #ffffff;
            color: #172435;
        }

        QLineEdit:disabled, QDateEdit:disabled, QComboBox:disabled,
        QSpinBox:disabled, QDoubleSpinBox:disabled {
            background: #eef2f6;
            color: #9aa7b5;
        }

        QSpinBox, QDoubleSpinBox {
            min-height: 42px;
            padding: 4px 28px 4px 10px;
            color: #253244;
            background: #ffffff;
            border: 1px solid #cbd6e2;
        }

        QSpinBox QLineEdit, QDoubleSpinBox QLineEdit {
            background: transparent;
            color: #253244;
            selection-background-color: #dbe9f8;
            selection-color: #1e3045;
            padding: 2px 0px;
            min-height: 30px;
        }

        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
            background: #f7f9fc;
            border: none;
            width: 22px;
        }

        QSpinBox::up-button:hover, QSpinBox::down-button:hover,
        QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
            background: #e8eef5;
        }

        QLineEdit[readOnly="true"] {
            background: #f3f6f9;
        }

        QComboBox::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: left center;
            width: 30px;
            border: none;
            background: transparent;
        }

        QComboBox QAbstractItemView {
            background: #ffffff;
            color: #253244;
            border: 1px solid #cbd6e2;
            selection-background-color: #dbe9f8;
            selection-color: #1e3045;
            padding: 4px;
            outline: none;
        }

        QDateEdit::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: left center;
            width: 30px;
            border: none;
            background: transparent;
        }

        QTableWidget, QTableView {
            background: #ffffff;
            alternate-background-color: #f7f9fc;
            color: #253244;
            border: 1px solid #d9e2ec;
            border-radius: 10px;
            gridline-color: #e1e7ee;
            selection-background-color: #dbe9f8;
            selection-color: #1e3045;
        }

        QTableWidget::item {
            padding: 7px;
        }

        QHeaderView::section {
            background: #edf2f7;
            color: #526174;
            border: none;
            padding: 10px;
            font-weight: 700;
        }

        QPushButton {
            background: #ffffff;
            color: #2f4054;
            border: 1px solid #cbd6e2;
            border-radius: 9px;
            padding: 9px 14px;
            min-height: 34px;
        }

        QPushButton:hover {
            background: #f0f5fa;
            border-color: #b9c8d8;
        }

        QPushButton:pressed {
            background: #e6edf5;
        }

        QPushButton:disabled {
            background: #eef2f6;
            color: #9aa7b5;
            border-color: #d7dee7;
        }

        QMenu {
            background: #ffffff;
            color: #253244;
            border: 1px solid #cbd6e2;
            padding: 5px;
        }

        QMenu::item {
            padding: 8px 24px;
            border-radius: 6px;
        }

        QMenu::item:selected {
            background: #e5eef9;
            color: #1e5a9f;
        }

        QToolTip {
            background: #253244;
            color: #ffffff;
            border: 1px solid #3b4b60;
            padding: 5px;
        }

        QCheckBox {
            color: #253244;
            spacing: 7px;
            min-height: 28px;
        }

        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            background: #ffffff;
            border: 2px solid #aebdcb;
            border-radius: 4px;
        }

        QCheckBox::indicator:hover {
            border-color: #78a7d5;
            background: #f7faff;
        }

        QCheckBox::indicator:checked {
            background: #2f78c4;
            border-color: #2f78c4;
        }

        QCheckBox::indicator:checked:hover {
            background: #2468ad;
            border-color: #2468ad;
        }

        QCheckBox::indicator:disabled {
            background: #eef2f6;
            border-color: #d7dee7;
        }

        QMessageBox {
            background: #ffffff;
            color: #253244;
        }

        QMessageBox QLabel {
            color: #253244;
            min-width: 280px;
        }

        QScrollBar:vertical {
            background: #eef2f6;
            width: 12px;
            margin: 0;
        }

        QScrollBar::handle:vertical {
            background: #c5d0dc;
            min-height: 35px;
            border-radius: 6px;
        }

        QScrollBar::handle:vertical:hover {
            background: #aebccc;
        }

        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: transparent;
            border: none;
        }
        """
    )

    init_db()

    if not ensure_admin_access():
        return

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
