import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np


if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

DB_PATH = BASE_DIR / "data" / "attendance.db"


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn, table, column, definition):
    columns = {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init_db():
    conn = get_connection()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS academic_years (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            start_date TEXT,
            end_date TEXT,
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS majors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS school_classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            grade TEXT NOT NULL DEFAULT '',
            major_id INTEGER,
            academic_year_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (major_id)
                REFERENCES majors(id)
                ON DELETE SET NULL,

            FOREIGN KEY (academic_year_id)
                REFERENCES academic_years(id)
                ON DELETE CASCADE,

            UNIQUE(name, academic_year_id)
        );

        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS student_enrollments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            class_id INTEGER NOT NULL,
            academic_year_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (student_id)
                REFERENCES students(id)
                ON DELETE CASCADE,

            FOREIGN KEY (class_id)
                REFERENCES school_classes(id)
                ON DELETE CASCADE,

            FOREIGN KEY (academic_year_id)
                REFERENCES academic_years(id)
                ON DELETE CASCADE,

            UNIQUE(student_id, academic_year_id)
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

    _ensure_column(
        conn,
        "attendance",
        "status",
        "TEXT NOT NULL DEFAULT 'present'",
    )
    _ensure_column(
        conn,
        "attendance",
        "notes",
        "TEXT NOT NULL DEFAULT ''",
    )
    _ensure_column(
        conn,
        "attendance",
        "source",
        "TEXT NOT NULL DEFAULT 'camera'",
    )
    _ensure_column(
        conn,
        "attendance",
        "updated_at",
        "TEXT",
    )

    conn.execute(
        "UPDATE attendance SET status = 'present' "
        "WHERE status IS NULL OR status = ''"
    )

    _ensure_column(
        conn,
        "academic_years",
        "start_date",
        "TEXT",
    )
    _ensure_column(
        conn,
        "academic_years",
        "end_date",
        "TEXT",
    )

    year_count = conn.execute(
        "SELECT COUNT(*) FROM academic_years"
    ).fetchone()[0]

    if year_count == 0:
        current_year = datetime.now().year
        if datetime.now().month < 9:
            current_year -= 1

        start_date = f"{current_year:04d}-09-01"
        end_date = f"{current_year + 1:04d}-06-30"
        year_name = f"{current_year}-{current_year + 1}"

        conn.execute(
            """
            INSERT INTO academic_years
                (name, start_date, end_date, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (year_name, start_date, end_date),
        )
    else:
        legacy_rows = conn.execute(
            """
            SELECT id, name
            FROM academic_years
            WHERE start_date IS NULL OR end_date IS NULL
            """
        ).fetchall()

        for year_id, name in legacy_rows:
            parts = str(name).strip().replace("/", "-").split("-")
            if (
                len(parts) == 2
                and parts[0].isdigit()
                and parts[1].isdigit()
                and len(parts[0]) == 4
                and len(parts[1]) == 4
            ):
                start_year = int(parts[0])
                end_year = int(parts[1])
                conn.execute(
                    """
                    UPDATE academic_years
                    SET start_date = ?, end_date = ?
                    WHERE id = ?
                    """,
                    (
                        f"{start_year:04d}-09-01",
                        f"{end_year:04d}-06-30",
                        year_id,
                    ),
                )

        if conn.execute(
            "SELECT COUNT(*) FROM academic_years WHERE is_active = 1"
        ).fetchone()[0] == 0:
            first_year = conn.execute(
                "SELECT id FROM academic_years ORDER BY id LIMIT 1"
            ).fetchone()
            if first_year:
                conn.execute(
                    "UPDATE academic_years SET is_active = 1 WHERE id = ?",
                    (first_year[0],),
                )

    conn.commit()
    conn.close()


def get_setting(key, default=None):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row is not None else default


def set_setting(key, value):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()


def list_academic_years():
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT id, name, start_date, end_date, is_active
            FROM academic_years
            ORDER BY is_active DESC, start_date DESC, id DESC
            """
        ).fetchall()
    finally:
        conn.close()


def get_active_academic_year():
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT id, name, start_date, end_date, is_active
            FROM academic_years
            WHERE is_active = 1
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()


def add_academic_year(start_date, end_date, active=False):
    start_date = start_date.strip()
    end_date = end_date.strip()

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("تاریخ سال تحصیلی نامعتبر است.") from exc

    if end <= start:
        raise ValueError("تاریخ پایان باید بعد از تاریخ شروع باشد.")

    conn = get_connection()
    try:
        overlap = conn.execute(
            """
            SELECT id, name
            FROM academic_years
            WHERE start_date IS NOT NULL
              AND end_date IS NOT NULL
              AND start_date < ?
              AND end_date > ?
            """,
            (end_date, start_date),
        ).fetchone()

        if overlap:
            raise ValueError(
                f"بازه انتخاب‌شده با سال تحصیلی «{overlap[1]}» هم‌پوشانی دارد."
            )

        name = f"{start.year:04d}-{end.year:04d}"

        if active:
            conn.execute("UPDATE academic_years SET is_active = 0")

        cursor = conn.execute(
            """
            INSERT INTO academic_years
                (name, start_date, end_date, is_active)
            VALUES (?, ?, ?, ?)
            """,
            (name, start_date, end_date, 1 if active else 0),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def set_active_academic_year(year_id):
    conn = get_connection()
    try:
        exists = conn.execute(
            "SELECT 1 FROM academic_years WHERE id = ?",
            (year_id,),
        ).fetchone()

        if not exists:
            raise ValueError("سال تحصیلی انتخاب‌شده پیدا نشد.")

        conn.execute("UPDATE academic_years SET is_active = 0")
        conn.execute(
            "UPDATE academic_years SET is_active = 1 WHERE id = ?",
            (year_id,),
        )
        conn.commit()
    finally:
        conn.close()


def list_majors():
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT id, name, created_at
            FROM majors
            ORDER BY name
            """
        ).fetchall()
    finally:
        conn.close()


def add_major(name):
    name = name.strip()
    if not name:
        raise ValueError("نام رشته الزامی است.")
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO majors (name) VALUES (?)",
            (name,),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_major(major_id, name):
    name = name.strip()
    if not name:
        raise ValueError("نام رشته الزامی است.")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE majors SET name = ? WHERE id = ?",
            (name, major_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_major(major_id):
    conn = get_connection()
    try:
        used = conn.execute(
            "SELECT COUNT(*) FROM school_classes WHERE major_id = ?",
            (major_id,),
        ).fetchone()[0]
        if used:
            raise ValueError("این رشته به یک یا چند کلاس متصل است.")
        conn.execute("DELETE FROM majors WHERE id = ?", (major_id,))
        conn.commit()
    finally:
        conn.close()


def list_classes(academic_year_id=None):
    conn = get_connection()
    try:
        if academic_year_id is None:
            return conn.execute(
                """
                SELECT
                    c.id,
                    c.name,
                    c.grade,
                    COALESCE(m.name, ''),
                    y.id,
                    y.name,
                    COUNT(se.id) AS student_count
                FROM school_classes c
                LEFT JOIN majors m ON m.id = c.major_id
                JOIN academic_years y ON y.id = c.academic_year_id
                LEFT JOIN student_enrollments se ON se.class_id = c.id
                GROUP BY c.id
                ORDER BY y.name DESC, c.grade, c.name
                """
            ).fetchall()

        return conn.execute(
            """
            SELECT
                c.id,
                c.name,
                c.grade,
                COALESCE(m.name, ''),
                y.id,
                y.name,
                COUNT(se.id) AS student_count
            FROM school_classes c
            LEFT JOIN majors m ON m.id = c.major_id
            JOIN academic_years y ON y.id = c.academic_year_id
            LEFT JOIN student_enrollments se ON se.class_id = c.id
            WHERE c.academic_year_id = ?
            GROUP BY c.id
            ORDER BY c.grade, c.name
            """,
            (academic_year_id,),
        ).fetchall()
    finally:
        conn.close()


def add_class(name, grade, major_id, academic_year_id):
    name = name.strip()
    grade = grade.strip()

    if not name:
        raise ValueError("نام کلاس الزامی است.")
    if not grade:
        raise ValueError("پایه الزامی است.")
    if not academic_year_id:
        raise ValueError("سال تحصیلی انتخاب نشده است.")

    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO school_classes
                (name, grade, major_id, academic_year_id)
            VALUES (?, ?, ?, ?)
            """,
            (name, grade, major_id or None, academic_year_id),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_class(class_id, name, grade, major_id, academic_year_id):
    name = name.strip()
    grade = grade.strip()

    if not name:
        raise ValueError("نام کلاس الزامی است.")
    if not grade:
        raise ValueError("پایه الزامی است.")
    if not academic_year_id:
        raise ValueError("سال تحصیلی انتخاب نشده است.")

    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE school_classes
            SET name = ?, grade = ?, major_id = ?, academic_year_id = ?
            WHERE id = ?
            """,
            (
                name.strip(),
                grade.strip(),
                major_id or None,
                academic_year_id,
                class_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def delete_class(class_id):
    conn = get_connection()
    try:
        used = conn.execute(
            "SELECT COUNT(*) FROM student_enrollments WHERE class_id = ?",
            (class_id,),
        ).fetchone()[0]
        if used:
            raise ValueError("این کلاس دانش‌آموز دارد و قابل حذف نیست.")
        conn.execute("DELETE FROM school_classes WHERE id = ?", (class_id,))
        conn.commit()
    finally:
        conn.close()


def get_student_class(student_id, academic_year_id=None):
    if academic_year_id is None:
        active = get_active_academic_year()
        academic_year_id = active[0] if active else None
    if academic_year_id is None:
        return None

    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT
                c.id,
                c.name,
                c.grade,
                COALESCE(m.name, ''),
                y.id,
                y.name
            FROM student_enrollments se
            JOIN school_classes c ON c.id = se.class_id
            LEFT JOIN majors m ON m.id = c.major_id
            JOIN academic_years y ON y.id = se.academic_year_id
            WHERE se.student_id = ? AND se.academic_year_id = ?
            """,
            (student_id, academic_year_id),
        ).fetchone()
    finally:
        conn.close()


def assign_student_class(student_id, class_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT academic_year_id FROM school_classes WHERE id = ?",
            (class_id,),
        ).fetchone()
        if row is None:
            raise ValueError("کلاس انتخاب‌شده پیدا نشد.")

        conn.execute(
            """
            INSERT INTO student_enrollments
                (student_id, class_id, academic_year_id)
            VALUES (?, ?, ?)
            ON CONFLICT(student_id, academic_year_id)
            DO UPDATE SET
                class_id = excluded.class_id
            """,
            (student_id, class_id, row[0]),
        )
        conn.commit()
    finally:
        conn.close()


def list_students(academic_year_id=None):
    if academic_year_id is None:
        active = get_active_academic_year()
        academic_year_id = active[0] if active else None

    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT
                s.id,
                s.student_code,
                s.name,
                COUNT(DISTINCT fe.id) AS embedding_count,
                s.created_at,
                COALESCE(c.name, ''),
                COALESCE(m.name, ''),
                COALESCE(c.grade, '')
            FROM students s
            LEFT JOIN face_embeddings fe
                ON fe.student_id = s.id
            LEFT JOIN student_enrollments se
                ON se.student_id = s.id
                AND se.academic_year_id = ?
            LEFT JOIN school_classes c
                ON c.id = se.class_id
            LEFT JOIN majors m
                ON m.id = c.major_id
            GROUP BY s.id
            ORDER BY s.id
            """,
            (academic_year_id,),
        ).fetchall()
    finally:
        conn.close()


def find_student_by_code(student_code):
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT id, student_code, name, created_at
            FROM students
            WHERE student_code = ?
            """,
            (student_code.strip(),),
        ).fetchone()
    finally:
        conn.close()


def find_major_by_name(name):
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT id, name FROM majors WHERE name = ?",
            (name.strip(),),
        ).fetchone()
    finally:
        conn.close()


def find_class_by_name(name, academic_year_id):
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT id, name
            FROM school_classes
            WHERE name = ? AND academic_year_id = ?
            """,
            (name.strip(), academic_year_id),
        ).fetchone()
    finally:
        conn.close()


def add_student(student_code, name, class_id=None):
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

        if class_id:
            row = conn.execute(
                "SELECT academic_year_id FROM school_classes WHERE id = ?",
                (class_id,),
            ).fetchone()
            if row is None:
                raise ValueError("کلاس انتخاب‌شده پیدا نشد.")
            conn.execute(
                """
                INSERT INTO student_enrollments
                    (student_id, class_id, academic_year_id)
                VALUES (?, ?, ?)
                """,
                (student_id, class_id, row[0]),
            )

        conn.commit()
        return student_id
    finally:
        conn.close()


def update_student(student_id, student_code, name, class_id=None):
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

        if class_id is not None:
            row = conn.execute(
                "SELECT academic_year_id FROM school_classes WHERE id = ?",
                (class_id,),
            ).fetchone()
            if row is None:
                raise ValueError("کلاس انتخاب‌شده پیدا نشد.")
            conn.execute(
                """
                INSERT INTO student_enrollments
                    (student_id, class_id, academic_year_id)
                VALUES (?, ?, ?)
                ON CONFLICT(student_id, academic_year_id)
                DO UPDATE SET class_id = excluded.class_id
                """,
                (student_id, class_id, row[0]),
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


def mark_attendance(student_id, camera_id="camera_0", status="present", notes=""):
    now = datetime.now()
    date = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM attendance WHERE student_id = ? AND date = ?",
            (student_id, date),
        ).fetchone()

        if existing is not None:
            return False

        conn.execute(
            """
            INSERT INTO attendance
                (student_id, date, timestamp, camera_id, status,
                 notes, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'camera', ?)
            """,
            (
                student_id,
                date,
                timestamp,
                camera_id,
                status,
                notes,
                timestamp,
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def set_attendance_status(
    student_id,
    date,
    status,
    notes="",
    timestamp=None,
    source="manual",
):
    status = status.strip().lower()
    if status not in {"present", "late", "excused", "absent"}:
        raise ValueError("وضعیت حضور نامعتبر است.")

    if timestamp is None:
        timestamp = f"{date} {datetime.now().strftime('%H:%M:%S')}"

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO attendance
                (student_id, date, timestamp, camera_id, status,
                 notes, source, updated_at)
            VALUES (?, ?, ?, 'manual', ?, ?, ?, ?)
            ON CONFLICT(student_id, date) DO UPDATE SET
                timestamp = excluded.timestamp,
                status = excluded.status,
                notes = excluded.notes,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (
                student_id,
                date,
                timestamp,
                status,
                notes,
                source,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_attendance(date=None, class_id=None, major_id=None):
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    active = get_active_academic_year()
    year_id = active[0] if active else None

    conn = get_connection()
    try:
        query = """
            SELECT
                s.id,
                s.student_code,
                s.name,
                a.timestamp,
                a.camera_id,
                COALESCE(a.status, 'absent'),
                COALESCE(a.notes, ''),
                COALESCE(c.name, ''),
                COALESCE(m.name, '')
            FROM students s
            LEFT JOIN student_enrollments se
                ON se.student_id = s.id
                AND se.academic_year_id = ?
            LEFT JOIN school_classes c
                ON c.id = se.class_id
            LEFT JOIN majors m
                ON m.id = c.major_id
            LEFT JOIN attendance a
                ON a.student_id = s.id
                AND a.date = ?
            WHERE 1 = 1
        """
        params = [year_id, date]

        if class_id:
            query += " AND c.id = ?"
            params.append(class_id)

        if major_id:
            query += " AND m.id = ?"
            params.append(major_id)

        query += " ORDER BY s.name"

        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def get_attendance_stats(
    date=None,
    class_id=None,
    major_id=None,
):
    rows = get_attendance(date, class_id, major_id)

    total = len(rows)
    present = sum(row[5] == "present" for row in rows)
    late = sum(row[5] == "late" for row in rows)
    excused = sum(row[5] == "excused" for row in rows)
    absent = total - present - late - excused

    return total, present, absent, late, excused


def get_recent_attendance(limit=12, date=None):
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT
                s.student_code,
                s.name,
                a.timestamp,
                COALESCE(c.name, ''),
                a.status
            FROM attendance a
            JOIN students s ON s.id = a.student_id
            LEFT JOIN student_enrollments se
                ON se.student_id = s.id
                AND se.academic_year_id = (
                    SELECT id FROM academic_years
                    WHERE is_active = 1
                    ORDER BY id DESC LIMIT 1
                )
            LEFT JOIN school_classes c ON c.id = se.class_id
            WHERE a.date = ?
              AND a.status IN ('present', 'late', 'excused')
            ORDER BY a.timestamp DESC
            LIMIT ?
            """,
            (date, limit),
        ).fetchall()
    finally:
        conn.close()


def get_report_rows(
    start_date,
    end_date,
    class_id=None,
    major_id=None,
):
    rows = get_attendance(
        date=start_date,
        class_id=class_id,
        major_id=major_id,
    )

    del rows

    active = get_active_academic_year()
    year_id = active[0] if active else None

    conn = get_connection()
    try:
        query = """
            SELECT
                s.id,
                s.student_code,
                s.name,
                c.name,
                COALESCE(m.name, ''),
                c.grade
            FROM students s
            JOIN student_enrollments se
                ON se.student_id = s.id
                AND se.academic_year_id = ?
            JOIN school_classes c ON c.id = se.class_id
            LEFT JOIN majors m ON m.id = c.major_id
            WHERE 1 = 1
        """
        params = [year_id]

        if class_id:
            query += " AND c.id = ?"
            params.append(class_id)

        if major_id:
            query += " AND m.id = ?"
            params.append(major_id)

        query += " ORDER BY c.grade, c.name, s.name"

        students = conn.execute(query, params).fetchall()

        attendance_rows = conn.execute(
            """
            SELECT student_id, date, status
            FROM attendance
            WHERE date BETWEEN ? AND ?
            """,
            (start_date, end_date),
        ).fetchall()
    finally:
        conn.close()

    by_student = {}
    for student_id, date, status in attendance_rows:
        by_student.setdefault(student_id, {})[date] = status

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    day_count = max(1, (end - start).days + 1)

    result = []

    for student_id, code, name, class_name, major_name, grade in students:
        statuses = by_student.get(student_id, {})

        present = 0
        late = 0
        excused = 0

        current = start
        while current <= end:
            status = statuses.get(current.strftime("%Y-%m-%d"))
            if status == "present":
                present += 1
            elif status == "late":
                late += 1
            elif status == "excused":
                excused += 1
            current = current.fromordinal(current.toordinal() + 1)

        absent = max(0, day_count - present - late - excused)
        attendance_percent = (
            ((present + late) / day_count) * 100
            if day_count
            else 0.0
        )

        result.append(
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
                round(attendance_percent, 1),
            )
        )

    return result


def get_today_attendance():
    return [
        (row[1], row[2], row[3], row[4])
        for row in get_attendance()
        if row[5] in {"present", "late", "excused"}
    ]
