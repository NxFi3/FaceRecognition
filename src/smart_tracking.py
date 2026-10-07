"""Person tracking, appearance Re-ID, identity persistence and line crossing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


REID_INTERVAL = 6
MAX_MISSES = 18
MIN_PERSON_SIZE = 45
ASSOCIATION_IOU_GATE = 0.08
ASSOCIATION_REID_GATE = 0.70
ASSOCIATION_REID_STRONG = 0.84
APPEARANCE_EMA_ALPHA = 0.18
IDENTITY_CONFIRMATIONS = 2


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
    """Ultralytics person detector kept behind a tiny stable API."""

    def __init__(
        self,
        model_path: str | Path,
        device: str = "cpu",
        conf: float = 0.30,
        imgsz: int = 480,
    ):
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "Person tracking needs ultralytics in the active Python environment."
            ) from exc

        self.model = YOLO(str(model_path))
        self.device = device
        self.conf = float(conf)
        self.imgsz = max(320, int(imgsz))

    def detect(self, frame):
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

    def update(self, detections, frame, frame_no):
        for track in self.tracks.values():
            if track.misses == 0:
                track.predict(frame.shape)

        need_reid = (
            self.reid is not None
            and (
                frame_no % self.reid_interval == 0
                or not self.tracks
                or any(track.appearance is None for track in self.tracks.values())
            )
        )
        if need_reid:
            embeddings = self._batch_embeddings(frame, detections)
        else:
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
    """Virtual vertical/horizontal gate line with debounced crossings."""

    def __init__(
        self,
        orientation="vertical",
        position=0.50,
        enter_direction="positive",
        cooldown_frames=25,
        primary_direction="enter",
    ):
        self.orientation = orientation
        self.position = float(max(0.05, min(0.95, position)))
        self.enter_direction = enter_direction
        self.primary_direction = (
            primary_direction if primary_direction in {"enter", "exit"} else "enter"
        )
        self.cooldown_frames = int(cooldown_frames)

    def _coordinate(self, point, frame_shape):
        h, w = frame_shape[:2]
        return point[0] if self.orientation == "vertical" else point[1]

    def _line_coordinate(self, frame_shape):
        h, w = frame_shape[:2]
        return (w if self.orientation == "vertical" else h) * self.position

    def check(self, track: PersonTrack, frame_shape, frame_no):
        # Require a temporally stable track before a physical crossing can
        # become an attendance/security event. This suppresses one-frame
        # detector noise around the gate.
        if track.hits < 3 or track.age < 3 or track.misses != 0:
            return None

        current = foot_point(track.bbox)
        previous = track.previous_foot
        track.previous_foot = current
        if previous is None:
            return None

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
    candidates = [
        base_dir / "yolo26n.pt",
        base_dir / "models" / "yolo26n.pt",
        Path.home() / ".local/share/Trash/files/FaceRecognition/yolo26n.pt",
        Path("/media/itsnxfi3/AAE2C7A6E2C7755B/.Trash-1000/files/yolo26n.pt"),
    ]
    return [path for path in candidates if path.exists()]
