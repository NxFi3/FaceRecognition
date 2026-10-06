import cv2
import time
import numpy as np
import face_recognition
from ultralytics import YOLO

from database import init_db, add_student, add_embedding

YOLO_MODEL = "yolov8n-face.pt"
CAMERA_ID = 0

CONFIDENCE = 0.35
IMAGE_SIZE = 640

NUM_SAMPLES = 5
MIN_FACE_SIZE = 80

# Minimum time between accepted samples
SAMPLE_INTERVAL = 1.0


def get_embedding(frame, bbox):
    x1, y1, x2, y2 = bbox

    # Convert BGR -> RGB
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    # face_recognition location format
    location = (y1, x2, y2, x1)

    encodings = face_recognition.face_encodings(
        rgb, known_face_locations=[location], model="small", num_jitters=1
    )

    if not encodings:
        return None

    return encodings[0].astype(np.float32)


def main():

    init_db()

    print()
    print("=" * 50)
    print("       SCHOOL ATTENDANCE - ENROLL")
    print("=" * 50)
    print()

    student_code = input("Student code: ").strip()
    name = input("Student name: ").strip()

    if not student_code or not name:
        print("Student code and name are required.")
        return

    print()
    print("Loading face detector...")

    model = YOLO(YOLO_MODEL)

    cap = cv2.VideoCapture(CAMERA_ID)

    if not cap.isOpened():
        print("ERROR: Could not open camera.")
        return

    # Try to use 640x480
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    embeddings = []

    last_sample_time = 0

    print("Camera ready.")
    print()
    print("Look at the camera.")
    print("Move your head slightly between samples.")
    print("Press Q to cancel.")

    while len(embeddings) < NUM_SAMPLES:

        ret, frame = cap.read()

        if not ret:
            print("ERROR: Failed to read camera.")
            break

        results = model.predict(frame, imgsz=IMAGE_SIZE, conf=CONFIDENCE, verbose=False)

        faces = []

        if results and results[0].boxes is not None:

            boxes = results[0].boxes

            for box in boxes:

                coords = box.xyxy[0].cpu().numpy()

                x1, y1, x2, y2 = coords.astype(int)

                x1 = max(0, x1)
                y1 = max(0, y1)
                x2 = min(frame.shape[1], x2)
                y2 = min(frame.shape[0], y2)

                width = x2 - x1
                height = y2 - y1

                if width < MIN_FACE_SIZE or height < MIN_FACE_SIZE:
                    continue

                faces.append((x1, y1, x2, y2))

        # We require exactly one face
        if len(faces) == 1:

            bbox = faces[0]

            x1, y1, x2, y2 = bbox

            # Draw face box
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

            current_time = time.time()

            # Don't capture samples too quickly
            if current_time - last_sample_time >= SAMPLE_INTERVAL:

                embedding = get_embedding(frame, bbox)

                if embedding is not None:

                    embeddings.append(embedding)

                    last_sample_time = current_time

                    print(f"Sample {len(embeddings)}/{NUM_SAMPLES} captured")

        elif len(faces) == 0:

            cv2.putText(
                frame,
                "No face detected",
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 0, 255),
                2,
            )

        else:

            cv2.putText(
                frame,
                "Only one person should be visible",
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 0, 255),
                2,
            )

        # Progress
        progress = f"Samples: {len(embeddings)}/{NUM_SAMPLES}"

        cv2.putText(
            frame,
            progress,
            (20, frame.shape[0] - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
        )

        cv2.imshow("Enrollment", frame)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            print()
            print("Enrollment cancelled.")
            cap.release()
            cv2.destroyAllWindows()
            return

    cap.release()
    cv2.destroyAllWindows()

    if len(embeddings) != NUM_SAMPLES:
        print("Enrollment failed.")
        return

    print()
    print("Saving student...")

    try:

        student_id = add_student(student_code, name)

    except Exception as e:

        print()
        print("ERROR: Could not create student.")
        print(e)

        return

    for embedding in embeddings:

        add_embedding(student_id, embedding)

    print()
    print("Enrollment successful!")
    print()
    print(f"Student : {name}")
    print(f"Code    : {student_code}")
    print(f"Samples : {len(embeddings)}")
    print()


if __name__ == "__main__":
    main()
