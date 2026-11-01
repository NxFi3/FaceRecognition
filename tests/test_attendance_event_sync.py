import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import database


class AttendanceEventSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "attendance.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)

        connection = sqlite3.connect(self.db_path)
        connection.executescript(
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                student_id INTEGER,
                camera_id TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            CREATE TABLE attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                status TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'camera',
                updated_at TEXT NOT NULL,
                UNIQUE(student_id, date)
            );
            """
        )
        connection.commit()
        connection.close()

    def test_sync_backfills_entries_and_is_idempotent(self):
        connection = sqlite3.connect(self.db_path)
        connection.executemany(
            """
            INSERT INTO events (event_type, student_id, camera_id, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("PERSON_ENTERED", 1, "camera_0", "2026-11-01 08:15:00"),
                ("LATE_ARRIVAL", 1, "camera_0", "2026-11-01 08:15:00"),
                ("PERSON_EXITED", 1, "camera_0", "2026-11-01 09:00:00"),
                ("PERSON_ENTERED", 2, "camera_1", "2026-11-01 07:45:00"),
                ("PERSON_ENTERED", 3, "camera_0", "2026-10-31 08:00:00"),
            ],
        )
        connection.commit()
        connection.close()

        self.assertEqual(
            database.sync_attendance_from_entry_events("2026-11-01", "2026-11-01"),
            2,
        )

        connection = sqlite3.connect(self.db_path)
        rows = connection.execute(
            "SELECT student_id, date, timestamp, camera_id, status "
            "FROM attendance ORDER BY student_id"
        ).fetchall()
        connection.close()
        self.assertEqual(
            rows,
            [
                (1, "2026-11-01", "2026-11-01 08:15:00", "camera_0", "late"),
                (2, "2026-11-01", "2026-11-01 07:45:00", "camera_1", "present"),
            ],
        )
        self.assertEqual(
            database.sync_attendance_from_entry_events("2026-11-01", "2026-11-01"),
            0,
        )

    def test_sync_does_not_overwrite_manual_attendance(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            """
            INSERT INTO events (event_type, student_id, camera_id, timestamp)
            VALUES ('PERSON_ENTERED', 7, 'camera_0', '2026-11-01 08:15:00')
            """
        )
        connection.execute(
            """
            INSERT INTO attendance
                (student_id, date, timestamp, camera_id, status, notes, source, updated_at)
            VALUES (7, '2026-11-01', '2026-11-01 08:00:00', 'manual',
                    'excused', 'approved', 'manual', '2026-11-01 08:00:00')
            """
        )
        connection.commit()
        connection.close()

        self.assertEqual(database.sync_attendance_from_entry_events("2026-11-01"), 0)

        connection = sqlite3.connect(self.db_path)
        row = connection.execute(
            "SELECT status, notes, source FROM attendance WHERE student_id = 7"
        ).fetchone()
        connection.close()
        self.assertEqual(row, ("excused", "approved", "manual"))

    def test_sync_rejects_reversed_date_range(self):
        with self.assertRaises(ValueError):
            database.sync_attendance_from_entry_events("2026-11-02", "2026-11-01")


if __name__ == "__main__":
    unittest.main()
