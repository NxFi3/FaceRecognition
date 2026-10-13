"""Live experiment: person tracking + OSNet appearance, independent of faces.

Run from the repository root:
    python -m src.reid_camera_test

This is NOT wired into the production app. It answers one question first:
can a lightweight person detector + OSNet keep a person's track stable
when the face is unavailable/occluded?
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

try:
    from .engine.reid import OSNetReID
except ImportError:
    from engine.reid import OSNetReID

BASE_DIR = Path(__file__).resolve().parent.parent
PERSON_MODEL = BASE_DIR / "models" / "yolo26n.pt"
if not PERSON_MODEL.exists():
    PERSON_MODEL = BASE_DIR / "yolo26n.pt"

CAMERA_INDEX = 0
DETECT_EVERY = 2
REID_EVERY = 5
MAX_MISSES = 12
IOU_THRESHOLD = 0.15
APPEARANCE_THRESHOLD = 0.72
EMA_ALPHA = 0.20


def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    aa = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    ab = max(0, bx2 - bx1) * max(0, by2 - by1)
    return inter / max(aa + ab - inter, 1)


def cosine(a, b):
    if a is None or b is None:
        return 0.0
    return float(np.dot(a, b) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-8))


def main():
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "Install optional Re-ID dependencies first: "
            "pip install -r requirements-reid.txt"
        ) from exc

    if not PERSON_MODEL.exists():
        raise SystemExit(f"Missing person detector: {PERSON_MODEL}")

    detector = YOLO(str(PERSON_MODEL))
    reid = OSNetReID(model_name="osnet_x0_25", device="cpu")

    cap = cv2.VideoCapture(CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    tracks = {}
    next_id = 1
    frame_no = 0
    last = time.perf_counter()
    fps = 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        frame_no += 1
        detections = []

        if frame_no % DETECT_EVERY == 0:
            result = detector.predict(
                frame,
                classes=[0],
                conf=0.35,
                verbose=False,
            )[0]
            if result.boxes is not None:
                for box in result.boxes.xyxy.cpu().numpy():
                    x1, y1, x2, y2 = map(int, box)
                    if x2 > x1 and y2 > y1:
                        detections.append((x1, y1, x2, y2))

            # Greedy association. This is intentionally simple: we are
            # evaluating appearance stability, not replacing the production
            # tracker yet.
            assigned = set()
            for track_id, track in list(tracks.items()):
                best = None
                best_score = -1.0
                for idx, bbox in enumerate(detections):
                    if idx in assigned:
                        continue
                    motion = iou(track["bbox"], bbox)
                    appearance = cosine(track.get("embedding"), track.get("candidate"))
                    score = motion + 0.35 * appearance
                    if score > best_score:
                        best_score = score
                        best = idx

                if best is not None and (
                    iou(track["bbox"], detections[best]) >= IOU_THRESHOLD
                    or cosine(track.get("embedding"), track.get("candidate")) >= APPEARANCE_THRESHOLD
                ):
                    track["bbox"] = detections[best]
                    track["misses"] = 0
                    assigned.add(best)
                else:
                    track["misses"] += 1

            for idx, bbox in enumerate(detections):
                if idx in assigned:
                    continue
                tracks[next_id] = {
                    "bbox": bbox,
                    "misses": 0,
                    "embedding": None,
                    "candidate": None,
                    "last_reid": -REID_EVERY,
                }
                next_id += 1

            tracks = {
                tid: t for tid, t in tracks.items()
                if t["misses"] <= MAX_MISSES
            }

        for track_id, track in tracks.items():
            x1, y1, x2, y2 = track["bbox"]
            if frame_no - track["last_reid"] >= REID_EVERY:
                crop = frame[max(0, y1):min(frame.shape[0], y2),
                             max(0, x1):min(frame.shape[1], x2)]
                if crop.size:
                    embedding = reid.embed(crop)
                    track["candidate"] = embedding
                    if track["embedding"] is None:
                        track["embedding"] = embedding
                    else:
                        updated = (1.0 - EMA_ALPHA) * track["embedding"] + EMA_ALPHA * embedding
                        track["embedding"] = updated / max(np.linalg.norm(updated), 1e-8)
                    track["last_reid"] = frame_no

            similarity = cosine(track.get("embedding"), track.get("candidate"))
            label = f"Person {track_id}  app={similarity:.2f} miss={track['misses']}"
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(frame, label, (x1, max(20, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)

        now = time.perf_counter()
        dt = now - last
        last = now
        if dt > 0:
            fps = 0.9 * fps + 0.1 / dt if fps else 1.0 / dt

        cv2.putText(frame, f"FPS {fps:.1f} | tracks {len(tracks)} | q=OSNet x0.25",
                    (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
        cv2.imshow("FaceRecognition - OSNet ReID experiment", frame)

        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
