import csv
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import face_recognition
import numpy as np
from PySide6.QtCore import QDate, QLibraryInfo, QThread, QTranslator, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
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
        get_setting,
        get_student,
        get_student_class,
        init_db,
        list_academic_years,
        list_classes,
        list_majors,
        list_students,
        mark_attendance,
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
        get_setting,
        get_student,
        get_student_class,
        init_db,
        list_academic_years,
        list_classes,
        list_majors,
        list_students,
        mark_attendance,
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

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

MODEL_PATH = BASE_DIR / "yolov8n-face.pt"

CAMERA_ID = 0
CAMERA_NAME = "camera_0"

CONFIDENCE = 0.35

# Capture more pixels so faces remain usable at longer distances.
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720

# 768 gives the face detector more detail for small/distant faces while
# keeping the detector substantially cheaper than very large inference sizes.
IMAGE_SIZE = 768

# Recognition is intentionally conservative: a single unusually-close
# embedding must not be enough to identify a person.
FACE_DISTANCE_THRESHOLD = 0.46
FACE_STABILITY_THRESHOLD = 0.49

RECOGNITION_INTERVAL = 5
RECOGNITION_CONFIRMATIONS = 3
REVALIDATION_INTERVAL = 25

# Do not discard usable distant faces too early.
MIN_FACE_SIZE = 45

# Enrollment should cover natural variation rather than five near-identical frames.
ENROLLMENT_SAMPLES = 12
ENROLLMENT_SAMPLE_INTERVAL = 1.0
ENROLLMENT_MIN_FACE_SIZE = 80

# Face crops are padded before encoding so important facial context is retained.
FACE_CROP_PADDING = 0.20
SMALL_FACE_THRESHOLD = 120
SMALL_FACE_UPSCALE = 2.0

UI_FRAME_LIMIT = 1.0 / 30.0


def frame_to_qimage(frame):
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, channels = rgb.shape
    image = QImage(
        rgb.data,
        w,
        h,
        channels * w,
        QImage.Format_RGB888,
    )
    return image.copy()


def face_embedding(frame, bbox):
    """
    Encode a padded face crop instead of the raw full-frame bounding box.

    Small/distant faces are upscaled locally before encoding. This improves
    the amount of facial detail seen by dlib without upscaling the whole frame.
    """
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

    crop_h, crop_w = crop.shape[:2]

    # Only upscale small faces. Large faces already contain enough detail.
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
        self.refresh()

    def refresh(self):
        self.students = load_students()

    def recognize(self, frame, bbox):
        """
        Return:
            (student_index, best_distance, stability_score)

        We use both the closest embedding and the mean of the three
        closest embeddings for the same student. This prevents one
        accidental embedding match from being enough.
        """
        if not self.students:
            return None, None, None

        embedding = face_embedding(frame, bbox)
        if embedding is None:
            return None, None, None

        candidates = []

        for index, student in enumerate(self.students):
            if not student["embeddings"]:
                continue

            distances = [
                float(np.linalg.norm(embedding - known_embedding))
                for known_embedding in student["embeddings"]
            ]

            distances.sort()

            best_distance = distances[0]
            top_k = distances[: min(3, len(distances))]
            stability_score = float(np.mean(top_k))

            candidates.append(
                (index, best_distance, stability_score)
            )

        if not candidates:
            return None, None, None

        candidates.sort(key=lambda item: (item[1], item[2]))

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

        return best_index, best_distance, best_stability




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

    def run(self):
        self._running = True
        self.status_changed.emit("Loading face detector...")

        cap = None

        try:
            if not MODEL_PATH.exists():
                raise FileNotFoundError(
                    f"Model not found: {MODEL_PATH}"
                )

            model = __import__("ultralytics").YOLO(str(MODEL_PATH))

            cap = cv2.VideoCapture(CAMERA_ID)

            if not cap.isOpened():
                raise RuntimeError("Could not open camera.")

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)

            actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

            matcher = FaceMatcher()
            tracks = {}

            frame_count = 0
            last_preview = 0.0
            fps = 0.0
            fps_counter = 0
            fps_start = time.time()

            self.status_changed.emit(
                f"Camera online • {actual_width}x{actual_height} • "
                f"{len(matcher.students)} students loaded"
            )

            while self._running:
                ret, frame = cap.read()

                if not ret:
                    self.error.emit("Camera frame failed.")
                    break

                frame_count += 1

                try:
                    results = model.track(
                        frame,
                        imgsz=IMAGE_SIZE,
                        conf=CONFIDENCE,
                        persist=True,
                        tracker="bytetrack.yaml",
                        verbose=False,
                    )
                except Exception as exc:
                    self.error.emit(f"Tracking error: {exc}")
                    break

                active_track_ids = set()

                if results and results[0].boxes is not None:
                    boxes = results[0].boxes

                    for box in boxes:
                        if box.id is None:
                            continue

                        track_id = int(box.id[0].item())
                        active_track_ids.add(track_id)

                        coords = box.xyxy[0].cpu().numpy().astype(int)
                        x1, y1, x2, y2 = coords

                        x1 = max(0, x1)
                        y1 = max(0, y1)
                        x2 = min(frame.shape[1], x2)
                        y2 = min(frame.shape[0], y2)

                        width = x2 - x1
                        height = y2 - y1

                        if width < MIN_FACE_SIZE or height < MIN_FACE_SIZE:
                            continue

                        if track_id not in tracks:
                            tracks[track_id] = {
                                "student_index": None,
                                "distance": None,
                                "stability": None,
                                "candidate_index": None,
                                "candidate_hits": 0,
                                "last_recognition": -RECOGNITION_INTERVAL,
                                "last_validation": -REVALIDATION_INTERVAL,
                            }

                        track = tracks[track_id]

                        if track["student_index"] is None:
                            should_recognize = (
                                frame_count - track["last_recognition"]
                                >= RECOGNITION_INTERVAL
                            )
                        else:
                            should_recognize = (
                                frame_count - track["last_validation"]
                                >= REVALIDATION_INTERVAL
                            )

                        if should_recognize:
                            track["last_recognition"] = frame_count

                            (
                                student_index,
                                distance,
                                stability,
                            ) = matcher.recognize(
                                frame,
                                (x1, y1, x2, y2),
                            )

                            if track["student_index"] is None:
          
                                if student_index == track["candidate_index"]:
                                    track["candidate_hits"] += 1
                                else:
                                    track["candidate_index"] = student_index
                                    track["candidate_hits"] = (
                                        1 if student_index is not None else 0
                                    )

                                if (
                                    student_index is not None
                                    and track["candidate_hits"]
                                    >= RECOGNITION_CONFIRMATIONS
                                ):
                                    track["student_index"] = student_index
                                    track["distance"] = distance
                                    track["stability"] = stability
                                    track["last_validation"] = frame_count

                                    student = matcher.students[student_index]
                                    student_id = student["id"]

                                    inserted = mark_attendance(
                                        student_id,
                                        CAMERA_NAME,
                                    )

                                    if inserted:
                                        timestamp = datetime.now().strftime(
                                            "%H:%M:%S"
                                        )

                                        self.attendance_added.emit(
                                            {
                                                "student_id": student_id,
                                                "student_code": student["student_code"],
                                                "name": student["name"],
                                                "time": timestamp,
                                                "camera": CAMERA_NAME,
                                            }
                                        )

                            else:
                                track["last_validation"] = frame_count

                                if (
                                    student_index != track["student_index"]
                                    or student_index is None
                                ):
                                    track["student_index"] = None
                                    track["distance"] = None
                                    track["stability"] = None
                                    track["candidate_index"] = student_index
                                    track["candidate_hits"] = (
                                        1 if student_index is not None else 0
                                    )
                                else:
                                    track["distance"] = distance
                                    track["stability"] = stability

                        if track["student_index"] is not None:
                            student = matcher.students[track["student_index"]]
                            name = student["name"]
                            distance = track["distance"]
                            stability = track["stability"]

                            label = (
                                f"{name} "
                                f"[{distance:.2f}/{stability:.2f}]"
                            )
                            box_color = (0, 220, 80)
                        elif track["candidate_index"] is not None:
                            candidate = matcher.students[track["candidate_index"]]
                            label = (
                                f"Confirming {candidate['name']} "
                                f"({track['candidate_hits']}/{RECOGNITION_CONFIRMATIONS})"
                            )
                            box_color = (0, 210, 255)
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

                disappeared = [
                    track_id
                    for track_id in tracks
                    if track_id not in active_track_ids
                ]

                for track_id in disappeared:
                    del tracks[track_id]

                fps_counter += 1
                elapsed = time.time() - fps_start

                if elapsed >= 1.0:
                    fps = fps_counter / elapsed
                    fps_counter = 0
                    fps_start = time.time()

                today = datetime.now().strftime("%Y-%m-%d")
                draw_text(frame, f"FPS: {fps:.1f}", (15, 28), 0.68)
                draw_text(frame, today, (15, 54), 0.52)

                now = time.time()

                if now - last_preview >= UI_FRAME_LIMIT:
                    self.frame_ready.emit(frame_to_qimage(frame))
                    last_preview = now

        except Exception as exc:
            self.error.emit(str(exc))
            self.status_changed.emit("Camera stopped")

        finally:
            if cap is not None:
                cap.release()

            self._running = False


class EnrollmentWorker(QThread):
    frame_ready = Signal(QImage)
    progress = Signal(int, int)
    capture_done = Signal(object)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False

    def stop(self):
        self._running = False

    def run(self):
        self._running = True
        cap = None

        try:
            if not MODEL_PATH.exists():
                raise FileNotFoundError(f"مدل پیدا نشد: {MODEL_PATH}")

            model = __import__("ultralytics").YOLO(str(MODEL_PATH))

            cap = cv2.VideoCapture(CAMERA_ID)

            if not cap.isOpened():
                raise RuntimeError("دوربین باز نشد.")

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)

            embeddings = []
            last_sample = 0.0

            while self._running and len(embeddings) < ENROLLMENT_SAMPLES:
                ret, frame = cap.read()
                if not ret:
                    raise RuntimeError("دریافت تصویر از دوربین ناموفق بود.")

                results = model.predict(
                    frame,
                    imgsz=IMAGE_SIZE,
                    conf=CONFIDENCE,
                    verbose=False,
                )

                faces = []

                if results and results[0].boxes is not None:
                    for box in results[0].boxes:
                        coords = box.xyxy[0].cpu().numpy().astype(int)
                        x1, y1, x2, y2 = coords

                        x1 = max(0, x1)
                        y1 = max(0, y1)
                        x2 = min(frame.shape[1], x2)
                        y2 = min(frame.shape[0], y2)

                        if (
                            x2 - x1 >= ENROLLMENT_MIN_FACE_SIZE
                            and y2 - y1 >= ENROLLMENT_MIN_FACE_SIZE
                        ):
                            faces.append((x1, y1, x2, y2))

                if len(faces) == 1:
                    bbox = faces[0]
                    now = time.time()

                    if now - last_sample >= ENROLLMENT_SAMPLE_INTERVAL:
                        # Encode clean camera pixels before drawing UI overlays.
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
                        "چهره‌ای پیدا نشد",
                        (20, 40),
                        0.72,
                        (0, 80, 255),
                        2,
                    )
                else:
                    draw_text(
                        frame,
                        "فقط یک نفر باید جلوی دوربین باشد",
                        (20, 40),
                        0.62,
                        (0, 80, 255),
                        2,
                    )

                draw_text(
                    frame,
                    f"نمونه‌ها: {len(embeddings)}/{ENROLLMENT_SAMPLES}",
                    (20, frame.shape[0] - 20),
                    0.68,
                )

                self.frame_ready.emit(frame_to_qimage(frame))

            if len(embeddings) == ENROLLMENT_SAMPLES:
                self.capture_done.emit(embeddings)
            else:
                self.capture_done.emit(None)

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

        else:
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

    def start_capture(self):
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

        self.worker = EnrollmentWorker()
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
                Qt.SmoothTransformation,
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

        size = min(self.width(), self.height()) - 55
        size = max(100, size)
        x = 20
        y = int((self.height() - size) / 2)
        pie_rect = self.rect().adjusted(20, 15, -20, -15)

        pie_size = min(pie_rect.width(), pie_rect.height())
        pie_x = int(pie_rect.center().x() - pie_size / 2)
        pie_y = int(pie_rect.center().y() - pie_size / 2)

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

        legend_x = min(
            self.width() - 170,
            int(self.width() * 0.60),
        )
        legend_y = 35
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        init_db()

        self.setWindowTitle("سامانه حضور و غیاب مدرسه")
        self.resize(1450, 900)

        self.camera_worker = None
        self.current_page = 0

        self.build_ui()
        self.refresh_all()
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

        main_layout.addWidget(content, 1)
        self.show_page(0)

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
        camera_title = QLabel("نمای زنده دوربین")
        camera_title.setAlignment(Qt.AlignRight)
        camera_layout.addWidget(camera_title)

        self.camera_preview = QLabel("در انتظار دوربین...")
        self.camera_preview.setAlignment(Qt.AlignCenter)
        self.camera_preview.setMinimumSize(860, 500)
        self.camera_preview.setObjectName("cameraPreview")
        camera_layout.addWidget(self.camera_preview, 1)
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

    def show_page(self, index):
        self.current_page = index
        self.stack.setCurrentIndex(index)

        titles = [
            "داشبورد",
            "دانش‌آموزان",
            "کلاس‌ها",
            "رشته‌ها",
            "حضور و غیاب",
            "گزارش‌ها",
        ]
        self.page_title.setText(titles[index])

        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)

        if index == 0:
            self.refresh_dashboard()
        elif index == 1:
            self.refresh_students()
        elif index == 2:
            self.refresh_classes()
        elif index == 3:
            self.refresh_majors()
        elif index == 4:
            self.refresh_attendance_filters()
            self.refresh_attendance()
        elif index == 5:
            self.refresh_report_filters()
            self.refresh_report()

    def start_camera(self):
        if self.camera_worker is not None and self.camera_worker.isRunning():
            return

        self.camera_worker = CameraWorker()
        self.camera_worker.frame_ready.connect(self.update_camera_preview)
        self.camera_worker.attendance_added.connect(self.attendance_event)
        self.camera_worker.status_changed.connect(self.update_camera_status)
        self.camera_worker.error.connect(self.camera_error)
        self.camera_worker.start()

    def stop_camera(self):
        worker = self.camera_worker
        if worker is None:
            return

        if worker.isRunning():
            worker.stop()
            worker.wait(5000)

        self.camera_worker = None

    def restart_camera(self):
        self.stop_camera()
        self.start_camera()

    def update_camera_preview(self, image):
        pixmap = QPixmap.fromImage(image)
        self.camera_preview.setPixmap(
            pixmap.scaled(
                self.camera_preview.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

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
        dialog = EnrollmentDialog(show_student_fields=True, parent=self)
        self.stop_camera()

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

        dialog = EnrollmentDialog(
            show_student_fields=False,
            student_id=student_id,
            parent=self,
        )
        self.stop_camera()

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
        self.stop_camera()
        event.accept()


def ensure_admin_access():
    if not password_is_configured():
        dialog = AdminPasswordDialog(mode="setup")
        return dialog.exec() == QDialog.Accepted

    login = AdminPasswordDialog(mode="login")
    return login.exec() == QDialog.Accepted


def main():
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

        QLineEdit, QDateEdit, QComboBox {
            background: #ffffff;
            color: #253244;
            border: 1px solid #cbd6e2;
            border-radius: 9px;
            padding: 9px 11px;
            selection-background-color: #dbe9f8;
            selection-color: #1e3045;
        }

        QLineEdit:focus, QDateEdit:focus, QComboBox:focus {
            border: 1px solid #78a7d5;
            background: #ffffff;
        }

        QLineEdit:disabled, QDateEdit:disabled, QComboBox:disabled {
            background: #eef2f6;
            color: #9aa7b5;
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
