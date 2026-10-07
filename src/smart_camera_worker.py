"""Production smart camera worker: person tracking + face identity + events."""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from .camera_capture import LatestFrameCapture

import cv2
import dlib
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .database import add_event, get_setting, mark_attendance
from .smart_events import AttendanceEventEngine, AttendancePresenceState
from .smart_tracking import (
    LineCrossing,
    PersonDetector,
    SmartPersonTracker,
    make_person_model_candidates,
)


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSON_DETECTION_INTERVAL_GPU = 2
PERSON_DETECTION_INTERVAL_CPU = 3
FACE_DETECTION_INTERVAL = 2
MAX_FACE_RECOGNITION_JOBS = 2
FACE_RECOGNITION_INTERVAL = 5
FACE_CONFIRMATIONS = 2
PREVIEW_FPS = 15.0
PERSON_IMAGE_SIZE = 480
LINE_POSITION = 0.50
LINE_ORIENTATION = "vertical"
LINE_ENTER_DIRECTION = "positive"

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
        import face_recognition_models

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
        shape = predictor(crop, rect)
        return np.asarray(
            encoder.compute_face_descriptor(crop, shape, 0),
            dtype=np.float32,
        )

    @staticmethod
    def _recognize_task(matcher, crop, bbox, track_id):
        return track_id, matcher.recognize_crop(crop, bbox)

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
            return configured

        candidates = make_person_model_candidates(
            Path(__file__).resolve().parent.parent
        )
        if candidates:
            return str(candidates[0])

        raise FileNotFoundError(
            "YOLO person model not found. Put yolo26n.pt in the project root "
            "or set the 'person_model_path' application setting."
        )

    def _record_event(self, **kwargs):
        event_id = add_event(**kwargs)
        event = dict(kwargs)
        event["id"] = event_id
        self.event_added.emit(event)
        return event_id

    def run(self):
        self._running = True
        capture = None
        futures = set()

        try:
            # Importing app at runtime avoids a module-level circular import.
            from .app import (
                CAMERA_HEIGHT,
                CAMERA_WIDTH,
                YUNET_MODEL_PATH,
                YUNET_NMS_THRESHOLD,
                YUNET_SCORE_THRESHOLD,
                YUNET_TOP_K,
                FaceMatcher,
                _open_camera,
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
                _open_camera,
                raw_bayer=use_raw_bayer,
            )
            capture.start()

            actual_width = capture.width or CAMERA_WIDTH
            actual_height = capture.height or CAMERA_HEIGHT
            actual_fps = capture.fps
            face_detector.setInputSize((actual_width, actual_height))

            resolved_person_device = person_device or "cpu"
            person_detection_interval = (
                PERSON_DETECTION_INTERVAL_GPU
                if str(resolved_person_device).lower().startswith("cuda")
                else PERSON_DETECTION_INTERVAL_CPU
            )

            person_detector = PersonDetector(
                person_model,
                device=resolved_person_device,
                conf=float(get_setting("person_confidence", "0.42")),
                imgsz=PERSON_IMAGE_SIZE,
            )
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
            )

            matcher = FaceMatcher()
            event_engine = AttendanceEventEngine(
                add_event_callback=self._record_event,
                mark_attendance_callback=self._mark_attendance_event,
                get_setting_callback=get_setting,
                state=self.presence_state,
            )

            executor = _get_shared_face_executor()

            frame_no = 0
            last_capture_sequence = 0
            last_person_detections = []
            last_face_detections = []
            last_preview = 0.0
            fps_counter = 0
            fps_start = time.perf_counter()
            fps = 0.0

            self.status_changed.emit(
                f"Smart camera online • Camera {self.camera_slot} "
                f"({camera_role}) • {actual_width}x{actual_height} • "
                f"{actual_fps:.0f} FPS • tracks + face identity + "
                f"line events • {len(matcher.students)} students • {reid_status}"
            )

            while self._running:
                sequence, frame = capture.wait_for_frame(
                    last_capture_sequence,
                    timeout=1.0,
                )
                if frame is None:
                    continue

                # The capture thread may have consumed many physical frames
                # while inference was busy. Process only the newest snapshot so
                # latency stays bounded instead of building a stale-frame queue.
                last_capture_sequence = sequence
                frame_no += 1

                for future in tuple(futures):
                    if not future.done():
                        continue

                    futures.remove(future)
                    try:
                        track_id, result = future.result()
                    except Exception as exc:
                        self.error.emit(f"Face recognition worker failed: {exc}")
                        continue

                    track = tracker.tracks.get(track_id)
                    if track is None:
                        continue

                    student_index, distance, stability = result
                    track.last_face_frame = frame_no

                    if student_index is None:
                        track.candidate_student_id = None
                        track.candidate_hits = 0
                        continue

                    student = matcher.students[student_index]
                    student_id = student["id"]

                    if track.candidate_student_id == student_id:
                        track.candidate_hits += 1
                    else:
                        track.candidate_student_id = student_id
                        track.candidate_hits = 1

                    confidence = self._confidence(distance, stability)

                    if (
                        track.student_id == student_id
                        or track.candidate_hits >= FACE_CONFIRMATIONS
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
                        # A later face match supersedes a previous Re-ID hand-off
                        # as the stronger identity source.
                        track.metadata.pop("global_identity", None)
                        track.metadata["identity_source"] = "face"
                        track.metadata["face_distance"] = distance
                        track.metadata["face_stability"] = stability
                        track.metadata["identity_reconciled"] = was_reconciled

                if frame_no % person_detection_interval == 0:
                    previous_tracks = dict(tracker.tracks)
                    last_person_detections = person_detector.detect(frame)
                    active_tracks = tracker.update(
                        last_person_detections,
                        frame,
                        frame_no,
                    )

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
                                confidence = max(
                                    0.0,
                                    min(1.0, identity_match.similarity),
                                )
                                event_engine.on_reid_identity(
                                    track,
                                    identity_match.student_id,
                                    identity_match.student_code,
                                    identity_match.student_name,
                                    confidence,
                                    camera_id,
                                    {
                                        "similarity": round(
                                            identity_match.similarity,
                                            4,
                                        ),
                                        "score": round(
                                            identity_match.score,
                                            4,
                                        ),
                                        "source_camera": identity_match.source_camera,
                                        "source_track_id": identity_match.source_track_id,
                                        "reason": identity_match.reason,
                                    },
                                )
                                track.metadata["global_identity"] = {
                                    "similarity": identity_match.similarity,
                                    "source_camera": identity_match.source_camera,
                                    "source_track_id": identity_match.source_track_id,
                                }
                                track.metadata["identity_source"] = "global_reid"

                        event_engine.on_track_seen(track, camera_id)

                        if camera_role == "observe":
                            continue

                        direction = line.check(track, frame.shape, frame_no)
                        if direction is not None:
                            event_engine.on_crossing(
                                track,
                                direction,
                                camera_id,
                            )
                else:
                    for track in tracker.tracks.values():
                        if track.misses == 0:
                            track.predict(frame.shape)

                if (
                    tracker.tracks
                    and frame_no % FACE_DETECTION_INTERVAL == 0
                ):
                    _, detections = face_detector.detect(frame)
                    last_face_detections = []
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
                                bbox[2] - bbox[0] >= 28
                                and bbox[3] - bbox[1] >= 28
                            ):
                                last_face_detections.append(bbox)

                submitted_tracks = set()
                if (
                    not tracker.tracks
                    or frame_no % FACE_DETECTION_INTERVAL != 0
                ):
                    last_face_detections_for_recognition = []
                else:
                    last_face_detections_for_recognition = last_face_detections

                for face_bbox in last_face_detections_for_recognition:
                    track = self._find_track_for_face(
                        face_bbox,
                        list(tracker.tracks.values()),
                    )
                    if track is None or track.misses > 0:
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
                    track.last_face_frame = frame_no
                    futures.add(
                        executor.submit(
                            self._recognize_task,
                            matcher,
                            face_crop,
                            local_bbox,
                            track.track_id,
                        )
                    )
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
                    # Annotation is only needed for the UI preview. Keeping it
                    # off the inference path avoids drawing several rectangles
                    # and text layers on every processing frame.
                    if line.orientation == "vertical":
                        line_x = int(frame.shape[1] * line.position)
                        cv2.line(
                            frame,
                            (line_x, 0),
                            (line_x, frame.shape[0]),
                            (255, 200, 0),
                            3,
                        )
                    else:
                        line_y = int(frame.shape[0] * line.position)
                        cv2.line(
                            frame,
                            (0, line_y),
                            (frame.shape[1], line_y),
                            (255, 200, 0),
                            3,
                        )

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

                    cv2.putText(
                        frame,
                        f"FPS {fps:.1f} | tracks {len(tracker.tracks)} | {reid_status}",
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

        except Exception as exc:
            self.error.emit(str(exc))
            self.status_changed.emit("Smart camera stopped")
        finally:
            for future in futures:
                future.cancel()
            if capture is not None:
                capture.stop()
            self._running = False

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
