import sqlite3

import pytest

from src import database


def _make_event_database(path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", path)
    connection = sqlite3.connect(path)
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


def test_sync_attendance_backfills_entries_and_is_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "attendance.db"
    _make_event_database(path, monkeypatch)
    connection = sqlite3.connect(path)
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

    assert database.sync_attendance_from_entry_events("2026-11-01", "2026-11-01") == 2

    connection = sqlite3.connect(path)
    rows = connection.execute(
        "SELECT student_id, date, timestamp, camera_id, status FROM attendance ORDER BY student_id"
    ).fetchall()
    connection.close()
    assert rows == [
        (1, "2026-11-01", "2026-11-01 08:15:00", "camera_0", "late"),
        (2, "2026-11-01", "2026-11-01 07:45:00", "camera_1", "present"),
    ]

    assert database.sync_attendance_from_entry_events("2026-11-01", "2026-11-01") == 0


def test_sync_does_not_overwrite_existing_manual_attendance(tmp_path, monkeypatch):
    path = tmp_path / "attendance.db"
    _make_event_database(path, monkeypatch)
    connection = sqlite3.connect(path)
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

    assert database.sync_attendance_from_entry_events("2026-11-01") == 0

    connection = sqlite3.connect(path)
    row = connection.execute(
        "SELECT status, notes, source FROM attendance WHERE student_id = 7"
    ).fetchone()
    connection.close()
    assert row == ("excused", "approved", "manual")


def test_sync_rejects_reversed_date_range():
    with pytest.raises(ValueError):
        database.sync_attendance_from_entry_events("2026-11-02", "2026-11-01")
