"""Person tracking, appearance Re-ID, identity persistence and line crossing."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import logging
import time

logger = logging.getLogger(__name__)
import numpy as np

from .runtime_profile import profile


REID_INTERVAL = 6
MAX_MISSES = 18
# Keep small/distant people in the tracking stream; face recognition has its
# own stricter identity gates, so a slightly more permissive person detector
# does not by itself grant attendance.
MIN_PERSON_SIZE = 36
ASSOCIATION_IOU_GATE = 0.08
ASSOCIATION_REID_GATE = 0.70
ASSOCIATION_REID_STRONG = 0.84
APPEARANCE_EMA_ALPHA = 0.18
IDENTITY_CONFIRMATIONS = 2
FACE_CACHE_MAX_SAMPLES = 12


def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    return inter / max(area_a + area_b - inter, 1)


def cosine(a, b):
    if a is None or b is None:
        return 0.0
    a = np.asarray(a, dtype=np.float32).reshape(-1)
    b = np.asarray(b, dtype=np.float32).reshape(-1)
    return float(np.dot(a, b) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-8))


def bbox_center(bbox):
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) * 0.5, (y1 + y2) * 0.5)


def foot_point(bbox):
    x1, _, x2, y2 = bbox
    return ((x1 + x2) * 0.5, float(y2))


def crop_bbox(frame, bbox):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in bbox]
    x1 = max(0, min(x1, w - 1))
    y1 = max(0, min(y1, h - 1))
    x2 = max(x1 + 1, min(x2, w))
    y2 = max(y1 + 1, min(y2, h))
    crop = frame[y1:y2, x1:x2]
    return crop if crop.size else None


class PersonDetector:
    """Person detector supporting torch-free ONNX Runtime and optional Ultralytics .pt."""

    def __init__(
        self,
        model_path: str | Path,
        device: str = "cpu",
        conf: float = 0.30,
        imgsz: int = 480,
    ):
        self.model_path = Path(model_path)
        self.device = str(device or "cpu")
        self.conf = float(conf)
        self.imgsz = max(320, int(imgsz))
        self.backend = ""
        self.model = None
        self._onnx_session = None
        self._onnx_input_name = None
        self._onnx_output_name = None
        self._onnx_input_height = self.imgsz
        self._onnx_input_width = self.imgsz

        if self.model_path.suffix.lower() == ".onnx":
            self._init_onnx()
        else:
            self._init_ultralytics()

    def _init_onnx(self):
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "ONNX person detection requires onnxruntime. Install it with "
                "'py -3.11 -m pip install onnxruntime'."
            ) from exc

        # Two camera workers can each own a detector. Keep per-session thread
        # counts modest to avoid CPU oversubscription on school PCs.
        try:
            intra_threads = int(os.environ.get("FR_ONNX_THREADS", "1"))
        except (TypeError, ValueError):
            intra_threads = 1
        intra_threads = max(1, min(8, intra_threads))

        options = ort.SessionOptions()
        options.intra_op_num_threads = intra_threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        try:
            self._onnx_session = ort.InferenceSession(
                str(self.model_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:
            raise RuntimeError(
                f"Could not load ONNX person model '{self.model_path}': {exc}"
            ) from exc

        inputs = self._onnx_session.get_inputs()
        outputs = self._onnx_session.get_outputs()
        if len(inputs) != 1 or not outputs:
            raise RuntimeError(
                "Unsupported ONNX person model: expected one input and at least one output."
            )

        input_meta = inputs[0]
        input_shape = input_meta.shape
        if len(input_shape) != 4 or input_shape[1] not in (3, "3"):
            raise RuntimeError(
                f"Unsupported ONNX input shape {input_shape}; expected NCHW with 3 channels."
            )
        self._onnx_input_name = input_meta.name
        self._onnx_output_name = outputs[0].name
        self._onnx_input_height = (
            int(input_shape[2])
            if isinstance(input_shape[2], (int, np.integer)) and input_shape[2] > 0
            else self.imgsz
        )
        self._onnx_input_width = (
            int(input_shape[3])
            if isinstance(input_shape[3], (int, np.integer)) and input_shape[3] > 0
            else self.imgsz
        )
        self.backend = "onnxruntime-cpu"
        logger.info(
            "PERSON_DETECTOR_READY backend=%s model=%s input=%sx%s threads=%s providers=%s",
            self.backend,
            self.model_path,
            self._onnx_input_width,
            self._onnx_input_height,
            intra_threads,
            self._onnx_session.get_providers(),
        )

    def _init_ultralytics(self):
        try:
            # Keep the .pt backend optional and out of frozen/torch-free runtime
            # builds. ONNX is the preferred deployment format.
            import importlib

            YOLO = importlib.import_module("ultralytics").YOLO
        except ImportError as exc:
            raise RuntimeError(
                "The selected person model is a .pt file and requires Ultralytics + "
                "PyTorch. For a torch-free runtime, put yolo26n.onnx in models/ and "
                "install onnxruntime."
            ) from exc

        self.model = YOLO(str(self.model_path))
        self.backend = "ultralytics"
        logger.info(
            "PERSON_DETECTOR_READY backend=%s model=%s device=%s imgsz=%s",
            self.backend,
            self.model_path,
            self.device,
            self.imgsz,
        )

    @staticmethod
    def _letterbox_onnx(frame, target_width, target_height):
        """Letterbox a BGR frame to the fixed NCHW ONNX input dimensions."""
        frame_height, frame_width = frame.shape[:2]
        if frame_height <= 0 or frame_width <= 0:
            raise ValueError("Person detector received an empty frame.")

        scale = min(
            target_width / float(frame_width),
            target_height / float(frame_height),
        )
        resized_width = max(1, min(target_width, int(round(frame_width * scale))))
        resized_height = max(1, min(target_height, int(round(frame_height * scale))))
        resized = cv2.resize(
            frame,
            (resized_width, resized_height),
            interpolation=cv2.INTER_LINEAR,
        )

        pad_width = target_width - resized_width
        pad_height = target_height - resized_height
        left = int(round(pad_width / 2.0 - 0.1))
        right = pad_width - left
        top = int(round(pad_height / 2.0 - 0.1))
        bottom = pad_height - top
        canvas = cv2.copyMakeBorder(
            resized,
            top,
            bottom,
            left,
            right,
            cv2.BORDER_CONSTANT,
            value=(114, 114, 114),
        )
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        tensor = np.ascontiguousarray(
            rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        )
        return tensor, scale, left, top

    def _decode_onnx_output(self, output, frame_shape, scale, pad_left, pad_top):
        """Decode the YOLO26 one-to-one output [1, N, 6] into app detections."""
        values = np.asarray(output, dtype=np.float32)
        if values.ndim == 3:
            if values.shape[0] != 1:
                raise RuntimeError(
                    f"Unsupported ONNX batch output shape {values.shape}; batch size must be 1."
                )
            values = values[0]
        if values.ndim != 2 or values.shape[1] != 6:
            raise RuntimeError(
                f"Unsupported YOLO26 ONNX output shape {np.asarray(output).shape}. "
                "Expected [1, N, 6] with xyxy, confidence, class; re-export using "
                "tools/export_person_model_onnx.py."
            )

        frame_height, frame_width = frame_shape[:2]
        detections = []
        for row in values:
            x1, y1, x2, y2, confidence, class_id = (float(v) for v in row)
            if not np.isfinite(row).all():
                continue
            if confidence < self.conf or int(round(class_id)) != 0:
                continue

            # YOLO26 end-to-end ONNX returns xyxy boxes in letterboxed-input pixels.
            x1 = int(round((x1 - pad_left) / scale))
            y1 = int(round((y1 - pad_top) / scale))
            x2 = int(round((x2 - pad_left) / scale))
            y2 = int(round((y2 - pad_top) / scale))
            x1 = max(0, min(x1, frame_width - 1))
            y1 = max(0, min(y1, frame_height - 1))
            x2 = max(0, min(x2, frame_width))
            y2 = max(0, min(y2, frame_height))
            if x2 - x1 < MIN_PERSON_SIZE or y2 - y1 < MIN_PERSON_SIZE:
                continue
            detections.append(
                {
                    "bbox": (x1, y1, x2, y2),
                    "confidence": float(confidence),
                }
            )
            if len(detections) >= 20:
                break
        return detections

    @profile.stage("person_detector")
    def detect(self, frame):
        if frame is None or getattr(frame, "size", 0) == 0:
            return []

        if self.backend == "onnxruntime-cpu":
            logger.debug(
                "PERSON_DETECT_START backend=%s model=%s",
                self.backend,
                self.model_path,
            )
            tensor, scale, pad_left, pad_top = self._letterbox_onnx(
                frame,
                self._onnx_input_width,
                self._onnx_input_height,
            )
            outputs = self._onnx_session.run(
                [self._onnx_output_name],
                {self._onnx_input_name: tensor},
            )
            if not outputs:
                return []
            return self._decode_onnx_output(
                outputs[0],
                frame.shape,
                scale,
                pad_left,
                pad_top,
            )

        logger.debug("PERSON_DETECT_START backend=%s device=%s imgsz=%s", self.backend, self.device, self.imgsz)
        result = self.model.predict(
            frame,
            imgsz=self.imgsz,
            classes=[0],
            conf=self.conf,
            device=self.device,
            max_det=20,
            verbose=False,
        )[0]

        detections = []
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return detections

        xyxy = boxes.xyxy.detach().cpu().numpy()
        confidences = boxes.conf.detach().cpu().numpy()

        for bbox, confidence in zip(xyxy, confidences):
            x1, y1, x2, y2 = [int(v) for v in bbox]
            if x2 - x1 < MIN_PERSON_SIZE or y2 - y1 < MIN_PERSON_SIZE:
                continue
            detections.append(
                {
                    "bbox": (x1, y1, x2, y2),
                    "confidence": float(confidence),
                }
            )

        return detections


@dataclass
class PersonTrack:
    track_id: int
    bbox: tuple[int, int, int, int]
    velocity: tuple[float, float, float, float] = (0, 0, 0, 0)
    appearance: Optional[np.ndarray] = None
    misses: int = 0
    hits: int = 1
    age: int = 1
    student_id: Optional[int] = None
    student_code: Optional[str] = None
    student_name: Optional[str] = None
    identity_confidence: float = 0.0
    candidate_student_id: Optional[int] = None
    candidate_hits: int = 0
    last_face_frame: int = -1
    last_reid_frame: int = -REID_INTERVAL
    last_seen_frame: int = 0
    previous_foot: Optional[tuple[float, float]] = None
    last_crossing: Optional[str] = None
    last_crossing_frame: int = -10_000
    metadata: dict = field(default_factory=dict)
    # Small, RAM-only recognition backlog. Samples are quality-ranked and
    # discarded with the track; they never touch disk.
    face_cache: list[dict] = field(default_factory=list, repr=False)
    face_cache_finalized: bool = False

    def cache_face_sample(
        self,
        crop,
        bbox,
        quality,
        frame_no,
        max_samples=FACE_CACHE_MAX_SAMPLES,
    ):
        if crop is None or getattr(crop, "size", 0) == 0:
            return False

        if not self.face_cache:
            # A completed/cleared cache may be populated again if the track is
            # still unresolved; allow a fresh finalization cycle for new samples.
            self.face_cache_finalized = False
        elif self.face_cache_finalized:
            # Do not mutate a cache while its immutable snapshot is being voted.
            return False

        sample = {
            "crop": np.ascontiguousarray(crop, dtype=np.uint8).copy(),
            "bbox": tuple(int(v) for v in bbox),
            "quality": float(quality),
            "frame_no": int(frame_no),
            "embedding": None,
        }

        self.face_cache.append(sample)
        self.face_cache.sort(key=lambda item: item["quality"], reverse=True)
        if len(self.face_cache) > int(max_samples):
            del self.face_cache[int(max_samples):]
        return True

    def snapshot_face_cache(self):
        # The worker owns the track, while a finalizer may process a snapshot
        # asynchronously. Copying here prevents later mutations from leaking
        # into the final decision.
        return [
            {
                "crop": sample["crop"].copy(),
                "bbox": tuple(sample["bbox"]),
                "quality": float(sample["quality"]),
                "frame_no": int(sample["frame_no"]),
                "embedding": None if sample.get("embedding") is None else np.asarray(sample["embedding"], dtype=np.float32).copy(),
            }
            for sample in self.face_cache
        ]

    def clear_face_cache(self):
        self.face_cache.clear()
        self.face_cache_finalized = True

    @profile.stage("track_predict")
    def predict(self, frame_shape):
        x1, y1, x2, y2 = self.bbox
        dx, dy, dw, dh = self.velocity
        width = max(1.0, (x2 - x1) + dw)
        height = max(1.0, (y2 - y1) + dh)
        h, w = frame_shape[:2]
        nx1 = max(0, min(int(x1 + dx), w - 1))
        ny1 = max(0, min(int(y1 + dy), h - 1))
        nx2 = min(w, int(nx1 + width))
        ny2 = min(h, int(ny1 + height))
        self.bbox = (nx1, ny1, nx2, ny2)

    def update_geometry(self, bbox, frame_no):
        old = self.bbox
        old_w = max(1, old[2] - old[0])
        old_h = max(1, old[3] - old[1])
        new_w = max(1, bbox[2] - bbox[0])
        new_h = max(1, bbox[3] - bbox[1])
        measured = (
            bbox[0] - old[0],
            bbox[1] - old[1],
            new_w - old_w,
            new_h - old_h,
        )
        self.velocity = tuple(
            0.55 * previous + 0.45 * current
            for previous, current in zip(self.velocity, measured)
        )
        self.bbox = bbox
        self.misses = 0
        self.hits += 1
        self.age += 1
        self.last_seen_frame = frame_no

    def update_appearance(self, embedding, frame_no):
        if embedding is None:
            return
        embedding = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if embedding.size == 0:
            return
        norm = max(float(np.linalg.norm(embedding)), 1e-8)
        embedding = embedding / norm

        if self.appearance is None:
            self.appearance = embedding
        else:
            updated = (
                (1.0 - APPEARANCE_EMA_ALPHA) * self.appearance
                + APPEARANCE_EMA_ALPHA * embedding
            )
            self.appearance = updated / max(float(np.linalg.norm(updated)), 1e-8)

        self.last_reid_frame = frame_no


class SmartPersonTracker:
    """Motion + appearance tracker. It keeps an identity across short occlusions."""

    def __init__(self, reid=None, reid_interval=REID_INTERVAL):
        self.reid = reid
        self.reid_interval = max(1, int(reid_interval))
        self.tracks: dict[int, PersonTrack] = {}
        self.next_track_id = 1
        self.last_reid_ms = 0.0
        self.last_reid_reason = "not_run"

    def _batch_embeddings(self, frame, detections):
        if self.reid is None or not detections:
            return [None] * len(detections)

        crops = []
        valid = []
        for index, detection in enumerate(detections):
            crop = crop_bbox(frame, detection["bbox"])
            if crop is None:
                continue
            crops.append(crop)
            valid.append(index)

        embeddings = [None] * len(detections)
        if not crops:
            return embeddings

        batch = self.reid.embed_batch(crops)
        for index, embedding in zip(valid, batch):
            embeddings[index] = embedding
        return embeddings

    def _associate(self, detections, detection_embeddings):
        candidates = []
        for track_id, track in self.tracks.items():
            for index, detection in enumerate(detections):
                det_bbox = detection["bbox"]
                motion = iou(track.bbox, det_bbox)
                appearance = cosine(track.appearance, detection_embeddings[index])
                has_appearance = (
                    track.appearance is not None
                    and detection_embeddings[index] is not None
                )

                if has_appearance:
                    if motion < ASSOCIATION_IOU_GATE and appearance < ASSOCIATION_REID_GATE:
                        continue
                    score = 0.58 * motion + 0.42 * appearance
                    if appearance >= ASSOCIATION_REID_STRONG:
                        score += 0.12
                else:
                    if motion < ASSOCIATION_IOU_GATE:
                        continue
                    score = motion

                candidates.append((score, motion, appearance, track_id, index))

        candidates.sort(reverse=True)
        assignments = {}
        used_tracks = set()
        used_detections = set()

        for score, motion, appearance, track_id, index in candidates:
            if track_id in used_tracks or index in used_detections:
                continue
            assignments[index] = track_id
            used_tracks.add(track_id)
            used_detections.add(index)

        return assignments, used_detections

    @profile.stage("tracker_update_including_reid")
    def update(self, detections, frame, frame_no):
        for track in self.tracks.values():
            if track.misses == 0:
                track.predict(frame.shape)

        interval_due = frame_no % self.reid_interval == 0
        no_tracks = not self.tracks
        # A stale track with no appearance must not force OSNet to run on every
        # detector call for up to MAX_MISSES frames. Only bootstrap appearance
        # for tracks that were active on the previous detector update.
        active_track_needs_appearance = any(
            track.appearance is None and track.misses == 0
            for track in self.tracks.values()
        )
        need_reid = (
            self.reid is not None
            and (interval_due or no_tracks or active_track_needs_appearance)
        )
        self.last_reid_ms = 0.0
        if need_reid:
            if interval_due:
                self.last_reid_reason = "interval"
            elif no_tracks:
                self.last_reid_reason = "bootstrap"
            else:
                self.last_reid_reason = "active_missing_appearance"
            reid_started = time.perf_counter()
            embeddings = self._batch_embeddings(frame, detections)
            self.last_reid_ms = (time.perf_counter() - reid_started) * 1000.0
        else:
            self.last_reid_reason = "throttled"
            embeddings = [None] * len(detections)

        assignments, used = self._associate(detections, embeddings)
        next_tracks: dict[int, PersonTrack] = {}

        for index, detection in enumerate(detections):
            bbox = detection["bbox"]
            track_id = assignments.get(index)

            if track_id is None:
                track_id = self.next_track_id
                self.next_track_id += 1
                track = PersonTrack(
                    track_id=track_id,
                    bbox=bbox,
                    last_seen_frame=frame_no,
                )
                profile.track_created(track)
            else:
                track = self.tracks[track_id]
                track.update_geometry(bbox, frame_no)

            if embeddings[index] is not None:
                track.update_appearance(embeddings[index], frame_no)

            track.metadata["detector_confidence"] = detection["confidence"]
            next_tracks[track_id] = track

        for track_id, track in self.tracks.items():
            if track_id in next_tracks:
                continue
            track.misses += 1
            track.age += 1
            if track.misses <= MAX_MISSES:
                next_tracks[track_id] = track

        self.tracks = next_tracks
        return list(self.tracks.values())


class LineCrossing:
    """Virtual gate with backwards-compatible simple lines or a drawn segment."""

    def __init__(
        self,
        orientation="vertical",
        position=0.50,
        enter_direction="positive",
        cooldown_frames=25,
        primary_direction="enter",
        line_points=None,
        inside_side=None,
    ):
        self.orientation = orientation
        self.position = float(max(0.05, min(0.95, position)))
        self.enter_direction = enter_direction
        self.primary_direction = (
            primary_direction if primary_direction in {"enter", "exit"} else "enter"
        )
        self.cooldown_frames = int(cooldown_frames)
        self.line_points = None
        if line_points is not None:
            try:
                candidate = tuple(float(value) for value in line_points)
                if (
                    len(candidate) == 4
                    and all(0.0 <= value <= 1.0 for value in candidate)
                    and (candidate[0] - candidate[2]) ** 2 + (candidate[1] - candidate[3]) ** 2 > 1e-6
                ):
                    self.line_points = candidate
            except (TypeError, ValueError):
                pass
        try:
            side = int(inside_side)
        except (TypeError, ValueError):
            side = 0
        self.inside_side = side if side in (-1, 1) else None

    def _coordinate(self, point, frame_shape):
        return point[0] if self.orientation == "vertical" else point[1]

    def _line_coordinate(self, frame_shape):
        h, w = frame_shape[:2]
        return (w if self.orientation == "vertical" else h) * self.position

    @staticmethod
    def _signed_distance(point, a, b):
        # Sign matches the UI's inside-side calculation.
        return (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])

    def _check_drawn_line(self, track, frame_shape, frame_no, previous, current):
        h, w = frame_shape[:2]
        x1, y1, x2, y2 = self.line_points
        a, b = (x1 * w, y1 * h), (x2 * w, y2 * h)
        vx, vy = b[0] - a[0], b[1] - a[1]
        length_sq = vx * vx + vy * vy
        if length_sq < 4.0:
            return None

        d0 = self._signed_distance(previous, a, b)
        d1 = self._signed_distance(current, a, b)
        old_side = track.metadata.get("_drawn_gate_side")
        if d0 > 0:
            previous_side = 1
        elif d0 < 0:
            previous_side = -1
        else:
            previous_side = old_side if old_side in (-1, 1) else None

        if d1 == 0:
            if previous_side in (-1, 1):
                track.metadata["_drawn_gate_side"] = previous_side
            return None
        current_side = 1 if d1 > 0 else -1
        if previous_side not in (-1, 1):
            track.metadata["_drawn_gate_side"] = current_side
            return None
        if current_side == previous_side:
            track.metadata["_drawn_gate_side"] = current_side
            return None

        # Require the movement to intersect the finite drawn segment, not its extension.
        denominator = d0 - d1
        if abs(denominator) < 1e-8:
            track.metadata["_drawn_gate_side"] = current_side
            return None
        t = d0 / denominator
        ix = previous[0] + t * (current[0] - previous[0])
        iy = previous[1] + t * (current[1] - previous[1])
        u = ((ix - a[0]) * vx + (iy - a[1]) * vy) / length_sq
        track.metadata["_drawn_gate_side"] = current_side
        if not (-0.01 <= u <= 1.01):
            return None
        if frame_no - track.last_crossing_frame < self.cooldown_frames:
            return None

        direction = "enter" if current_side == self.inside_side else "exit"
        track.last_crossing = direction
        track.last_crossing_frame = frame_no
        return direction

    def check(self, track: PersonTrack, frame_shape, frame_no):
        # Require a temporally stable track before a physical crossing can become an event.
        if track.hits < 3 or track.age < 3 or track.misses != 0:
            return None
        current = foot_point(track.bbox)
        previous = track.previous_foot
        track.previous_foot = current
        if previous is None:
            return None
        if self.line_points is not None and self.inside_side in (-1, 1):
            if abs(current[0] - previous[0]) < 1.0 and abs(current[1] - previous[1]) < 1.0:
                return None
            return self._check_drawn_line(track, frame_shape, frame_no, previous, current)

        # Backwards-compatible path for existing vertical/horizontal settings.
        previous_value = self._coordinate(previous, frame_shape)
        current_value = self._coordinate(current, frame_shape)
        line = self._line_coordinate(frame_shape)
        if abs(current_value - previous_value) < 1.0:
            return None
        crossed = (
            previous_value < line <= current_value
            if self.enter_direction == "positive"
            else previous_value > line >= current_value
        )
        opposite = (
            previous_value > line >= current_value
            if self.enter_direction == "positive"
            else previous_value < line <= current_value
        )
        if not crossed and not opposite:
            return None
        if frame_no - track.last_crossing_frame < self.cooldown_frames:
            return None
        if crossed:
            direction = self.primary_direction
        else:
            direction = "exit" if self.primary_direction == "enter" else "enter"
        track.last_crossing = direction
        track.last_crossing_frame = frame_no
        return direction


def make_person_model_candidates(base_dir: Path):
    # Keep model discovery portable. Never depend on a developer's
    # machine-specific Trash/media paths.
    candidates = [
        # Prefer the torch-free CPU inference backend whenever its export exists.
        base_dir / "models" / "yolo26n.onnx",
        base_dir / "yolo26n.onnx",
        base_dir / "models" / "yolo26n.pt",
        base_dir / "models" / "yolov8n.pt",
        base_dir / "yolo26n.pt",  # Backward compatibility with older checkouts.
        base_dir / "yolov8n.pt",
    ]
    return [path for path in candidates if path.exists()]
