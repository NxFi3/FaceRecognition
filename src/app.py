import csv
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import face_recognition
import numpy as np
from PySide6.QtCore import QDate, QThread, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
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
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from database import (
        add_embedding,
        add_student,
        delete_embeddings,
        delete_student,
        get_attendance,
        get_attendance_stats,
        get_student,
        init_db,
        list_students,
        mark_attendance,
        update_student,
        load_students,
    )
except ModuleNotFoundError:
    from .database import (
        add_embedding,
        add_student,
        delete_embeddings,
        delete_student,
        get_attendance,
        get_attendance_stats,
        get_student,
        init_db,
        list_students,
        mark_attendance,
        update_student,
        load_students,
    )


BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = BASE_DIR / "yolov8n-face.pt"

CAMERA_ID = 0
CAMERA_NAME = "camera_0"

CONFIDENCE = 0.35
IMAGE_SIZE = 640
# Recognition is intentionally conservative: a single unusually-close
# embedding must not be enough to identify a person.
FACE_DISTANCE_THRESHOLD = 0.46
FACE_STABILITY_THRESHOLD = 0.49

RECOGNITION_INTERVAL = 5
RECOGNITION_CONFIRMATIONS = 3
REVALIDATION_INTERVAL = 25

MIN_FACE_SIZE = 60

ENROLLMENT_SAMPLES = 5
ENROLLMENT_SAMPLE_INTERVAL = 1.0
ENROLLMENT_MIN_FACE_SIZE = 80

UI_FRAME_LIMIT = 1.0 / 30.0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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
    x1, y1, x2, y2 = bbox

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    location = (y1, x2, y2, x1)

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


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------


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

        # The face must be close to at least one enrolled sample AND
        # reasonably close to several samples of the same person.
        if (
            best_distance > FACE_DISTANCE_THRESHOLD
            or best_stability > FACE_STABILITY_THRESHOLD
        ):
            return None, best_distance, best_stability

        # When there is more than one student, also require separation
        # from the runner-up. This blocks ambiguous nearest-neighbor matches.
        if len(candidates) > 1:
            _, second_distance, second_stability = candidates[1]

            if (
                second_distance - best_distance < 0.05
                or second_stability - best_stability < 0.04
            ):
                return None, best_distance, best_stability

        return best_index, best_distance, best_stability


# ---------------------------------------------------------------------------
# Live camera worker
# ---------------------------------------------------------------------------


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

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            matcher = FaceMatcher()
            tracks = {}

            frame_count = 0
            last_preview = 0.0
            fps = 0.0
            fps_counter = 0
            fps_start = time.time()

            self.status_changed.emit(
                f"Camera online • {len(matcher.students)} students loaded"
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
                                # Unknown -> candidate -> confirmed.
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

                                # Re-validation failed or points to another
                                # person: drop the cached identity and start
                                # confirmation again instead of keeping a
                                # stale identity forever.
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


# ---------------------------------------------------------------------------
# Enrollment worker
# ---------------------------------------------------------------------------


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
                raise FileNotFoundError(
                    f"Model not found: {MODEL_PATH}"
                )

            model = __import__("ultralytics").YOLO(str(MODEL_PATH))

            cap = cv2.VideoCapture(CAMERA_ID)

            if not cap.isOpened():
                raise RuntimeError("Could not open camera.")

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            embeddings = []
            last_sample = 0.0

            while self._running and len(embeddings) < ENROLLMENT_SAMPLES:
                ret, frame = cap.read()

                if not ret:
                    raise RuntimeError("Failed to read camera.")

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
                    x1, y1, x2, y2 = bbox

                    cv2.rectangle(
                        frame,
                        (x1, y1),
                        (x2, y2),
                        (0, 220, 80),
                        2,
                    )

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

                elif len(faces) == 0:
                    draw_text(
                        frame,
                        "No face detected",
                        (20, 38),
                        0.72,
                        (0, 80, 255),
                        2,
                    )
                else:
                    draw_text(
                        frame,
                        "Only one person should be visible",
                        (20, 38),
                        0.62,
                        (0, 80, 255),
                        2,
                    )

                progress_text = (
                    f"Samples: {len(embeddings)}/{ENROLLMENT_SAMPLES}"
                )
                draw_text(
                    frame,
                    progress_text,
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


# ---------------------------------------------------------------------------
# Enrollment dialog
# ---------------------------------------------------------------------------


class EnrollmentDialog(QDialog):
    def __init__(self, show_student_fields=True, parent=None):
        super().__init__(parent)

        self.show_student_fields = show_student_fields
        self.embeddings = None
        self.worker = None

        self.setWindowTitle(
            "Add Student" if show_student_fields else "Re-enroll Face"
        )
        self.resize(900, 680)

        root = QVBoxLayout(self)

        if show_student_fields:
            form = QFormLayout()

            self.code_edit = QLineEdit()
            self.name_edit = QLineEdit()

            self.code_edit.setPlaceholderText("e.g. 140123")
            self.name_edit.setPlaceholderText("Student name")

            form.addRow("Student code", self.code_edit)
            form.addRow("Name", self.name_edit)

            root.addLayout(form)
        else:
            title = QLabel(
                "Capture five new samples for this student."
            )
            title.setObjectName("sectionTitle")
            root.addWidget(title)

        self.preview = QLabel("Camera preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(800, 480)
        self.preview.setObjectName("cameraPreview")
        root.addWidget(self.preview, 1)

        self.progress_label = QLabel("Ready to capture 5 samples.")
        self.progress_label.setObjectName("muted")
        root.addWidget(self.progress_label)

        buttons = QHBoxLayout()

        self.start_button = QPushButton("Start Capture")
        self.cancel_button = QPushButton("Cancel")

        self.start_button.clicked.connect(self.start_capture)
        self.cancel_button.clicked.connect(self.reject)

        buttons.addStretch()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)

        root.addLayout(buttons)

    def start_capture(self):
        if self.show_student_fields:
            if not self.code_edit.text().strip():
                QMessageBox.warning(self, "Missing data", "Student code is required.")
                return

            if not self.name_edit.text().strip():
                QMessageBox.warning(self, "Missing data", "Student name is required.")
                return

        self.start_button.setEnabled(False)

        self.progress_label.setText(
            "Camera started. Look at the camera and move slightly between samples."
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
            f"Captured {current}/{total} samples."
        )

    def capture_error(self, message):
        QMessageBox.critical(self, "Capture error", message)

    def capture_finished(self, embeddings):
        if embeddings is None:
            self.progress_label.setText("Capture cancelled.")
            self.start_button.setEnabled(True)
            return

        self.embeddings = embeddings

        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(5000)

        self.progress_label.setText("Capture complete.")
        self.accept()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)

        event.accept()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        init_db()

        self.setWindowTitle("School Attendance System")
        self.resize(1380, 860)

        self.camera_worker = None
        self.current_page = 0

        self.build_ui()
        self.refresh_all()
        self.start_camera()

    # ----------------------------
    # UI
    # ----------------------------

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)

        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(18, 22, 18, 18)
        side_layout.setSpacing(8)

        title = QLabel("ATTENDANCE")
        title.setObjectName("brand")
        side_layout.addWidget(title)

        subtitle = QLabel("School Recognition")
        subtitle.setObjectName("brandSub")
        side_layout.addWidget(subtitle)

        side_layout.addSpacing(24)

        self.nav_buttons = []

        for text, index in (
            ("Dashboard", 0),
            ("Students", 1),
            ("Attendance", 2),
            ("Export", 3),
        ):
            button = QPushButton(text)
            button.setCheckable(True)
            button.clicked.connect(
                lambda checked=False, i=index: self.show_page(i)
            )
            self.nav_buttons.append(button)
            side_layout.addWidget(button)

        side_layout.addStretch()

        self.camera_status = QLabel("Camera starting...")
        self.camera_status.setObjectName("sidebarStatus")
        self.camera_status.setWordWrap(True)
        side_layout.addWidget(self.camera_status)

        main_layout.addWidget(sidebar)

        content = QFrame()
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 22, 28, 28)

        self.page_title = QLabel()
        self.page_title.setObjectName("pageTitle")
        content_layout.addWidget(self.page_title)

        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)

        self.build_dashboard_page()
        self.build_students_page()
        self.build_attendance_page()
        self.build_export_page()

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

        card, self.total_students_label = self.make_card("Total students")
        cards.addWidget(card)

        card, self.present_label = self.make_card("Present today")
        cards.addWidget(card)

        card, self.absent_label = self.make_card("Absent today")
        cards.addWidget(card)

        layout.addLayout(cards)

        camera_row = QHBoxLayout()
        camera_row.setSpacing(16)

        camera_box = QFrame()
        camera_box.setObjectName("panel")
        camera_layout = QVBoxLayout(camera_box)

        camera_title = QLabel("Live camera")
        camera_title.setObjectName("sectionTitle")
        camera_layout.addWidget(camera_title)

        self.camera_preview = QLabel("Waiting for camera...")
        self.camera_preview.setAlignment(Qt.AlignCenter)
        self.camera_preview.setMinimumSize(860, 500)
        self.camera_preview.setObjectName("cameraPreview")
        camera_layout.addWidget(self.camera_preview, 1)

        camera_row.addWidget(camera_box, 3)

        recent_box = QFrame()
        recent_box.setObjectName("panel")
        recent_layout = QVBoxLayout(recent_box)

        recent_title = QLabel("Recent attendance")
        recent_title.setObjectName("sectionTitle")
        recent_layout.addWidget(recent_title)

        self.recent_table = QTableWidget(0, 3)
        self.recent_table.setHorizontalHeaderLabels(
            ["Code", "Name", "Time"]
        )
        self.configure_table(self.recent_table)
        recent_layout.addWidget(self.recent_table)

        camera_row.addWidget(recent_box, 1)

        layout.addLayout(camera_row, 1)

        self.stack.addWidget(page)

    def build_students_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        toolbar = QHBoxLayout()

        add_button = QPushButton("Add student")
        edit_button = QPushButton("Edit")
        reenroll_button = QPushButton("Re-enroll face")
        delete_button = QPushButton("Delete")
        refresh_button = QPushButton("Refresh")

        add_button.clicked.connect(self.add_student_dialog)
        edit_button.clicked.connect(self.edit_selected_student)
        reenroll_button.clicked.connect(self.reenroll_selected_student)
        delete_button.clicked.connect(self.delete_selected_student)
        refresh_button.clicked.connect(self.refresh_students)

        toolbar.addWidget(add_button)
        toolbar.addWidget(edit_button)
        toolbar.addWidget(reenroll_button)
        toolbar.addWidget(delete_button)
        toolbar.addStretch()
        toolbar.addWidget(refresh_button)

        layout.addLayout(toolbar)

        self.students_table = QTableWidget(0, 5)
        self.students_table.setHorizontalHeaderLabels(
            ["ID", "Code", "Name", "Faces", "Created"]
        )
        self.configure_table(self.students_table)
        self.students_table.doubleClicked.connect(
            lambda index: self.edit_selected_student()
        )

        layout.addWidget(self.students_table, 1)

        self.stack.addWidget(page)

    def build_attendance_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        toolbar = QHBoxLayout()

        toolbar.addWidget(QLabel("Date"))

        self.attendance_date = QDateEdit()
        self.attendance_date.setCalendarPopup(True)
        self.attendance_date.setDate(QDate.currentDate())
        self.attendance_date.dateChanged.connect(
            lambda _: self.refresh_attendance()
        )

        toolbar.addWidget(self.attendance_date)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_attendance)
        toolbar.addWidget(refresh_button)
        toolbar.addStretch()

        self.attendance_summary = QLabel()
        self.attendance_summary.setObjectName("muted")
        toolbar.addWidget(self.attendance_summary)

        layout.addLayout(toolbar)

        self.attendance_table = QTableWidget(0, 4)
        self.attendance_table.setHorizontalHeaderLabels(
            ["Code", "Name", "Time", "Camera"]
        )
        self.configure_table(self.attendance_table)

        layout.addWidget(self.attendance_table, 1)

        self.stack.addWidget(page)

    def build_export_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        panel = QFrame()
        panel.setObjectName("panel")

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(24, 24, 24, 24)
        panel_layout.setSpacing(16)

        heading = QLabel("Export attendance")
        heading.setObjectName("sectionTitle")
        panel_layout.addWidget(heading)

        info = QLabel(
            "Choose a date and export the attendance shown in the database."
        )
        info.setObjectName("muted")
        info.setWordWrap(True)
        panel_layout.addWidget(info)

        form = QFormLayout()

        self.export_date = QDateEdit()
        self.export_date.setCalendarPopup(True)
        self.export_date.setDate(QDate.currentDate())
        form.addRow("Date", self.export_date)

        panel_layout.addLayout(form)

        buttons = QHBoxLayout()

        csv_button = QPushButton("Export CSV")
        xlsx_button = QPushButton("Export Excel")

        csv_button.clicked.connect(lambda: self.export_attendance("csv"))
        xlsx_button.clicked.connect(lambda: self.export_attendance("xlsx"))

        buttons.addWidget(csv_button)
        buttons.addWidget(xlsx_button)
        buttons.addStretch()

        panel_layout.addLayout(buttons)

        self.export_status = QLabel("No export yet.")
        self.export_status.setObjectName("muted")
        self.export_status.setWordWrap(True)
        panel_layout.addWidget(self.export_status)

        panel_layout.addStretch()

        layout.addWidget(panel)
        layout.addStretch()

        self.stack.addWidget(page)

    def configure_table(self, table):
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)

        table.setMinimumHeight(300)

    # ----------------------------
    # Navigation
    # ----------------------------

    def show_page(self, index):
        self.current_page = index
        self.stack.setCurrentIndex(index)

        titles = [
            "Dashboard",
            "Students",
            "Attendance",
            "Export",
        ]

        self.page_title.setText(titles[index])

        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)

        if index == 0:
            self.refresh_dashboard()
        elif index == 1:
            self.refresh_students()
        elif index == 2:
            self.refresh_attendance()

    # ----------------------------
    # Camera
    # ----------------------------

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
        self.camera_status.setText(f"Camera error: {message}")

    def attendance_event(self, event):
        self.refresh_dashboard()
        self.refresh_attendance()

        self.camera_status.setText(
            f'Attendance marked • {event["name"]} ({event["student_code"]}) • {event["time"]}'
        )

    # ----------------------------
    # Dashboard
    # ----------------------------

    def refresh_dashboard(self):
        total, present, absent = get_attendance_stats()

        self.total_students_label.setText(str(total))
        self.present_label.setText(str(present))
        self.absent_label.setText(str(absent))

        rows = get_attendance()
        recent = rows[-12:][::-1]

        self.recent_table.setRowCount(len(recent))

        for row, (_, code, name, timestamp, _) in enumerate(recent):
            self.recent_table.setItem(row, 0, QTableWidgetItem(code))
            self.recent_table.setItem(row, 1, QTableWidgetItem(name))
            self.recent_table.setItem(
                row,
                2,
                QTableWidgetItem(timestamp.split(" ")[-1]),
            )

    # ----------------------------
    # Students
    # ----------------------------

    def refresh_students(self):
        rows = list_students()

        self.students_table.setRowCount(len(rows))

        for row, (student_id, code, name, face_count, created_at) in enumerate(rows):
            values = [
                str(student_id),
                code,
                name,
                str(face_count),
                created_at,
            ]

            for column, value in enumerate(values):
                self.students_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(value),
                )

    def selected_student_id(self):
        row = self.students_table.currentRow()

        if row < 0:
            return None

        item = self.students_table.item(row, 0)

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
                )

                for embedding in dialog.embeddings:
                    add_embedding(student_id, embedding)

            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Could not add student",
                    str(exc),
                )
                return

            QMessageBox.information(
                self,
                "Student added",
                f"{dialog.name_edit.text().strip()} was added with "
                f"{len(dialog.embeddings)} face samples.",
            )

            self.refresh_all()

        finally:
            self.start_camera()

    def edit_selected_student(self):
        student_id = self.selected_student_id()

        if student_id is None:
            QMessageBox.information(
                self,
                "Select student",
                "Select a student first.",
            )
            return

        student = get_student(student_id)

        if student is None:
            return

        _, old_code, old_name, _ = student

        dialog = QDialog(self)
        dialog.setWindowTitle("Edit student")
        dialog.resize(460, 220)

        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        code_edit = QLineEdit(old_code)
        name_edit = QLineEdit(old_name)

        form.addRow("Student code", code_edit)
        form.addRow("Name", name_edit)

        layout.addLayout(form)

        buttons = QHBoxLayout()
        save = QPushButton("Save")
        cancel = QPushButton("Cancel")

        save.clicked.connect(dialog.accept)
        cancel.clicked.connect(dialog.reject)

        buttons.addStretch()
        buttons.addWidget(save)
        buttons.addWidget(cancel)

        layout.addLayout(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        if not code_edit.text().strip() or not name_edit.text().strip():
            QMessageBox.warning(
                self,
                "Missing data",
                "Both student code and name are required.",
            )
            return

        try:
            update_student(
                student_id,
                code_edit.text().strip(),
                name_edit.text().strip(),
            )
            self.refresh_all()
            self.restart_camera()

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Could not update student",
                str(exc),
            )

    def reenroll_selected_student(self):
        student_id = self.selected_student_id()

        if student_id is None:
            QMessageBox.information(
                self,
                "Select student",
                "Select a student first.",
            )
            return

        student = get_student(student_id)

        if student is None:
            return

        name = student[2]

        answer = QMessageBox.question(
            self,
            "Re-enroll face",
            f"Replace all face samples for {name}?",
        )

        if answer != QMessageBox.Yes:
            return

        dialog = EnrollmentDialog(
            show_student_fields=False,
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
                "Face data updated",
                f"{len(dialog.embeddings)} new face samples saved for {name}.",
            )

            self.refresh_all()

        finally:
            self.start_camera()

    def delete_selected_student(self):
        student_id = self.selected_student_id()

        if student_id is None:
            QMessageBox.information(
                self,
                "Select student",
                "Select a student first.",
            )
            return

        student = get_student(student_id)

        if student is None:
            return

        name = student[2]
        code = student[1]

        answer = QMessageBox.warning(
            self,
            "Delete student",
            f"Delete {name} ({code})?\n\n"
            "This also removes the stored face samples and attendance records.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        try:
            delete_student(student_id)
            self.refresh_all()
            self.restart_camera()

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Could not delete student",
                str(exc),
            )

    # ----------------------------
    # Attendance
    # ----------------------------

    def selected_attendance_date(self):
        return self.attendance_date.date().toString("yyyy-MM-dd")

    def refresh_attendance(self):
        date = self.selected_attendance_date()
        rows = get_attendance(date)

        self.attendance_table.setRowCount(len(rows))

        for row, (_, code, name, timestamp, camera) in enumerate(rows):
            values = [
                code,
                name,
                timestamp.split(" ")[-1],
                camera,
            ]

            for column, value in enumerate(values):
                self.attendance_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(value),
                )

        total, present, absent = get_attendance_stats(date)

        self.attendance_summary.setText(
            f"Present: {present} • Absent: {absent} • Total students: {total}"
        )

    # ----------------------------
    # Export
    # ----------------------------

    def selected_export_date(self):
        return self.export_date.date().toString("yyyy-MM-dd")

    def export_attendance(self, fmt):
        date = self.selected_export_date()
        rows = get_attendance(date)

        if not rows:
            QMessageBox.information(
                self,
                "Nothing to export",
                f"No attendance records for {date}.",
            )
            return

        default_name = f"attendance_{date}.{fmt}"

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export attendance",
            str(BASE_DIR / "exports" / default_name),
            "CSV files (*.csv)" if fmt == "csv" else "Excel files (*.xlsx)",
        )

        if not path:
            return

        Path(path).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:
            if fmt == "csv":
                with open(
                    path,
                    "w",
                    newline="",
                    encoding="utf-8-sig",
                ) as file:
                    writer = csv.writer(file)
                    writer.writerow(
                        ["student_code", "name", "timestamp", "camera_id"]
                    )

                    for _, code, name, timestamp, camera in rows:
                        writer.writerow(
                            [code, name, timestamp, camera]
                        )

            else:
                try:
                    from openpyxl import Workbook
                except ImportError:
                    QMessageBox.warning(
                        self,
                        "Excel export unavailable",
                        "Install openpyxl first:\n\npip install openpyxl",
                    )
                    return

                workbook = Workbook()
                sheet = workbook.active
                sheet.title = "Attendance"

                sheet.append(
                    ["student_code", "name", "timestamp", "camera_id"]
                )

                for _, code, name, timestamp, camera in rows:
                    sheet.append(
                        [code, name, timestamp, camera]
                    )

                workbook.save(path)

            self.export_status.setText(
                f"Exported {len(rows)} records to:\n{path}"
            )

            QMessageBox.information(
                self,
                "Export complete",
                f"Exported {len(rows)} records.",
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Export failed",
                str(exc),
            )

    # ----------------------------
    # Refresh / shutdown
    # ----------------------------

    def refresh_all(self):
        self.refresh_dashboard()
        self.refresh_students()
        self.refresh_attendance()

    def closeEvent(self, event):
        self.stop_camera()
        event.accept()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def main():
    app = QApplication(sys.argv)

    app.setStyle("Fusion")

    app.setStyleSheet(
        """
        QWidget {
            font-family: Inter, Segoe UI, sans-serif;
            font-size: 14px;
        }

        QMainWindow, QWidget#content {
            background: #111318;
        }

        #sidebar {
            background: #181b21;
            border-right: 1px solid #292d35;
        }

        #brand {
            font-size: 20px;
            font-weight: 800;
            letter-spacing: 1px;
        }

        #brandSub {
            color: #8e96a3;
            font-size: 12px;
        }

        #sidebar QPushButton {
            text-align: left;
            padding: 11px 14px;
            border: none;
            border-radius: 9px;
            color: #b7bec9;
            background: transparent;
        }

        #sidebar QPushButton:hover {
            background: #22262e;
            color: white;
        }

        #sidebar QPushButton:checked {
            background: #2c313b;
            color: white;
        }

        #sidebarStatus {
            color: #87909e;
            font-size: 11px;
            padding: 8px 2px;
        }

        #pageTitle {
            font-size: 28px;
            font-weight: 800;
            padding-bottom: 4px;
        }

        #sectionTitle {
            font-size: 16px;
            font-weight: 700;
        }

        #muted {
            color: #8e96a3;
        }

        #statCard, #panel {
            background: #191c22;
            border: 1px solid #292e37;
            border-radius: 14px;
        }

        #statTitle {
            color: #8e96a3;
            font-size: 12px;
        }

        #statValue {
            font-size: 34px;
            font-weight: 800;
        }

        #cameraPreview {
            background: #0b0d10;
            border: 1px solid #242932;
            border-radius: 10px;
            color: #6f7784;
        }

        QTableWidget {
            background: #15181d;
            border: 1px solid #292e37;
            border-radius: 10px;
            gridline-color: #242932;
            selection-background-color: #303641;
            selection-color: white;
        }

        QHeaderView::section {
            background: #1e2229;
            color: #aeb6c3;
            border: none;
            padding: 9px;
        }

        QLineEdit, QDateEdit, QComboBox {
            background: #15181d;
            border: 1px solid #303640;
            border-radius: 8px;
            padding: 8px 10px;
        }

        QPushButton {
            background: #2b313b;
            color: white;
            border: 1px solid #3a424e;
            border-radius: 8px;
            padding: 9px 14px;
        }

        QPushButton:hover {
            background: #363d48;
        }

        QPushButton:pressed {
            background: #242a32;
        }
        """
    )

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
