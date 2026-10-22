from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import pytest
from src import database as db


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'attendance.db')
    db.init_db()
    return db


def test_atomic_duplicate_attendance(database):
    student = database.add_student('1', 'Test')
    barrier = threading.Barrier(2)
    def mark(camera):
        barrier.wait()
        return database.mark_attendance(student, f'camera_{camera}')
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(mark, [1, 2]))
    assert sorted(results) == [False, True]
    conn = database.get_connection()
    try:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('SELECT count(*) FROM attendance').fetchone()[0] == 1
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    finally:
        conn.close()
    with pytest.raises(sqlite3.IntegrityError):
        database.mark_attendance(9999)


def test_event_day_index_and_order(database):
    database.add_event('TEST', timestamp='2026-10-22 10:00:00')
    newest = database.add_event('TEST', timestamp='2026-10-22 10:00:00')
    database.add_event('TEST', timestamp='2026-10-23 00:00:00')
    rows = database.get_recent_events(date='2026-10-22')
    assert len(rows) == 2 and rows[0][0] == newest
    conn = database.get_connection()
    try:
        plan = conn.execute("EXPLAIN QUERY PLAN SELECT id FROM events WHERE date(timestamp)=? ORDER BY timestamp DESC,id DESC LIMIT 100", ('2026-10-22',)).fetchall()
        assert any('idx_events_day_time' in row[3] for row in plan)
    finally:
        conn.close()
