import sqlite3
import numpy as np
from pathlib import Path
from datetime import datetime

DB_PATH = Path("data/attendance.db")


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()

    conn.executescript("""
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
    """)

    conn.commit()
    conn.close()


def add_student(student_code, name):
    conn = get_connection()

    cursor = conn.execute(
        """
        INSERT INTO students (student_code, name)
        VALUES (?, ?)
        """,
        (student_code, name),
    )

    student_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return student_id


def add_embedding(student_id, embedding):
    embedding = np.asarray(embedding, dtype=np.float32)

    conn = get_connection()

    conn.execute(
        """
        INSERT INTO face_embeddings (student_id, embedding)
        VALUES (?, ?)
        """,
        (student_id, embedding.tobytes()),
    )

    conn.commit()
    conn.close()


def load_students():
    conn = get_connection()

    rows = conn.execute("""
        SELECT
            s.id,
            s.student_code,
            s.name,
            fe.embedding
        FROM students s
        JOIN face_embeddings fe
            ON fe.student_id = s.id
        ORDER BY s.id, fe.id
    """).fetchall()

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


def mark_attendance(student_id, camera_id="camera_0"):
    now = datetime.now()

    date = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO attendance
            (student_id, date, timestamp, camera_id)
        VALUES (?, ?, ?, ?)
        """,
        (student_id, date, timestamp, camera_id),
    )

    conn.commit()

    inserted = cursor.rowcount > 0

    conn.close()

    return inserted


def get_today_attendance():
    today = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()

    rows = conn.execute(
        """
        SELECT
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
        (today,),
    ).fetchall()

    conn.close()

    return rows
