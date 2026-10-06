import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "attendance.db"


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS face_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            embedding BLOB NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (student_id)
                REFERENCES students(id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_face_embeddings_student
        ON face_embeddings(student_id);

        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            camera_id TEXT NOT NULL DEFAULT 'camera_0',

            FOREIGN KEY (student_id)
                REFERENCES students(id)
                ON DELETE CASCADE,

            UNIQUE(student_id, date)
        );
        """
    )

    conn.commit()
    conn.close()


def add_student(student_code, name):
    conn = get_connection()

    try:
        cursor = conn.execute(
            """
            INSERT INTO students (student_code, name)
            VALUES (?, ?)
            """,
            (student_code.strip(), name.strip()),
        )
        student_id = cursor.lastrowid
        conn.commit()
        return student_id
    finally:
        conn.close()


def update_student(student_id, student_code, name):
    conn = get_connection()

    try:
        conn.execute(
            """
            UPDATE students
            SET student_code = ?, name = ?
            WHERE id = ?
            """,
            (student_code.strip(), name.strip(), student_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_student(student_id):
    conn = get_connection()

    try:
        conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
        conn.commit()
    finally:
        conn.close()


def delete_embeddings(student_id):
    conn = get_connection()

    try:
        conn.execute(
            "DELETE FROM face_embeddings WHERE student_id = ?",
            (student_id,),
        )
        conn.commit()
    finally:
        conn.close()


def add_embedding(student_id, embedding):
    embedding = np.asarray(embedding, dtype=np.float32)

    conn = get_connection()

    try:
        conn.execute(
            """
            INSERT INTO face_embeddings (student_id, embedding)
            VALUES (?, ?)
            """,
            (student_id, embedding.tobytes()),
        )
        conn.commit()
    finally:
        conn.close()


def load_students():
    conn = get_connection()

    try:
        rows = conn.execute(
            """
            SELECT
                s.id,
                s.student_code,
                s.name,
                fe.embedding
            FROM students s
            JOIN face_embeddings fe
                ON fe.student_id = s.id
            ORDER BY s.id, fe.id
            """
        ).fetchall()
    finally:
        conn.close()

    students = {}

    for student_id, student_code, name, blob in rows:
        embedding = np.frombuffer(blob, dtype=np.float32)

        if student_id not in students:
            students[student_id] = {
                "id": student_id,
                "student_code": student_code,
                "name": name,
                "embeddings": [],
            }

        students[student_id]["embeddings"].append(embedding)

    return list(students.values())


def list_students():
    conn = get_connection()

    try:
        rows = conn.execute(
            """
            SELECT
                s.id,
                s.student_code,
                s.name,
                COUNT(fe.id) AS embedding_count,
                s.created_at
            FROM students s
            LEFT JOIN face_embeddings fe
                ON fe.student_id = s.id
            GROUP BY s.id
            ORDER BY s.id
            """
        ).fetchall()
    finally:
        conn.close()

    return rows


def get_student(student_id):
    conn = get_connection()

    try:
        return conn.execute(
            """
            SELECT id, student_code, name, created_at
            FROM students
            WHERE id = ?
            """,
            (student_id,),
        ).fetchone()
    finally:
        conn.close()


def mark_attendance(student_id, camera_id="camera_0"):
    now = datetime.now()

    date = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()

    try:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO attendance
                (student_id, date, timestamp, camera_id)
            VALUES (?, ?, ?, ?)
            """,
            (student_id, date, timestamp, camera_id),
        )

        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_attendance(date=None):
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()

    try:
        return conn.execute(
            """
            SELECT
                s.id,
                s.student_code,
                s.name,
                a.timestamp,
                a.camera_id
            FROM attendance a
            JOIN students s
                ON s.id = a.student_id
            WHERE a.date = ?
            ORDER BY a.timestamp
            """,
            (date,),
        ).fetchall()
    finally:
        conn.close()


def get_attendance_stats(date=None):
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()

    try:
        total_students = conn.execute(
            "SELECT COUNT(*) FROM students"
        ).fetchone()[0]

        present = conn.execute(
            "SELECT COUNT(*) FROM attendance WHERE date = ?",
            (date,),
        ).fetchone()[0]
    finally:
        conn.close()

    return total_students, present, max(0, total_students - present)


def get_today_attendance():
    return [
        (student_code, name, timestamp, camera_id)
        for _, student_code, name, timestamp, camera_id in get_attendance()
    ]
