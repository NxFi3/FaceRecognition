import cv2
import time
import numpy as np
import face_recognition
from datetime import datetime
from ultralytics import YOLO

from database import init_db, load_students, mark_attendance

# =========================
# Configuration
# =========================

YOLO_MODEL = "yolov8n-face.pt"

CAMERA_ID = 0

CONFIDENCE = 0.35
IMAGE_SIZE = 640

FACE_DISTANCE_THRESHOLD = 0.50

# Retry recognition for an unknown track
RECOGNITION_INTERVAL = 5

MIN_FACE_SIZE = 60

CAMERA_NAME = "camera_0"


# =========================
# Load database
# =========================

init_db()

students = load_students()

if not students:
    print("ERROR: No students found in database.")
    print("Run enroll.py first.")
    raise SystemExit(1)


print()
print("=" * 50)
print("       SCHOOL ATTENDANCE SYSTEM")
print("=" * 50)
print()

print(f"Loaded {len(students)} students.")

total_embeddings = sum(len(student["embeddings"]) for student in students)

print(f"Loaded {total_embeddings} face embeddings.")
print()


# =========================
# Prepare recognition data
# =========================

student_ids = []
student_names = []
student_codes = []
student_embeddings = []

for student in students:

    student_ids.append(student["id"])
    student_names.append(student["name"])
    student_codes.append(student["student_code"])
    student_embeddings.append(student["embeddings"])


# =========================
# Recognition
# =========================


def recognize_face(frame, bbox):

    x1, y1, x2, y2 = bbox

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    location = (y1, x2, y2, x1)

    encodings = face_recognition.face_encodings(
        rgb, known_face_locations=[location], model="small", num_jitters=1
    )

    if not encodings:
        return None, None

    query_embedding = encodings[0]

    best_distance = float("inf")
    best_student = None

    # Compare against every student's embeddings
    for student_index, embeddings in enumerate(student_embeddings):

        for known_embedding in embeddings:

            distance = np.linalg.norm(query_embedding - known_embedding)

            if distance < best_distance:
                best_distance = distance
                best_student = student_index

    if best_student is not None and best_distance < FACE_DISTANCE_THRESHOLD:
        return best_student, best_distance

    return None, best_distance


# =========================
# Camera
# =========================

model = YOLO(YOLO_MODEL)

cap = cv2.VideoCapture(CAMERA_ID)

if not cap.isOpened():
    print("ERROR: Could not open camera.")
    raise SystemExit(1)


cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)


# =========================
# Tracking state
# =========================

tracks = {}

frame_count = 0

fps = 0
fps_counter = 0
fps_start = time.time()


print("Camera started.")
print("Press Q to quit.")
print()


# =========================
# Main loop
# =========================

while True:

    ret, frame = cap.read()

    if not ret:
        print("ERROR: Camera frame failed.")
        break

    frame_count += 1

    # ---------------------------------
    # YOLO + ByteTrack
    # ---------------------------------

    results = model.track(
        frame,
        imgsz=IMAGE_SIZE,
        conf=CONFIDENCE,
        persist=True,
        tracker="bytetrack.yaml",
        verbose=False,
    )

    active_track_ids = set()

    if results and results[0].boxes is not None:

        boxes = results[0].boxes

        for box in boxes:

            if box.id is None:
                continue

            track_id = int(box.id[0].item())

            active_track_ids.add(track_id)

            coords = box.xyxy[0].cpu().numpy()

            x1, y1, x2, y2 = coords.astype(int)

            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(frame.shape[1], x2)
            y2 = min(frame.shape[0], y2)

            width = x2 - x1
            height = y2 - y1

            # Ignore tiny faces
            if width < MIN_FACE_SIZE or height < MIN_FACE_SIZE:
                continue

            # ---------------------------------
            # Initialize track
            # ---------------------------------

            if track_id not in tracks:

                tracks[track_id] = {
                    "student_index": None,
                    "distance": None,
                    "last_recognition": -RECOGNITION_INTERVAL,
                }

            track = tracks[track_id]

            # ---------------------------------
            # Recognition
            # ---------------------------------

            should_recognize = (
                track["student_index"] is None
                and frame_count - track["last_recognition"] >= RECOGNITION_INTERVAL
            )

            if should_recognize:

                track["last_recognition"] = frame_count

                student_index, distance = recognize_face(frame, (x1, y1, x2, y2))

                if student_index is not None:

                    track["student_index"] = student_index
                    track["distance"] = distance

                    student_id = student_ids[student_index]

                    inserted = mark_attendance(student_id, CAMERA_NAME)

                    if inserted:

                        now = datetime.now().strftime("%H:%M:%S")

                        print(
                            f"[ATTENDANCE] "
                            f"{student_names[student_index]} "
                            f"({student_codes[student_index]}) "
                            f"→ {now}"
                        )

            # ---------------------------------
            # Display
            # ---------------------------------

            if track["student_index"] is not None:

                student_index = track["student_index"]

                name = student_names[student_index]

                distance = track["distance"]

                label = f"{name} " f"[{distance:.2f}]"

                box_color = (0, 255, 0)

            else:

                label = "Recognizing..."

                box_color = (0, 255, 255)

            cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)

            cv2.putText(
                frame,
                label,
                (x1, max(25, y1 - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                box_color,
                2,
            )

            # Track ID
            cv2.putText(
                frame,
                f"ID: {track_id}",
                (x1, y2 + 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                box_color,
                1,
            )

    # ---------------------------------
    # Remove disappeared tracks
    # ---------------------------------

    disappeared = [track_id for track_id in tracks if track_id not in active_track_ids]

    for track_id in disappeared:
        del tracks[track_id]

    # ---------------------------------
    # FPS
    # ---------------------------------

    fps_counter += 1

    elapsed = time.time() - fps_start

    if elapsed >= 1.0:

        fps = fps_counter / elapsed

        fps_counter = 0
        fps_start = time.time()

    cv2.putText(
        frame,
        f"FPS: {fps:.1f}",
        (15, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
    )

    # ---------------------------------
    # Date
    # ---------------------------------

    today = datetime.now().strftime("%Y-%m-%d")

    cv2.putText(
        frame, today, (15, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2
    )

    cv2.imshow("School Attendance", frame)

    key = cv2.waitKey(1) & 0xFF

    if key == ord("q"):
        break


# =========================
# Cleanup
# =========================

cap.release()
cv2.destroyAllWindows()

print()
print("Attendance system stopped.")
