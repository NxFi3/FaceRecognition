"""Production smart camera worker: person tracking + face identity + events."""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
import math
import logging

logger = logging.getLogger(__name__)

from .camera_capture import LatestFrameCapture
from .runtime_profile import profile
try:
    from ..cloud.entitlement import runtime_allowed
except ImportError:
    from cloud.entitlement import runtime_allowed
from .camera_device import open_camera

import cv2
import dlib
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

try:
    from ..database import add_event, get_setting, mark_attendance
except ImportError:
    # Supports direct execution where src/ is the import root.
    from database import add_event, get_setting, mark_attendance
from .smart_events import AttendanceEventEngine, AttendancePresenceState
from .smart_tracking import (
    LineCrossing,
    PersonDetector,
    SmartPersonTracker,
    make_person_model_candidates,
)


BASE_DIR = Path(__file__).resolve().parents[2]
PERSON_DETECTION_INTERVAL_GPU = 2
PERSON_DETECTION_INTERVAL_CPU = 3
FACE_DETECTION_INTERVAL = 2
# Run YuNet on a smaller image, then map its boxes/landmarks back to camera pixels.
# 0.75 is a compromise between native-resolution CPU cost and half-resolution misses.
FACE_DETECTION_SCALE = 0.75
MAX_FACE_RECOGNITION_JOBS = 2
FACE_RECOGNITION_INTERVAL = 5
FACE_CONFIRMATIONS = 3
# Keep three independent observations for identity assignment until the
# system has a real presentation-attack/liveness check. A sharp printed face
# can also achieve a strong embedding distance and quality score.
FACE_CACHE_MAX_SAMPLES = 12
FACE_CACHE_MAX_SIDE = 160
FACE_CACHE_MIN_VOTE_RATIO = 0.50
FACE_CACHE_CLUSTER_DISTANCE = 0.58
FACE_CACHE_DISTANCE_MARGIN = 0.035
FACE_WITNESS_MAX_AGE_FRAMES = 45
PREVIEW_FPS = 15.0
PERSON_IMAGE_SIZE = 512
LINE_POSITION = 0.50
LINE_ORIENTATION = "vertical"
LINE_ENTER_DIRECTION = "positive"


def _camera_role_accepts_crossing(camera_role, direction):
    """Allow only the event type configured for this camera."""
    role = str(camera_role or "observe")
    if role == "entry":
        return direction == "enter"
    if role == "exit":
        return direction == "exit"
    return False


def _draw_gate_overlay(frame, line, camera_role):
    """Draw the exact active gate on the preview; observe-only has no gate."""
    if str(camera_role or "observe") == "observe":
        return frame

    if line.line_points is not None and line.inside_side in (-1, 1):
        x1, y1, x2, y2 = line.line_points
        height, width = frame.shape[:2]
        p1 = (
            int(round(x1 * max(0, width - 1))),
            int(round(y1 * max(0, height - 1))),
        )
        p2 = (
            int(round(x2 * max(0, width - 1))),
            int(round(y2 * max(0, height - 1))),
        )
        cv2.line(frame, p1, p2, (255, 200, 0), 3, cv2.LINE_AA)
    elif line.orientation == "vertical":
        line_x = int(frame.shape[1] * line.position)
        cv2.line(frame, (line_x, 0), (line_x, frame.shape[0]), (255, 200, 0), 3)
    else:
        line_y = int(frame.shape[0] * line.position)
        cv2.line(frame, (0, line_y), (frame.shape[1], line_y), (255, 200, 0), 3)
    return frame


def _check_camera_gate(line, track, frame_shape, frame_no, camera_role):
    """Run crossing detection and enforce this camera's entry/exit-only role."""
    previous_crossing = track.last_crossing
    previous_crossing_frame = track.last_crossing_frame
    direction = line.check(track, frame_shape, frame_no)
    if direction is not None and not _camera_role_accepts_crossing(camera_role, direction):
        # Filtered directions must not consume the cooldown; otherwise an
        # entry-only camera can suppress a following valid entry after a turn.
        track.last_crossing = previous_crossing
        track.last_crossing_frame = previous_crossing_frame
        return None
    return direction


_FACE_EXECUTOR_LOCK = Lock()
_FACE_EXECUTOR = None


def _get_shared_face_executor():
    """Use one bounded face-recognition pool for the whole application.

    Two camera workers must not each create their own dlib pools: on a weak
    school CPU that can oversubscribe the machine and make both camera loops
    slower. A small shared pool keeps recognition asynchronous without creating
    four concurrent dlib jobs for two cameras.
    """
    global _FACE_EXECUTOR

    with _FACE_EXECUTOR_LOCK:
        if _FACE_EXECUTOR is None:
            cpu_count = os.cpu_count() or 1
            worker_count = min(
                MAX_FACE_RECOGNITION_JOBS,
                max(1, cpu_count // 2),
            )
            _FACE_EXECUTOR = ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="smart-face",
            )

        return _FACE_EXECUTOR


class SmartCameraWorker(QThread):
    """Camera worker where a person's track outlives face visibility."""

    frame_ready = Signal(int, QImage)
    attendance_added = Signal(object)
    status_changed = Signal(str)
    error = Signal(str)
    event_added = Signal(object)

    def __init__(
        self,
        parent=None,
        camera_slot=1,
        camera_config=None,
        presence_state=None,
        identity_manager=None,
    ):
        super().__init__(parent)
        self._running = False
        self.camera_slot = int(camera_slot)
        self.camera_config = dict(camera_config or {})
        self.presence_state = presence_state or AttendancePresenceState()
        self.identity_manager = identity_manager

    def stop(self):
        self._running = False

    @staticmethod
    def _face_models():
        from . import model_assets as face_recognition_models

        shape_path = face_recognition_models.pose_predictor_five_point_model_location()
        encoder_path = face_recognition_models.face_recognition_model_location()
        if not os.path.exists(shape_path):
            raise FileNotFoundError(f"5-point model not found: {shape_path}")
        if not os.path.exists(encoder_path):
            raise FileNotFoundError(f"Face encoder not found: {encoder_path}")
        return (
            dlib.shape_predictor(str(shape_path)),
            dlib.face_recognition_model_v1(str(encoder_path)),
        )

    @staticmethod
    def _prepare_face(frame, bbox):
        x1, y1, x2, y2 = [int(v) for v in bbox]
        h, w = frame.shape[:2]
        x1 = max(0, min(x1, w - 1))
        y1 = max(0, min(y1, h - 1))
        x2 = max(x1 + 1, min(x2, w))
        y2 = max(y1 + 1, min(y2, h))

        face_w = x2 - x1
        face_h = y2 - y1
        pad_x = int(face_w * 0.20)
        pad_y = int(face_h * 0.20)

        cx1 = max(0, x1 - pad_x)
        cy1 = max(0, y1 - pad_y)
        cx2 = min(w, x2 + pad_x)
        cy2 = min(h, y2 + pad_y)

        crop = frame[cy1:cy2, cx1:cx2]
        if crop.size == 0:
            return None

        crop = np.ascontiguousarray(crop, dtype=np.uint8)
        local_bbox = (
            x1 - cx1,
            y1 - cy1,
            x2 - cx1,
            y2 - cy1,
        )
        return crop, local_bbox

    @staticmethod
    def _encode_face(crop, bbox, models):
        predictor, encoder = models
        x1, y1, x2, y2 = bbox
        rect = dlib.rectangle(
            left=int(x1),
            top=int(y1),
            right=int(x2),
            bottom=int(y2),
        )
        rgb_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        shape = predictor(rgb_crop, rect)
        return np.asarray(
            encoder.compute_face_descriptor(rgb_crop, shape, 0),
            dtype=np.float32,
        )

    @staticmethod
    def _recognize_task(matcher, crop, bbox, track_id, frame_no, submitted_at=None):
        """Recognize one observed face and retain its source-frame timestamp.

        ``submitted_at`` lets logs distinguish slow embedding inference from
        executor queueing and confirmation latency.
        """
        started = time.perf_counter()
        embedding, result = matcher.recognize_crop_with_embedding(crop, bbox)
        finished = time.perf_counter()
        total_ms = (
            (finished - submitted_at) * 1000.0
            if submitted_at is not None
            else (finished - started) * 1000.0
        )
        compute_ms = (finished - started) * 1000.0
        return int(track_id), int(frame_no), embedding, result, total_ms, compute_ms

    @staticmethod
    def _cache_recognition_embedding(track, frame_no, embedding):
        """Attach a computed embedding to the exact frame sample that produced it."""
        if embedding is None:
            return False
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if vector.size == 0:
            return False
        for sample in reversed(track.face_cache):
            if (
                int(sample.get("frame_no", -1)) == int(frame_no)
                and sample.get("embedding") is None
            ):
                sample["embedding"] = vector.copy()
                return True
        return False

    @staticmethod
    def _face_sample_quality(track, frame_no):
        for sample in reversed(track.face_cache):
            if int(sample.get("frame_no", -1)) == int(frame_no):
                return float(sample.get("quality", 0.0))
        return 0.0

    @classmethod
    def _required_face_confirmations(cls, track, frame_no, distance, stability):
        """Do not use embedding confidence as a substitute for liveness.

        A printed/displayed face can have excellent image quality and a close
        embedding, so all identity assignments currently require three
        independent recognitions. This is a conservative mitigation, not a
        presentation-attack detector.
        """
        return FACE_CONFIRMATIONS

    @staticmethod
    def _face_quality_score(detection, crop):
        if crop is None or crop.size == 0:
            return 0.0
        height, width = crop.shape[:2]
        face_size = min(width, height)
        size_score = max(0.0, min(1.0, (face_size - 28.0) / 132.0))
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        sharp_score = max(0.0, min(1.0, math.log1p(sharpness) / math.log1p(500.0)))

        frontal_score = 0.5
        values = np.asarray(detection, dtype=np.float32).reshape(-1)
        if values.size >= 14:
            points = values[4:14].reshape(5, 2)
            right_eye, left_eye, nose, right_mouth, left_mouth = points
            eye_center = (right_eye + left_eye) * 0.5
            eye_span = max(float(np.linalg.norm(right_eye - left_eye)), 1.0)
            yaw_error = float(np.linalg.norm(nose - eye_center)) / eye_span
            eye_roll = abs(float(right_eye[1] - left_eye[1])) / eye_span
            mouth_center = (right_mouth + left_mouth) * 0.5
            mouth_error = float(np.linalg.norm(nose - mouth_center)) / eye_span
            frontal_score = (
                0.55 * max(0.0, min(1.0, 1.0 - yaw_error / 0.65))
                + 0.25 * max(0.0, min(1.0, 1.0 - eye_roll / 0.35))
                + 0.20 * max(0.0, min(1.0, 1.0 - mouth_error / 1.8))
            )
        return 0.50 * size_score + 0.30 * sharp_score + 0.20 * frontal_score

    @staticmethod
    def _compact_face_sample(crop, bbox, max_side=FACE_CACHE_MAX_SIDE):
        if crop is None or crop.size == 0:
            return None
        h, w = crop.shape[:2]
        scale = min(1.0, float(max_side) / max(h, w))
        if scale < 1.0:
            nw = max(1, int(round(w * scale)))
            nh = max(1, int(round(h * scale)))
            crop = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
        else:
            nw, nh = w, h
        x1, y1, x2, y2 = bbox
        sx, sy = nw / max(float(w), 1.0), nh / max(float(h), 1.0)
        compact_bbox = (
            int(round(x1 * sx)), int(round(y1 * sy)),
            int(round(x2 * sx)), int(round(y2 * sy)),
        )
        return np.ascontiguousarray(crop, dtype=np.uint8), compact_bbox

    @staticmethod
    def _finalize_face_cache_task(matcher, samples):
        if not samples:
            return None
        clusters = []
        for sample in samples:
            try:
                cached_embedding = sample.get("embedding")
                if cached_embedding is None:
                    embedding = matcher.recognize_crop_with_embedding(
                        sample["crop"], sample["bbox"]
                    )[0]
                else:
                    embedding = np.asarray(
                        cached_embedding, dtype=np.float32
                    )
                result = matcher.match_embedding(embedding)
            except Exception:
                continue
            student_index, distance, stability = result
            if student_index is None or embedding is None:
                continue
            embedding = np.asarray(embedding, dtype=np.float32).reshape(-1)
            if embedding.size == 0:
                continue
            embedding /= max(float(np.linalg.norm(embedding)), 1e-8)

            selected = None
            best_cluster_distance = float("inf")
            for cluster in clusters:
                distance_to_centroid = float(
                    np.linalg.norm(embedding - cluster["centroid"])
                )
                if distance_to_centroid < best_cluster_distance:
                    best_cluster_distance = distance_to_centroid
                    selected = cluster
            if selected is None or best_cluster_distance > FACE_CACHE_CLUSTER_DISTANCE:
                selected = {"centroid": embedding.copy(), "samples": []}
                clusters.append(selected)

            selected["samples"].append({
                "student_index": int(student_index),
                "distance": float(distance),
                "stability": float(stability),
                "quality": float(sample["quality"]),
                "embedding": embedding,
            })
            embeddings = np.vstack([item["embedding"] for item in selected["samples"]])
            centroid = embeddings.mean(axis=0)
            selected["centroid"] = centroid / max(float(np.linalg.norm(centroid)), 1e-8)

        if not clusters:
            return None

        cluster = max(
            clusters,
            key=lambda item: (
                len(item["samples"]),
                sum(s["quality"] for s in item["samples"]),
            ),
        )
        votes = {}
        for sample in cluster["samples"]:
            distance_score = max(0.0, min(1.0, 1.0 - sample["distance"] / 0.50))
            stability_score = max(0.0, min(1.0, 1.0 - sample["stability"] / 0.52))
            weight = max(0.01, sample["quality"]) * max(
                0.05, 0.7 * distance_score + 0.3 * stability_score
            )
            bucket = votes.setdefault(
                sample["student_index"],
                {"count": 0, "weight": 0.0, "distances": [], "stabilities": [],
                },
            )
            bucket["count"] += 1
            bucket["weight"] += weight
            bucket["distances"].append(sample["distance"])
            bucket["stabilities"].append(sample["stability"])

        ranked = sorted(
            votes.items(),
            key=lambda item: (item[1]["weight"], item[1]["count"]),
            reverse=True,
        )
        if not ranked:
            return None

        best_index, best_vote = ranked[0]
        second_weight = ranked[1][1]["weight"] if len(ranked) > 1 else 0.0
        cluster_size = len(cluster["samples"])
        minimum_votes = max(2, int(math.ceil(cluster_size * FACE_CACHE_MIN_VOTE_RATIO)))
        if (
            best_vote["count"] < minimum_votes
            or best_vote["weight"] <= second_weight * 1.15
        ):
            return None

        best_distance = float(np.mean(best_vote["distances"]))
        best_stability = float(np.mean(best_vote["stabilities"]))
        if len(ranked) > 1:
            second_distance = float(np.mean(ranked[1][1]["distances"]))
            second_stability = float(np.mean(ranked[1][1]["stabilities"]))
            if (
                second_distance - best_distance < FACE_CACHE_DISTANCE_MARGIN
                and second_stability - best_stability < 0.025
            ):
                return None

        return {
            "student_index": int(best_index),
            "distance": best_distance,
            "stability": best_stability,
            "votes": int(best_vote["count"]),
            "cluster_size": int(cluster_size),
        }

    @staticmethod
    def _finalize_face_cache_for_track(matcher, samples, track, camera_id):
        """Return the context expected by the asynchronous cache-result handler."""
        result = SmartCameraWorker._finalize_face_cache_task(matcher, samples)
        return track, result, camera_id

    @staticmethod
    def _find_track_for_face(face_bbox, tracks):
        fx1, fy1, fx2, fy2 = face_bbox
        center = ((fx1 + fx2) * 0.5, (fy1 + fy2) * 0.5)
        best = None
        best_score = -1.0

        for track in tracks:
            x1, y1, x2, y2 = track.bbox
            inside = x1 <= center[0] <= x2 and y1 <= center[1] <= y2
            if inside:
                score = 1.0
            else:
                ix1 = max(fx1, x1)
                iy1 = max(fy1, y1)
                ix2 = min(fx2, x2)
                iy2 = min(fy2, y2)
                inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
                face_area = max(1, (fx2 - fx1) * (fy2 - fy1))
                score = inter / face_area

            if score > best_score:
                best = track
                best_score = score

        return best if best_score > 0.05 else None

    @staticmethod
    def _confidence(distance, stability, threshold=0.49):
        if distance is None:
            return 0.0
        d_score = max(0.0, min(1.0, 1.0 - float(distance) / threshold))
        s_score = (
            max(0.0, min(1.0, 1.0 - float(stability) / threshold))
            if stability is not None
            else d_score
        )
        return 0.7 * d_score + 0.3 * s_score

    def _resolve_person_model(self):
        configured = str(get_setting("person_model_path", "") or "").strip()
        if configured and os.path.exists(configured):
            configured_path = Path(configured)
            # If a previous version saved a .pt path, transparently use the
            # side-by-side ONNX export after it is installed. This keeps the
            # existing setting from forcing a missing PyTorch dependency.
            if configured_path.suffix.lower() == ".pt":
                onnx_sibling = configured_path.with_suffix(".onnx")
                if onnx_sibling.is_file():
                    logger.info(
                        "PERSON_MODEL_CONFIG_ONNX_OVERRIDE configured_pt=%s onnx=%s",
                        configured_path,
                        onnx_sibling,
                    )
                    return str(onnx_sibling)
            return configured

        candidates = make_person_model_candidates(
            BASE_DIR
        )
        if candidates:
            return str(candidates[0])

        raise FileNotFoundError(
            "YOLO person model not found. Put models/yolo26n.onnx (recommended, "
            "does not require PyTorch) or models/yolo26n.pt (requires Ultralytics + "
            "PyTorch), or set the 'person_model_path' application setting."
        )

    def _record_event(self, **kwargs):
        event_type = str(kwargs.get("event_type", ""))
        if event_type == "UNKNOWN_PERSON":
            # A short-lived detector/Re-ID track is not an attendance event.
            # Do not persist or publish one-off anonymous line crossings:
            # they flood the event table without giving staff a trustworthy
            # identity to review. Meaningful unknown-exit alerts should be
            # implemented as a separately validated, debounced feature.
            logger.debug(
                "NONPERSISTED_TRACK_NOISE event_type=%s track=%s camera=%s",
                event_type,
                kwargs.get("track_id"),
                kwargs.get("camera_id"),
            )
            return None

        event_id = add_event(**kwargs)
        event = dict(kwargs)
        event["id"] = event_id
        self.event_added.emit(event)
        return event_id

    @profile.in_camera(lambda self: self.camera_slot)
    def run(self):
        self._running = True
        profile.count("worker_starts")
        profile.gauge("worker_running", 1)
        logger.info("WORKER_START slot=%s config=%s", self.camera_slot, self.camera_config)
        capture = None
        futures = set()
        cache_futures = set()
        untracked_face_futures = {}
        face_future_track_ids = {}
        face_future_track_refs = {}
        face_only_identity = None
        face_only_candidate_student_id = None
        face_only_candidate_hits = 0
        face_only_last_frame = -1

        try:
            # Importing app at runtime avoids a module-level circular import.
            try:
                from ..app import (
                    CAMERA_HEIGHT,
                    CAMERA_WIDTH,
                    YUNET_MODEL_PATH,
                    YUNET_NMS_THRESHOLD,
                    YUNET_SCORE_THRESHOLD,
                    YUNET_TOP_K,
                    FaceMatcher,
                    _encode_face_crop,
                    _prepare_face_crop,
                    get_camera_settings,
                    frame_to_qimage,
                )
            except ImportError:
                from app import (
                    CAMERA_HEIGHT,
                    CAMERA_WIDTH,
                    YUNET_MODEL_PATH,
                    YUNET_NMS_THRESHOLD,
                    YUNET_SCORE_THRESHOLD,
                    YUNET_TOP_K,
                    FaceMatcher,
                    _encode_face_crop,
                    _prepare_face_crop,
                    get_camera_settings,
                    frame_to_qimage,
                )

            if not YUNET_MODEL_PATH.exists():
                raise FileNotFoundError(
                    f"YuNet model not found: {YUNET_MODEL_PATH}"
                )

            person_model = self._resolve_person_model()
            person_device = str(get_setting("person_device", "") or "").strip()

            reid = None
            reid_status = "Re-ID disabled (motion only)"
            reid_interval = int(get_setting("reid_interval", "6"))
            reid_enabled = str(get_setting("reid_enabled", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}
            try:
                import torch
                from .reid import OSNetReID

                if not reid_enabled:
                    raise RuntimeError("disabled in Settings")

                if not person_device:
                    person_device = (
                        "cuda"
                        if torch.cuda.is_available()
                        else "cpu"
                    )
                if person_device == "cuda" and not torch.cuda.is_available():
                    raise RuntimeError("CUDA requested but no GPU is available")

                # CPU-only school PCs get a lower Re-ID frequency so motion tracking
                # remains responsive between appearance refreshes.
                if person_device == "cpu":
                    # Keep cross-camera appearance updates responsive without
                    # making CPU Re-ID compete with every inference cycle.
                    reid_interval = max(reid_interval, 12)

                reid = OSNetReID(
                    model_name="osnet_x0_25",
                    device=person_device,
                )
                reid_status = f"OSNet x0.25/{person_device} every {reid_interval} frames"
            except Exception as exc:
                reid_status = f"Re-ID unavailable ({exc})"
                reid = None

            self.status_changed.emit(
                "Loading smart camera • person detector + " + reid_status
            )

            face_detector = cv2.FaceDetectorYN.create(
                str(YUNET_MODEL_PATH),
                "",
                (CAMERA_WIDTH, CAMERA_HEIGHT),
                YUNET_SCORE_THRESHOLD,
                YUNET_NMS_THRESHOLD,
                YUNET_TOP_K,
            )

            camera_id, target_fps = get_camera_settings()
            if "camera_id" in self.camera_config:
                camera_id = int(self.camera_config["camera_id"])
            if "target_fps" in self.camera_config:
                target_fps = int(self.camera_config["target_fps"])
            use_raw_bayer = bool(
                self.camera_config.get("raw_bayer", False)
            )
            capture = LatestFrameCapture(
                camera_id,
                target_fps,
                lambda cid, fps: open_camera(
                    cid, fps, width=CAMERA_WIDTH, height=CAMERA_HEIGHT
                ),
                raw_bayer=use_raw_bayer,
            )
            logger.info("WORKER_CAMERA_START slot=%s camera_id=%s target_fps=%s raw_bayer=%s", self.camera_slot, camera_id, target_fps, use_raw_bayer)
            capture.start()

            actual_width = capture.width or CAMERA_WIDTH
            actual_height = capture.height or CAMERA_HEIGHT
            actual_fps = capture.fps
            face_input_width = max(160, int(round(actual_width * FACE_DETECTION_SCALE)))
            face_input_height = max(160, int(round(actual_height * FACE_DETECTION_SCALE)))
            face_scale_x = actual_width / float(face_input_width)
            face_scale_y = actual_height / float(face_input_height)
            face_detector.setInputSize((face_input_width, face_input_height))
            logger.info(
                "FACE_DETECT_CONFIG slot=%s native=%sx%s input=%sx%s scale=%.2f",
                self.camera_slot, actual_width, actual_height,
                face_input_width, face_input_height, FACE_DETECTION_SCALE,
            )

            resolved_person_device = person_device or "cpu"
            person_detection_interval = (
                PERSON_DETECTION_INTERVAL_GPU
                if str(resolved_person_device).lower().startswith("cuda")
                else PERSON_DETECTION_INTERVAL_CPU
            )

            person_detector = PersonDetector(
                person_model,
                device=resolved_person_device,
                conf=float(get_setting("person_confidence", "0.20")),
                imgsz=PERSON_IMAGE_SIZE,
            )
            # ONNX Runtime currently uses the CPU provider in the standard
            # requirements, even if an old settings value still says "cuda".
            # Keep the conservative CPU cadence to avoid overloading laptops.
            if person_detector.backend == "onnxruntime-cpu":
                person_detection_interval = PERSON_DETECTION_INTERVAL_CPU

            tracker = SmartPersonTracker(
                reid=reid,
                reid_interval=reid_interval,
            )

            camera_role = str(
                self.camera_config.get(
                    "role",
                    "entry" if self.camera_slot == 1 else "exit",
                )
                or ("entry" if self.camera_slot == 1 else "exit")
            )
            if self.identity_manager is not None:
                self.identity_manager.register_camera(
                    camera_id,
                    camera_role,
                )

            line = LineCrossing(
                orientation=str(
                    self.camera_config.get(
                        "line_orientation",
                        get_setting(
                            "line_orientation",
                            LINE_ORIENTATION,
                        ),
                    )
                ),
                position=float(
                    self.camera_config.get(
                        "line_position",
                        get_setting("line_position", LINE_POSITION),
                    )
                ),
                enter_direction=str(
                    self.camera_config.get(
                        "line_direction",
                        get_setting(
                            "line_enter_direction",
                            LINE_ENTER_DIRECTION,
                        ),
                    )
                ),
                cooldown_frames=int(
                    self.camera_config.get(
                        "line_cooldown",
                        get_setting("line_cooldown_frames", "25"),
                    )
                ),
                primary_direction=(
                    "exit" if camera_role == "exit" else "enter"
                ),
                line_points=self.camera_config.get("line_points"),
                inside_side=self.camera_config.get("inside_side"),
            )

            matcher = FaceMatcher()
            event_engine = AttendanceEventEngine(
                add_event_callback=self._record_event,
                mark_attendance_callback=self._mark_attendance_event,
                get_setting_callback=get_setting,
                state=self.presence_state,
            )

            executor = _get_shared_face_executor()

            def has_pending_face_jobs(track_id):
                return any(
                    pending_track_id == int(track_id)
                    for pending_track_id in face_future_track_ids.values()
                )

            def schedule_face_cache_finalization(track, owner_camera_id):
                if track.face_cache_finalized or not track.face_cache:
                    return False
                track.face_cache_finalized = True
                track.metadata.pop("cache_finalize_waiting", None)
                cache_futures.add(
                    profile.submit(
                        executor, "cache_finalization",
                        self._finalize_face_cache_for_track,
                        matcher,
                        track.snapshot_face_cache(),
                        track,
                        owner_camera_id,
                    )
                )
                return True

            def maybe_schedule_waiting_face_cache(track, owner_camera_id):
                if (
                    track.metadata.get("cache_finalize_waiting")
                    and track.metadata.get("pending_crossing") is not None
                    and not has_pending_face_jobs(track.track_id)
                ):
                    schedule_face_cache_finalization(track, owner_camera_id)

            frame_no = 0
            last_capture_sequence = 0
            last_person_detections = []
            last_face_detections = []
            last_preview = 0.0
            fps_counter = 0
            fps_start = time.perf_counter()
            fps = 0.0
            last_person_detect_ms = 0.0
            last_tracker_update_ms = 0.0
            last_face_detect_ms = 0.0
            last_face_recognition_ms = 0.0
            last_face_compute_ms = 0.0

            if profile.enabled:
                profile.details(physical_camera_id=camera_id, width=actual_width, height=actual_height,
                                reported_capture_fps=actual_fps, person_backend=person_detector.backend,
                                person_model=Path(person_model).name, reid_status=reid_status,
                                person_interval=person_detection_interval, reid_interval=reid_interval,
                                face_detection_interval=FACE_DETECTION_INTERVAL,
                                face_recognition_interval=FACE_RECOGNITION_INTERVAL,
                                recognition_workers=executor._max_workers, opencv_threads=cv2.getNumThreads())
            logger.info("WORKER_ONLINE slot=%s camera_id=%s role=%s actual=%sx%s fps=%s students=%s reid=%s", self.camera_slot, camera_id, camera_role, actual_width, actual_height, actual_fps, len(matcher.students), reid_status)
            self.status_changed.emit(
                f"Smart camera online • Camera {self.camera_slot} "
                f"({camera_role}) • {actual_width}x{actual_height} • "
                f"{actual_fps:.0f} FPS • tracks + face identity + "
                f"line events • {len(matcher.students)} students • {reid_status}"
            )

            while self._running and runtime_allowed():
                sequence, frame = capture.wait_for_frame(
                    last_capture_sequence,
                    timeout=1.0,
                )
                if frame is None:
                    profile.count("frame_wait_timeouts")
                    logger.debug("FRAME_WAIT_TIMEOUT slot=%s", self.camera_slot)
                    continue

                # The capture thread may have consumed many physical frames
                # while inference was busy. Process only the newest snapshot so
                # latency stays bounded instead of building a stale-frame queue.
                last_capture_sequence = sequence
                frame_no += 1

                for future in tuple(cache_futures):
                    if not future.done():
                        continue
                    cache_futures.remove(future)
                    try:
                        track, result, camera_id = future.result()
                    except Exception as exc:
                        self.error.emit(f"Face cache worker failed: {exc}")
                        continue

                    # Keep the pending marker visible while on_face_identity
                    # runs; the event engine must not replay this crossing before
                    # the cache path applies its final witness decision.
                    pending = track.metadata.get("pending_crossing")
                    if result is not None:
                        student = matcher.students[result["student_index"]]
                        confidence = self._confidence(result["distance"], result["stability"])
                        if (
                            track.student_id != student["id"]
                            or track.metadata.get("identity_source") != "face"
                        ):
                            event_engine.on_face_identity(
                                track,
                                student["id"],
                                student["student_code"],
                                student["name"],
                                confidence,
                                camera_id,
                            )
                            if self.identity_manager is not None:
                                self.identity_manager.register_face_identity(
                                    track,
                                    camera_id,
                                    student["id"],
                                    student["student_code"],
                                    student["name"],
                                )
                        track.metadata["cache_identity_votes"] = result["votes"]
                        track.metadata["cache_cluster_size"] = result["cluster_size"]
                        if track.metadata.get("identity_source") != "face":
                            track.metadata["identity_source"] = "face_cache"

                    if pending is not None:
                        track.metadata.pop("pending_crossing", None)
                        track.metadata.pop("cache_finalize_waiting", None)
                        track.metadata["face_witnessed_for_crossing"] = (
                            result is not None
                        )
                        event_engine.on_crossing(
                            track,
                            pending["direction"],
                            camera_id,
                        )
                    track.clear_face_cache()

                for future in tuple(untracked_face_futures):
                    if not future.done():
                        continue
                    bbox = untracked_face_futures.pop(future)
                    try:
                        _, _, _, result, _, _ = future.result()
                    except Exception as exc:
                        logger.warning(
                            "FACE_ONLY_RECOGNITION_ERROR slot=%s error=%s",
                            self.camera_slot,
                            exc,
                        )
                        continue

                    student_index, distance, stability = result
                    face_only_last_frame = frame_no
                    if student_index is None:
                        face_only_candidate_student_id = None
                        face_only_candidate_hits = 0
                        continue

                    student = matcher.students[student_index]
                    student_id = student["id"]
                    if face_only_candidate_student_id == student_id:
                        face_only_candidate_hits += 1
                    else:
                        face_only_candidate_student_id = student_id
                        face_only_candidate_hits = 1

                    if face_only_candidate_hits >= FACE_CONFIRMATIONS:
                        face_only_identity = {
                            "student_index": int(student_index),
                            "bbox": bbox,
                            "confidence": self._confidence(distance, stability),
                            "frame_no": frame_no,
                        }

                for future in tuple(futures):
                    if not future.done():
                        continue

                    futures.remove(future)
                    track_ref = face_future_track_refs.pop(future, None)
                    face_future_track_ids.pop(future, None)
                    try:
                        (
                            track_id,
                            recognition_frame,
                            embedding,
                            result,
                            recognition_ms,
                            compute_ms,
                        ) = future.result()
                    except Exception as exc:
                        self.error.emit(f"Face recognition worker failed: {exc}")
                        if track_ref is not None:
                            maybe_schedule_waiting_face_cache(track_ref, camera_id)
                        continue

                    track = tracker.tracks.get(track_id)
                    cache_track = track if track is not None else track_ref
                    if cache_track is not None:
                        self._cache_recognition_embedding(
                            cache_track,
                            recognition_frame,
                            embedding,
                        )

                    last_face_recognition_ms = recognition_ms
                    last_face_compute_ms = compute_ms
                    if track is None:
                        # A track can disappear while dlib is still working.
                        # Keep its completed embedding for the pending crossing
                        # instead of needlessly encoding the same crop again.
                        if cache_track is not None:
                            maybe_schedule_waiting_face_cache(cache_track, camera_id)
                        continue

                    student_index, distance, stability = result
                    sample_quality = self._face_sample_quality(
                        cache_track if cache_track is not None else track,
                        recognition_frame,
                    )
                    if student_index is None:
                        logger.info(
                            "FACE_MATCH_REJECTED slot=%s camera=%s track=%s frame=%s "
                            "distance=%s stability=%s quality=%.3f total_ms=%.1f "
                            "compute_ms=%.1f",
                            self.camera_slot,
                            camera_id,
                            track_id,
                            recognition_frame,
                            "none" if distance is None else f"{float(distance):.4f}",
                            "none" if stability is None else f"{float(stability):.4f}",
                            sample_quality,
                            recognition_ms,
                            compute_ms,
                        )
                        track.candidate_student_id = None
                        track.candidate_hits = 0
                        maybe_schedule_waiting_face_cache(track, camera_id)
                        continue

                    student = matcher.students[student_index]
                    student_id = student["id"]
                    required_confirmations = self._required_face_confirmations(
                        track,
                        recognition_frame,
                        distance,
                        stability,
                    )

                    # Require multiple independent recognitions before
                    # assigning a face identity; image quality alone does not
                    # establish that the source is a live person.
                    if track.candidate_student_id == student_id:
                        track.candidate_hits += 1
                    else:
                        track.candidate_student_id = student_id
                        track.candidate_hits = 1

                    confidence = self._confidence(distance, stability)
                    logger.info(
                        "FACE_MATCH_CANDIDATE slot=%s camera=%s track=%s frame=%s "
                        "student_id=%s distance=%.4f stability=%.4f quality=%.3f "
                        "confirmations=%s/%s total_ms=%.1f compute_ms=%.1f",
                        self.camera_slot, camera_id, track_id, recognition_frame,
                        student_id, float(distance), float(stability),
                        sample_quality, track.candidate_hits, required_confirmations,
                        recognition_ms, compute_ms,
                    )
                    identity_is_current = track.student_id == student_id
                    identity_is_new = (
                        track.student_id is None
                        or track.student_id != student_id
                    )
                    # Identity switches retain the same conservative
                    # three-observation policy as initial assignments.
                    was_reconciled = False

                    if identity_is_current:
                        if (
                            track.metadata.get("identity_source") != "face"
                            and track.candidate_hits >= required_confirmations
                        ):
                            was_reconciled = event_engine.on_face_identity(
                                track,
                                student_id,
                                student["student_code"],
                                student["name"],
                                confidence,
                                camera_id,
                            )
                            if self.identity_manager is not None:
                                self.identity_manager.register_face_identity(
                                    track,
                                    camera_id,
                                    student_id,
                                    student["student_code"],
                                    student["name"],
                                )
                            track.metadata.pop("global_identity", None)
                            track.metadata["identity_source"] = "face"
                            track.metadata["face_distance"] = distance
                            track.metadata["face_stability"] = stability
                            track.metadata["identity_reconciled"] = was_reconciled
                        elif track.metadata.get("identity_source") == "face":
                            track.candidate_hits = max(
                                track.candidate_hits,
                                FACE_CONFIRMATIONS,
                            )
                    elif identity_is_new and track.candidate_hits >= required_confirmations:
                        was_reconciled = event_engine.on_face_identity(
                            track,
                            student_id,
                            student["student_code"],
                            student["name"],
                            confidence,
                            camera_id,
                        )
                        if self.identity_manager is not None:
                            self.identity_manager.register_face_identity(
                                track,
                                camera_id,
                                student_id,
                                student["student_code"],
                                student["name"],
                            )
                        # Face confirmation supersedes a provisional Re-ID link.
                        track.metadata.pop("global_identity", None)
                        track.metadata["identity_source"] = "face"
                        track.metadata["face_distance"] = distance
                        track.metadata["face_stability"] = stability
                        track.metadata["identity_reconciled"] = was_reconciled

                    if track.metadata.get("identity_source") == "face":
                        logger.debug(
                            "FACE_RECOGNITION_RESULT slot=%s track=%s frame=%s "
                            "distance=%.3f stability=%.3f confirmations=%s/%s "
                            "total_ms=%.1f compute_ms=%.1f",
                            self.camera_slot,
                            track_id,
                            recognition_frame,
                            float(distance),
                            float(stability),
                            track.candidate_hits,
                            required_confirmations,
                            recognition_ms,
                            compute_ms,
                        )
                    maybe_schedule_waiting_face_cache(track, camera_id)

                previous_track_ids = set(tracker.tracks)
                if frame_no % person_detection_interval == 0:
                    previous_tracks = dict(tracker.tracks)
                    previous_track_ids = set(previous_tracks)
                    yolo_started = time.perf_counter()
                    last_person_detections = person_detector.detect(frame)
                    last_person_detect_ms = (time.perf_counter() - yolo_started) * 1000.0
                    tracker_started = time.perf_counter()
                    active_tracks = tracker.update(
                        last_person_detections,
                        frame,
                        frame_no,
                    )
                    last_tracker_update_ms = (time.perf_counter() - tracker_started) * 1000.0

                    active_ids = set(tracker.tracks)
                    for lost_id, lost_track in previous_tracks.items():
                        if lost_id not in active_ids:
                            if self.identity_manager is not None:
                                self.identity_manager.track_lost(
                                    lost_track,
                                    camera_id,
                                )
                            event_engine.on_track_lost(
                                lost_track,
                                camera_id,
                            )
                            if (
                                lost_track.face_cache
                                and not lost_track.face_cache_finalized
                                and lost_track.metadata.get("pending_crossing")
                            ):
                                if has_pending_face_jobs(lost_id):
                                    lost_track.metadata["cache_finalize_waiting"] = True
                                else:
                                    schedule_face_cache_finalization(
                                        lost_track,
                                        camera_id,
                                    )

                    for track in active_tracks:
                        if track.last_seen_frame != frame_no:
                            continue

                        # First try a shared cross-camera Re-ID hand-off. A
                        # successful link changes UNKNOWN -> Student before the
                        # line engine runs, so the exit/entry event is attributed
                        # to the correct person immediately.
                        if self.identity_manager is not None and reid is not None:
                            identity_match = self.identity_manager.observe_track(
                                track,
                                camera_id,
                            )
                            if identity_match is not None:
                                # The shared manager has already seen this
                                # student's trusted appearance on the other
                                # camera. Apply that hand-off to this local
                                # track so Camera 2 can actually see the same
                                # identity instead of silently parking the
                                # match in unused metadata.
                                event_engine.on_reid_identity(
                                    track,
                                    identity_match.student_id,
                                    identity_match.student_code,
                                    identity_match.student_name,
                                    min(1.0, max(0.0, identity_match.similarity)),
                                    camera_id,
                                    metadata={
                                        "similarity": identity_match.similarity,
                                        "source_camera": identity_match.source_camera,
                                        "source_track_id": identity_match.source_track_id,
                                        "reason": identity_match.reason,
                                    },
                                )
                                track.metadata["global_identity"] = {
                                    "student_id": identity_match.student_id,
                                    "student_code": identity_match.student_code,
                                    "student_name": identity_match.student_name,
                                    "similarity": identity_match.similarity,
                                    "source_camera": identity_match.source_camera,
                                    "source_track_id": identity_match.source_track_id,
                                    "reason": identity_match.reason,
                                }
                                track.metadata["identity_source"] = "reid"
                                logger.info(
                                    "GLOBAL_REID_LINK slot=%s camera=%s track=%s "
                                    "student=%s similarity=%.3f source_camera=%s source_track=%s",
                                    self.camera_slot,
                                    camera_id,
                                    track.track_id,
                                    identity_match.student_id,
                                    identity_match.similarity,
                                    identity_match.source_camera,
                                    identity_match.source_track_id,
                                )

                        event_engine.on_track_seen(track, camera_id)

                        if camera_role == "observe":
                            continue

                        direction = _check_camera_gate(
                            line, track, frame.shape, frame_no, camera_role
                        )
                        if direction is not None:
                            track.metadata["face_witnessed_for_crossing"] = (
                                track.last_face_frame >= 0
                                and frame_no - track.last_face_frame
                                <= FACE_WITNESS_MAX_AGE_FRAMES
                                and track.metadata.get("identity_source") in {"face", "face_cache"}
                            )
                            # A trusted face/Re-ID identity should never wait for
                            # unrelated cache voting before its line-crossing event.
                            # Only unresolved tracks need the cache fallback.
                            needs_face_confirmation = (
                                track.student_id is None
                                or (
                                    direction == "enter"
                                    and track.metadata.get("identity_source")
                                    not in {"face", "face_cache"}
                                )
                            )
                            if (
                                track.metadata.get("pending_crossing") is None
                                and needs_face_confirmation
                                and track.face_cache
                            ):
                                track.metadata["pending_crossing"] = {
                                    "direction": direction,
                                    "camera_id": camera_id,
                                    "frame_no": frame_no,
                                }
                                if has_pending_face_jobs(track.track_id):
                                    track.metadata["cache_finalize_waiting"] = True
                                else:
                                    schedule_face_cache_finalization(
                                        track,
                                        camera_id,
                                    )
                            else:
                                event_engine.on_crossing(
                                    track,
                                    direction,
                                    camera_id,
                                )
                                if track.student_id is not None and track.face_cache:
                                    track.clear_face_cache()
                else:
                    for track in tracker.tracks.values():
                        if track.misses == 0:
                            track.predict(frame.shape)

                # Face detection must not depend on the person tracker.
                # A missed YOLO person detection otherwise prevents YuNet from
                # running and the recognition path can never recover.
                if frame_no % FACE_DETECTION_INTERVAL == 0:
                    face_started = time.perf_counter()
                    if (face_input_width, face_input_height) == (frame.shape[1], frame.shape[0]):
                        face_frame = frame
                    else:
                        face_frame = cv2.resize(
                            frame, (face_input_width, face_input_height),
                            interpolation=cv2.INTER_AREA,
                        )
                    _, detections = face_detector.detect(face_frame)
                    last_face_detect_ms = (time.perf_counter() - face_started) * 1000.0
                    profile.observe("yunet_including_resize", last_face_detect_ms)
                    last_face_detections = []
                    if detections is not None:
                        for raw_detection in detections:
                            # YuNet returns bbox + five landmarks + score. Scale
                            # coordinates back before cropping/recognition/quality scoring.
                            detection = np.asarray(raw_detection, dtype=np.float32).copy()
                            detection[:14:2] *= face_scale_x
                            detection[1:14:2] *= face_scale_y
                            x, y, w, h = detection[:4]
                            bbox = (
                                max(0, int(x)),
                                max(0, int(y)),
                                min(frame.shape[1], int(x + w)),
                                min(frame.shape[0], int(y + h)),
                            )
                            if (
                                bbox[2] - bbox[0] >= 24
                                and bbox[3] - bbox[1] >= 24
                            ):
                                last_face_detections.append((bbox, detection))

                if frame_no % 30 == 0:
                    face_scores = [] if not last_face_detections else [
                        float(detection[14]) for _, detection in last_face_detections
                    ]
                    active_track_count = sum(1 for track in tracker.tracks.values() if track.misses == 0)
                    stale_track_count = len(tracker.tracks) - active_track_count
                    new_track_ids = sorted(set(tracker.tracks) - previous_track_ids) if frame_no % person_detection_interval == 0 else []
                    logger.info(
                        "PIPELINE_DETECT slot=%s frame=%s persons=%s det_conf=%s "
                        "tracks=%s active_tracks=%s stale_tracks=%s new_track_ids=%s "
                        "faces=%s face_scores=%s yolo_ms=%.1f tracker_ms=%.1f reid_ms=%.1f reid_reason=%s "
                        "yunet_ms=%.1f face_jobs=%s untracked_jobs=%s cache_jobs=%s "
                        "recognition_total_ms=%.1f recognition_compute_ms=%.1f fps=%.1f",
                        self.camera_slot, frame_no, len(last_person_detections),
                        [round(float(d.get("confidence", 0.0)), 3) for d in last_person_detections[:10]],
                        len(tracker.tracks), active_track_count, stale_track_count, new_track_ids,
                        len(last_face_detections), [round(score, 3) for score in face_scores[:10]],
                        last_person_detect_ms, last_tracker_update_ms, tracker.last_reid_ms,
                        tracker.last_reid_reason, last_face_detect_ms, len(futures),
                        len(untracked_face_futures), len(cache_futures),
                        last_face_recognition_ms, last_face_compute_ms, fps,
                    )

                submitted_tracks = set()
                if frame_no % FACE_DETECTION_INTERVAL != 0:
                    last_face_detections_for_recognition = []
                else:
                    last_face_detections_for_recognition = last_face_detections

                for face_bbox, face_detection in last_face_detections_for_recognition:
                    track = self._find_track_for_face(
                        face_bbox,
                        list(tracker.tracks.values()),
                    )
                    if track is None or track.misses > 0:
                        # Recognition must be able to recover from a temporary
                        # person-detector miss. This fallback is recognition-only;
                        # attendance still requires the normal person track and
                        # face-witness path.
                        if (
                            not untracked_face_futures
                            and frame_no - face_only_last_frame >= FACE_RECOGNITION_INTERVAL
                        ):
                            prepared = _prepare_face_crop(frame, face_bbox)
                            if prepared is not None:
                                face_crop, local_bbox = prepared
                                submitted_at = time.perf_counter()
                                future = profile.submit(
                                    executor, "face_recognition",
                                    self._recognize_task,
                                    matcher,
                                    face_crop,
                                    local_bbox,
                                    -1,
                                    frame_no,
                                    submitted_at,
                                )
                                untracked_face_futures[future] = face_bbox
                        continue
                    if track.track_id in submitted_tracks:
                        continue
                    if frame_no - track.last_face_frame < FACE_RECOGNITION_INTERVAL:
                        continue
                    if len(futures) >= MAX_FACE_RECOGNITION_JOBS:
                        break

                    prepared = _prepare_face_crop(frame, face_bbox)
                    if prepared is None:
                        continue

                    face_crop, local_bbox = prepared
                    compact = self._compact_face_sample(
                        face_crop,
                        local_bbox,
                    )
                    if compact is not None:
                        compact_crop, compact_bbox = compact
                        quality = self._face_quality_score(
                            face_detection,
                            face_crop,
                        )
                        track.cache_face_sample(
                            compact_crop,
                            compact_bbox,
                            quality,
                            frame_no,
                            FACE_CACHE_MAX_SAMPLES,
                        )
                    track.last_face_frame = frame_no
                    submitted_at = time.perf_counter()
                    future = profile.submit(
                        executor, "face_recognition",
                        self._recognize_task,
                        matcher,
                        face_crop,
                        local_bbox,
                        track.track_id,
                        frame_no,
                        submitted_at,
                    )
                    futures.add(future)
                    face_future_track_ids[future] = track.track_id
                    face_future_track_refs[future] = track
                    submitted_tracks.add(track.track_id)

                preview_now = time.perf_counter()
                fps_counter += 1
                elapsed = preview_now - fps_start
                if elapsed >= 1.0:
                    fps = fps_counter / elapsed
                    fps_counter = 0
                    fps_start = preview_now

                preview_time = time.perf_counter()
                if preview_time - last_preview >= 1.0 / PREVIEW_FPS:
                    # Keep the preview gate identical to the active crossing line.
                    _draw_gate_overlay(frame, line, camera_role)

                    for track in tracker.tracks.values():
                        x1, y1, x2, y2 = track.bbox
                        if track.student_id is None:
                            label = f"UNKNOWN  T{track.track_id}"
                        else:
                            linked_label = (
                                "  LINKED"
                                if track.metadata.get("global_identity")
                                else ""
                            )
                            label = (
                                f"{track.student_name}  T{track.track_id}"
                                + linked_label
                                + ("  OCCLUDED" if track.misses else "")
                            )

                        box_color = (
                            (0, 220, 80)
                            if track.student_id is not None
                            else (0, 140, 255)
                        )
                        if track.misses:
                            box_color = (180, 180, 180)

                        cv2.rectangle(
                            frame,
                            (x1, y1),
                            (x2, y2),
                            box_color,
                            2,
                        )
                        cv2.putText(
                            frame,
                            label,
                            (x1, max(24, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.55,
                            box_color,
                            2,
                            cv2.LINE_AA,
                        )

                        if track.student_id is not None:
                            link_info = track.metadata.get("global_identity")
                            identity = (
                                f"face={track.identity_confidence:.2f} "
                                f"last-face={frame_no - track.last_face_frame}"
                            )
                            if link_info:
                                identity += (
                                    f" | global-reid={link_info['similarity']:.2f}"
                                    f" <- C{link_info['source_camera']}"
                                )
                            cv2.putText(
                                frame,
                                identity,
                                (x1, min(frame.shape[0] - 8, y2 + 18)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.45,
                                box_color,
                                1,
                                cv2.LINE_AA,
                            )

                    if (
                        face_only_identity is not None
                        and frame_no - face_only_identity["frame_no"] <= FACE_WITNESS_MAX_AGE_FRAMES
                    ):
                        fx1, fy1, fx2, fy2 = face_only_identity["bbox"]
                        cv2.rectangle(
                            frame,
                            (fx1, fy1),
                            (fx2, fy2),
                            (255, 0, 255),
                            2,
                        )
                        student = matcher.students[face_only_identity["student_index"]]
                        cv2.putText(
                            frame,
                            f"FACE {student['name']}  {face_only_identity['confidence']:.2f}",
                            (fx1, max(24, fy1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.55,
                            (255, 0, 255),
                            2,
                            cv2.LINE_AA,
                        )

                    cv2.putText(
                        frame,
                        f"FPS {fps:.1f} | tracks {sum(1 for t in tracker.tracks.values() if t.misses == 0)}/{len(tracker.tracks)} | {reid_status}",
                        (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.58,
                        (255, 255, 255),
                        2,
                        cv2.LINE_AA,
                    )

                    self.frame_ready.emit(
                        self.camera_slot,
                        frame_to_qimage(frame),
                    )
                    last_preview = preview_time
                    if profile.enabled:
                        profile.observe("preview_including_qimage", (time.perf_counter() - preview_time) * 1000)
                    profile.count("preview_frames")

                if profile.enabled:
                    profile.gauge("active_tracks", sum(t.misses == 0 for t in tracker.tracks.values()))
                    profile.gauge("stale_tracks", sum(t.misses != 0 for t in tracker.tracks.values()))
                    profile.gauge("unconsumed_face_futures", len(futures) + len(untracked_face_futures))
                    profile.gauge("unconsumed_cache_futures", len(cache_futures))
                    profile.frame_done()

        except Exception as exc:
            profile.count("worker_crashes")
            logger.exception(
                "WORKER_CRASH slot=%s config=%s",
                self.camera_slot,
                self.camera_config,
            )
            self.error.emit(str(exc))
            self.status_changed.emit("Smart camera stopped")
        finally:
            for future in futures:
                future.cancel()
            for future in untracked_face_futures:
                future.cancel()
            for future in cache_futures:
                future.cancel()
            if capture is not None:
                capture.stop()
            self._running = False
            profile.gauge("worker_running", 0)
            for metric in ("active_tracks", "stale_tracks", "unconsumed_face_futures", "unconsumed_cache_futures"):
                profile.gauge(metric, 0)

    def _mark_attendance_event(
        self,
        student_id,
        camera_id,
        status="present",
        notes="",
    ):
        inserted = mark_attendance(
            student_id,
            camera_id,
            status=status,
            notes=notes,
        )
        if inserted:
            student = None
            try:
                from .database import get_student

                row = get_student(student_id)
                if row is not None:
                    student = {
                        "student_id": row[0],
                        "student_code": row[1],
                        "name": row[2],
                    }
            except Exception:
                pass

            payload = student or {
                "student_id": student_id,
                "student_code": "",
                "name": "",
            }
            payload.update(
                {
                    "time": time.strftime("%H:%M:%S"),
                    "camera": camera_id,
                }
            )
            self.attendance_added.emit(payload)
        return inserted


if __name__ == "__main__":
    raise SystemExit(
        "SmartCameraWorker is loaded by src.app; run the main application instead."
    )
