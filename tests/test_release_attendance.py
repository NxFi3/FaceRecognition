"""Synthetic release regressions: no camera, biometric matching or real pupils."""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from src import database
from src.engine.camera_capture import LatestFrameCapture
from src.engine.smart_events import AttendanceEventEngine, AttendancePresenceState
from src.engine.smart_tracking import PersonTrack


def track(student_id=None, identifier=1, witnessed=True):
    return PersonTrack(track_id=identifier, bbox=(0, 0, 20, 40), student_id=student_id,
                       metadata={'face_witnessed_for_crossing': witnessed})


def setting(key, default=None):
    return {'enable_late_events': '0', 'enable_early_events': '0'}.get(key, default)


@contextmanager
def fail_database_write(sql_fragment, message, occurrence=1):
    """Inject a real connection failure after BEGIN, before SQLite commit."""
    open_connection = database.get_connection
    class FailingConnection:
        def __init__(self, connection):
            self.connection = connection
            self.matches = 0
        def execute(self, sql, *args):
            if sql_fragment in sql:
                self.matches += 1
                if self.matches == occurrence:
                    raise sqlite3.OperationalError(message)
            return self.connection.execute(sql, *args)
        def commit(self):
            if sql_fragment == 'COMMIT':
                raise sqlite3.OperationalError(message)
            return self.connection.commit()
        def __getattr__(self, name):
            return getattr(self.connection, name)
    with patch.object(database, 'get_connection', side_effect=lambda: FailingConnection(open_connection())):
        yield


class AttendanceReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.dbpath = Path(self.directory.name)/'synthetic.sqlite3'
        self.pathpatch = patch.object(database, 'DB_PATH', self.dbpath)
        self.pathpatch.start(); self.addCleanup(self.pathpatch.stop)
        database.init_db()
        self.student = database.add_student('SYNTHETIC-RELEASE', 'Synthetic pupil')

    def engine(self, state=None, add_event=None, commit=None):
        return AttendanceEventEngine(add_event or database.add_event,
                                     commit or database.record_movement_transition,
                                     setting, state=state)

    def test_two_cameras_share_one_entry_transition_and_one_attendance_row(self):
        first_inside = threading.Event(); second_inside = threading.Event(); release = threading.Event()
        count_lock = threading.Lock(); calls = []; errors = []
        def commit(**kwargs):
            with count_lock:
                calls.append(kwargs); position = len(calls)
            if position == 1:
                first_inside.set()
                if not release.wait(2): raise RuntimeError('Test release did not arrive')
            else:
                second_inside.set()
            return database.record_movement_transition(**kwargs)
        state = AttendancePresenceState()
        engines = [self.engine(state=state, commit=commit), self.engine(state=state, commit=commit)]
        def crossing(index):
            try: engines[index].on_crossing(track(self.student, index+1), 'enter', index)
            except Exception as error: errors.append(error)
        first = threading.Thread(target=crossing, args=(0,)); second = threading.Thread(target=crossing, args=(1,))
        first.start(); self.assertTrue(first_inside.wait(2)); second.start()
        try:
            duplicate_entered = second_inside.wait(.1)
        finally:
            release.set(); first.join(2); second.join(2)
        self.assertFalse(first.is_alive()); self.assertFalse(second.is_alive()); self.assertEqual(errors, [])
        self.assertFalse(duplicate_entered, 'Second camera passed the shared presence transition while it was in progress')
        self.assertEqual(len(calls), 1)
        self.assertEqual([row[1] for row in database.get_recent_events()], ['PERSON_ENTERED'])
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0], 1)

    def test_failed_entry_event_write_rolls_back_attendance_and_remains_retryable(self):
        state = AttendancePresenceState()
        engine = self.engine(state=state)
        observation = track(self.student)
        with fail_database_write('INSERT INTO events', 'Synthetic disk full'):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'disk full'):
                engine.on_crossing(observation, 'enter', 0)
        self.assertNotIn(self.student, state.present)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
        self.assertEqual(engine.on_crossing(observation, 'enter', 0), ['PERSON_ENTERED'])
        self.assertIn(self.student, database.get_current_presence_ids())
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0], 1)

    def test_failed_exit_event_write_preserves_presence_until_successful_retry(self):
        state = AttendancePresenceState()
        engine = self.engine(state=state)
        observation = track(self.student)
        self.assertEqual(engine.on_crossing(observation, 'enter', 0), ['PERSON_ENTERED'])
        with fail_database_write('INSERT INTO events', 'Synthetic read-only event storage'):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'read-only'):
                engine.on_crossing(observation, 'exit', 1)
        self.assertIn(self.student, state.present)
        self.assertIn(self.student, database.get_current_presence_ids())
        self.assertEqual(engine.on_crossing(observation, 'exit', 1), ['PERSON_EXITED'])
        self.assertNotIn(self.student, state.present)
        self.assertEqual([row[1] for row in database.get_recent_events()], ['PERSON_EXITED', 'PERSON_ENTERED'])

    def test_failed_commit_rolls_back_attendance_and_movement(self):
        engine = self.engine()
        with fail_database_write('COMMIT', 'Synthetic commit failure'):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'commit failure'):
                engine.on_crossing(track(self.student), 'enter', 0)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)

    def test_killed_process_between_attendance_and_event_leaves_neither_row(self):
        marker = Path(self.directory.name)/'after-attendance.marker'
        script = '''
import sys, time
from pathlib import Path
from src import database
database.DB_PATH = Path(sys.argv[1])
original = database.get_connection
class PausingConnection:
    def __init__(self, connection): self.connection = connection
    def execute(self, sql, *args):
        result = self.connection.execute(sql, *args)
        if 'INSERT INTO attendance' in sql:
            Path(sys.argv[2]).write_text('uncommitted')
            time.sleep(30)
        return result
    def __getattr__(self, name): return getattr(self.connection, name)
database.get_connection = lambda: PausingConnection(original())
database.record_movement_transition(
    student_id=int(sys.argv[3]), direction='enter', camera_id='camera_0',
    track_id=1, confidence=.9)
'''
        process = subprocess.Popen(
            [sys.executable, '-c', script, str(self.dbpath), str(marker), str(self.student)],
            cwd=str(Path(__file__).resolve().parents[1]),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists(), 'Child did not reach the uncommitted attendance insert')
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)

    def test_second_companion_event_failure_rolls_back_first_event_and_attendance(self):
        with fail_database_write('INSERT INTO events', 'Synthetic second event failure', occurrence=2):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'second event failure'):
                database.record_movement_transition(
                    student_id=self.student, direction='enter', camera_id='camera_0',
                    track_id=1, confidence=.9, status='late',
                    auxiliary_event='LATE_ARRIVAL',
                    auxiliary_metadata={'school_start_time': '08:00'})
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
        result = database.record_movement_transition(
            student_id=self.student, direction='enter', camera_id='camera_0',
            track_id=1, confidence=.9, status='late',
            auxiliary_event='LATE_ARRIVAL', auxiliary_metadata={'school_start_time': '08:00'})
        self.assertTrue(result.applied)
        self.assertTrue(result.attendance_inserted)
        self.assertEqual([event[1] for event in result.events], ['PERSON_ENTERED', 'LATE_ARRIVAL'])

    def test_early_departure_companion_failure_preserves_durable_entry(self):
        entered = database.record_movement_transition(
            student_id=self.student, direction='enter', camera_id='camera_0',
            track_id=1, confidence=.9)
        self.assertTrue(entered.applied)
        with fail_database_write('INSERT INTO events', 'Synthetic second exit event failure', occurrence=2):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'second exit event failure'):
                database.record_movement_transition(
                    student_id=self.student, direction='exit', camera_id='camera_1',
                    track_id=2, confidence=.9, auxiliary_event='EARLY_DEPARTURE',
                    auxiliary_metadata={'school_end_time': '15:00'})
        self.assertIn(self.student, database.get_current_presence_ids())
        self.assertEqual([row[1] for row in database.get_recent_events()], ['PERSON_ENTERED'])

    def test_retry_after_commit_before_ram_update_does_not_duplicate_entry(self):
        state = AttendancePresenceState()
        calls = []
        def crash_after_commit(**kwargs):
            result = database.record_movement_transition(**kwargs)
            calls.append(result)
            if len(calls) == 1:
                raise RuntimeError('Synthetic process interruption after SQLite commit')
            return result
        engine = self.engine(state=state, commit=crash_after_commit)
        observation = track(self.student)
        with self.assertRaisesRegex(RuntimeError, 'process interruption'):
            engine.on_crossing(observation, 'enter', 0)
        self.assertNotIn(self.student, state.present)
        self.assertEqual(engine.on_crossing(observation, 'enter', 0), [])
        self.assertIn(self.student, state.present)
        self.assertEqual([row[1] for row in database.get_recent_events()], ['PERSON_ENTERED'])
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0], 1)

    def test_retry_after_exit_commit_before_ram_update_does_not_duplicate_exit(self):
        state = AttendancePresenceState()
        observation = track(self.student)
        self.assertEqual(self.engine(state=state).on_crossing(observation, 'enter', 0), ['PERSON_ENTERED'])
        calls = []
        def crash_after_commit(**kwargs):
            result = database.record_movement_transition(**kwargs)
            calls.append(result)
            if len(calls) == 1:
                raise RuntimeError('Synthetic exit interruption after SQLite commit')
            return result
        engine = self.engine(state=state, commit=crash_after_commit)
        with self.assertRaisesRegex(RuntimeError, 'exit interruption'):
            engine.on_crossing(observation, 'exit', 1)
        self.assertIn(self.student, state.present)
        self.assertEqual(engine.on_crossing(observation, 'exit', 1), [])
        self.assertNotIn(self.student, state.present)
        self.assertEqual([row[1] for row in database.get_recent_events()], ['PERSON_EXITED', 'PERSON_ENTERED'])

    def test_independent_worker_states_are_deduplicated_by_sqlite(self):
        engines = [self.engine(), self.engine()]
        barrier = threading.Barrier(2)
        results = []; errors = []
        def crossing(index):
            try:
                barrier.wait(timeout=2)
                results.append(engines[index].on_crossing(track(self.student, index+1), 'enter', index))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=crossing, args=(index,)) for index in (0, 1)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [[], ['PERSON_ENTERED']])
        self.assertEqual([row[1] for row in database.get_recent_events()], ['PERSON_ENTERED'])

    def test_genuine_exit_and_reentry_keep_one_daily_attendance_row(self):
        engine = self.engine()
        observation = track(self.student)
        self.assertEqual(engine.on_crossing(observation, 'enter', 0), ['PERSON_ENTERED'])
        self.assertEqual(engine.on_crossing(observation, 'exit', 1), ['PERSON_EXITED'])
        self.assertEqual(engine.on_crossing(observation, 'enter', 0), ['PERSON_ENTERED'])
        self.assertEqual([row[1] for row in database.get_recent_events()],
                         ['PERSON_ENTERED', 'PERSON_EXITED', 'PERSON_ENTERED'])
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0], 1)

    def test_worker_publishes_signals_only_after_atomic_commit(self):
        from src.engine.smart_camera_worker import SmartCameraWorker
        worker = SmartCameraWorker()
        notifications = []
        def observed(kind, payload):
            with closing(database.get_connection()) as conn:
                counts = (conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0],
                          conn.execute('SELECT COUNT(*) FROM events').fetchone()[0])
            notifications.append((kind, payload, counts))
        worker.attendance_added.connect(lambda payload: observed('attendance', payload))
        worker.event_added.connect(lambda payload: observed('event', payload))
        kwargs = dict(student_id=self.student, direction='enter', camera_id='camera_0',
                      track_id=1, confidence=.9)
        with fail_database_write('INSERT INTO events', 'Synthetic event write error'):
            with self.assertRaises(sqlite3.OperationalError):
                worker._commit_transition_event(**kwargs)
        self.assertEqual(notifications, [])
        result = worker._commit_transition_event(**kwargs)
        self.assertTrue(result.applied)
        self.assertEqual([item[0] for item in notifications], ['attendance', 'event'])
        self.assertTrue(all(item[2] == (1, 1) for item in notifications))

    def test_unknown_or_unwitnessed_identity_never_marks_attendance(self):
        engine = self.engine()
        for observation in [track(), track(self.student, witnessed=False)]:
            self.assertEqual(engine.on_crossing(observation, 'enter', 0), ['UNKNOWN_PERSON'])
        self.assertEqual(database.get_current_presence_ids(), set())
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0], 0)
        self.assertEqual([row[1] for row in database.get_recent_events()], ['UNKNOWN_PERSON', 'UNKNOWN_PERSON'])

    def test_reports_keep_grade_and_class_distinct_and_manual_correction_persists(self):
        year = database.get_active_academic_year()[0]
        major = database.add_major('Synthetic major')
        classroom = database.add_class('Class A', 'Grade 7', major, year)
        database.assign_student_class(self.student, classroom)
        today = (datetime.strptime(database.get_active_academic_year()[2], '%Y-%m-%d') + timedelta(days=30)).strftime('%Y-%m-%d')
        # School calendar may exclude the host's weekday; explicitly include this synthetic day.
        database.set_school_day_override(year, today, True, 'Synthetic test day')
        database.set_attendance_status(self.student, today, 'excused', 'Staff correction')
        rows = database.get_report_rows(today, today)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], 'Grade 7')
        self.assertEqual(rows[0][4], 'Synthetic major')
        self.assertEqual(rows[0][5], 'Class A')
        self.assertEqual(rows[0][9], 1)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT status,source,notes FROM attendance').fetchone(),
                             ('excused', 'manual', 'Staff correction'))

    def test_class_summary_excludes_closed_day_records_from_counts_and_percentage(self):
        year = database.get_active_academic_year()[0]
        classroom = database.add_class('Class B', 'Grade 8', None, year)
        database.assign_student_class(self.student, classroom)
        day = datetime.strptime(database.get_active_academic_year()[2], '%Y-%m-%d') + timedelta(days=30)
        today = day.strftime('%Y-%m-%d')
        closed = (day - timedelta(days=1)).strftime('%Y-%m-%d')
        database.set_school_day_override(year, today, True, 'Synthetic open day')
        database.set_school_day_override(year, closed, False, 'Synthetic closed day')
        database.set_attendance_status(self.student, today, 'present')
        database.set_attendance_status(self.student, closed, 'present')
        summary = database.get_class_report_summary(closed, today, class_id=classroom)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0][4], 1)
        self.assertEqual(summary[0][8], 100.0)
        # Historical data is retained; only its report eligibility changes.
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0], 2)


class LegacyDatabaseReleaseTests(unittest.TestCase):
    def test_legacy_sqlite_columns_are_migrated_without_losing_history_or_templates(self):
        # A synthetic pre-status/pre-capacity school database, not customer data.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'legacy.sqlite3'
            embedding = np.zeros(128, dtype=np.float32).tobytes()
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript("""
                    CREATE TABLE students (id INTEGER PRIMARY KEY AUTOINCREMENT,
                        student_code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE attendance (id INTEGER PRIMARY KEY AUTOINCREMENT,
                        student_id INTEGER NOT NULL, date TEXT NOT NULL,
                        timestamp TEXT NOT NULL, camera_id TEXT NOT NULL,
                        UNIQUE(student_id,date));
                    CREATE TABLE face_embeddings (id INTEGER PRIMARY KEY AUTOINCREMENT,
                        student_id INTEGER NOT NULL, embedding BLOB NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                    INSERT INTO students(id,student_code,name) VALUES(71,'SYNTHETIC-LEGACY','Legacy synthetic pupil');
                    INSERT INTO attendance(student_id,date,timestamp,camera_id)
                        VALUES(71,'2026-10-26','2026-10-26 08:01:02','camera_0');
                """)
                conn.execute('INSERT INTO face_embeddings(student_id,embedding) VALUES(?,?)',(71,embedding))
                conn.commit()
            with patch.object(database, 'DB_PATH', path):
                database.init_db(); database.init_db()
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT student_code,capacity_archived FROM students WHERE id=71').fetchone(),
                                 ('SYNTHETIC-LEGACY',0))
                self.assertEqual(conn.execute('SELECT date,timestamp,status,source FROM attendance').fetchone(),
                                 ('2026-10-26','2026-10-26 08:01:02','present','camera'))
                self.assertEqual(conn.execute('SELECT embedding FROM face_embeddings').fetchone()[0], embedding)


class ExportReleaseTests(unittest.TestCase):
    def test_disk_full_preserves_existing_csv_and_removes_partial_file(self):
        from src import export_attendance
        class BrokenWriter:
            def writerow(self, _values): raise OSError('Synthetic disk full')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = root/'attendance_2026-10-26.csv'
            previous.write_text('previous complete export', encoding='utf8')
            with patch.object(export_attendance, 'OUTPUT_DIR', root), patch.object(export_attendance, 'init_db'), \
                 patch.object(export_attendance, 'get_today_attendance', return_value=[('SYNTHETIC','Pupil','2026-10-26 08:00:00','camera_0')]), \
                 patch.object(export_attendance.csv, 'writer', return_value=BrokenWriter()):
                with self.assertRaises(OSError): export_attendance.main()
            self.assertEqual(previous.read_text(encoding='utf8'), 'previous complete export')
            self.assertEqual([p.name for p in root.iterdir()], [previous.name])


class SyntheticCapture:
    def __init__(self, frame=None, error=None):
        self.frame, self.error = frame, error
        self.released = threading.Event()
    def get(self, _property): return 2
    def read(self):
        if self.error: raise self.error
        if self.released.wait(.005): return False, None
        return True, self.frame
    def release(self): self.released.set()


class CaptureReleaseTests(unittest.TestCase):
    def test_native_read_exception_reaches_waiter_and_releases_capture(self):
        source = SyntheticCapture(error=OSError('Synthetic camera disconnect'))
        capture = LatestFrameCapture(0, 30, lambda *_args: source)
        capture.start()
        try:
            self.assertTrue(source.released.wait(1))
            with self.assertRaisesRegex(RuntimeError, 'Synthetic camera disconnect'):
                capture.wait_for_frame(timeout=.03)
        finally: capture.stop()

    def test_restart_does_not_return_previous_session_frame_or_error(self):
        first = SyntheticCapture(frame=np.ones((2, 2, 3), dtype=np.uint8))
        second = SyntheticCapture(error=OSError('Synthetic second session failure'))
        sources = iter([first, second]); capture = LatestFrameCapture(0, 30, lambda *_args: next(sources))
        capture.start(); self.assertIsNotNone(capture.wait_for_frame(timeout=1)[1]); capture.stop()
        capture.start()
        try:
            self.assertTrue(second.released.wait(1))
            with self.assertRaisesRegex(RuntimeError, 'second session'):
                capture.wait_for_frame(timeout=.03)
        finally: capture.stop()

    def test_burst_capture_exposes_only_latest_frame_and_does_not_repeat_sequence(self):
        class BurstCapture(SyntheticCapture):
            def __init__(self):
                super().__init__(); self.count = 0; self.burst_done = threading.Event()
            def read(self):
                self.count += 1
                if self.count <= 3:
                    return True, np.full((2, 2, 3), self.count, dtype=np.uint8)
                self.burst_done.set(); self.released.wait(1)
                return False, None
        source = BurstCapture(); capture = LatestFrameCapture(0, 30, lambda *_args: source)
        capture.start()
        try:
            self.assertTrue(source.burst_done.wait(1))
            sequence, frame = capture.wait_for_frame(timeout=.03)
            self.assertEqual(sequence, 3)
            self.assertTrue(np.all(frame == 3))
            self.assertEqual(capture.wait_for_frame(sequence, timeout=.02), (None, None))
        finally:
            source.release(); capture.stop()

    def test_capture_configuration_failure_releases_opened_handle(self):
        source = SyntheticCapture()
        source.get = lambda _property: (_ for _ in ()).throw(OSError('Synthetic metadata failure'))
        capture = LatestFrameCapture(0, 30, lambda *_args: source)
        with self.assertRaisesRegex(OSError, 'metadata failure'): capture.start()
        self.assertTrue(source.released.is_set())

    def test_missing_model_dependency_fails_explicitly_without_cwd_fallback(self):
        from src.engine import model_assets
        with patch.object(model_assets, 'find_spec', return_value=None), patch.object(model_assets, '_legacy_directory', None), \
             patch.object(model_assets.sys, 'frozen', False, create=True):
            with self.assertRaisesRegex(RuntimeError, 'face_recognition_models'):
                model_assets.face_recognition_model_location()


if __name__ == '__main__': unittest.main()
