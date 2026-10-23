import cv2
import time
import numpy as np
try:
    from .engine.model_assets import install_legacy_model_compat
except ImportError:
    from engine.model_assets import install_legacy_model_compat
install_legacy_model_compat()

import face_recognition
from ultralytics import YOLO

from database import init_db, add_student, add_embedding, get_setting

YOLO_MODEL = "yolov8n-face.pt"
CAMERA_ID = 0

CONFIDENCE = 0.35
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
IMAGE_SIZE = 768

# More samples improve robustness across angle, distance, and lighting.
NUM_SAMPLES = 12
MIN_FACE_SIZE = 80

FACE_CROP_PADDING = 0.20
SMALL_FACE_THRESHOLD = 120
SMALL_FACE_UPSCALE = 2.0

# Minimum time between accepted samples
SAMPLE_INTERVAL = 1.0


def get_embedding(frame, bbox):
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

    if max(face_w, face_h) < SMALL_FACE_THRESHOLD:
        crop = cv2.resize(
            crop,
            None,
            fx=SMALL_FACE_UPSCALE,
            fy=SMALL_FACE_UPSCALE,
            interpolation=cv2.INTER_CUBIC,
        )
        scale = SMALL_FACE_UPSCALE
    else:
        scale = 1.0

    local_x1 = int((x1 - crop_x1) * scale)
    local_y1 = int((y1 - crop_y1) * scale)
    local_x2 = int((x2 - crop_x1) * scale)
    local_y2 = int((y2 - crop_y1) * scale)

    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    location = (local_y1, local_x2, local_y2, local_x1)

    encodings = face_recognition.face_encodings(
        rgb,
        known_face_locations=[location],
        model="small",
        num_jitters=1,
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

    # Enrollment follows the same Camera 1 device selected in the application.
    # Fall back to the legacy single-camera setting for older installations.
    camera_id = int(
        get_setting(
            "camera_1_id",
            get_setting("camera_id", CAMERA_ID),
        )
    )
    target_fps = int(
        get_setting(
            "camera_1_target_fps",
            get_setting("camera_target_fps", 60),
        )
    )

    cap = cv2.VideoCapture(camera_id)
    if not cap.isOpened():
        print(f"ERROR: Could not open Camera 1 (device {camera_id}).")
        return

    if target_fps >= 60:
        cap.set(
            cv2.CAP_PROP_FOURCC,
            cv2.VideoWriter_fourcc(*"MJPG"),
        )
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, target_fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    print(f"Camera resolution: {actual_width}x{actual_height}")

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

            current_time = time.time()

            # Capture the clean camera pixels before drawing UI overlays.
            if current_time - last_sample_time >= SAMPLE_INTERVAL:

                embedding = get_embedding(frame, bbox)

                if embedding is not None:
                    embeddings.append(embedding)
                    last_sample_time = current_time

            # Draw face box after embedding so the overlay never contaminates it.
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
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
