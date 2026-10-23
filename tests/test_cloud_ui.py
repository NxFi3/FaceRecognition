import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from filelock import FileLock
from PySide6.QtWidgets import QApplication, QMainWindow, QDialog
from src.cloud.ui import AccountDialog, CloudController, Jobs, run_application
from src.cloud.client import AccessDenied


class CloudUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
    def test_expired_access_stops_cameras_and_quits_once(self):
        window = QMainWindow()
        window.stop_camera = Mock()
        client = Mock()
        client.allowed.return_value = False
        client.require_lease.side_effect = AccessDenied("Expired")
        controller = CloudController(self.app, window, client, Path("unused.db"))
        controller.timer.stop()
        with patch.object(self.app, "quit") as quit_app, patch("src.cloud.ui.QMessageBox.warning"):
            controller.tick()
            controller.tick()
        window.stop_camera.assert_called_once()
        quit_app.assert_called_once()
        controller.jobs.close()
        window.deleteLater()
    def test_missing_service_config_fails_closed_and_releases_instance_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_window = Mock()
            init_db = Mock()
            with patch("src.cloud.ui.QMessageBox.critical"):
                result = run_application(self.app, create_window, init_db, Mock(), root / "attendance.db", root)
            self.assertEqual(result, 1)
            init_db.assert_not_called()
            create_window.assert_not_called()
            lock = FileLock(str(root / ".application.lock"))
            with lock.acquire(timeout=0):
                pass
    def test_account_consent_and_restore_disabled_after_startup(self):
        client = Mock()
        client.require_lease.return_value = {"school_name": "Test", "student_limit": 900,
            "subscription_expires_at": time.time() + 3600, "backups_enabled": True}
        client.state = {}
        client.remembered = True
        dialog = AccountDialog(client, "unused.db", can_restore=False)
        self.assertFalse(dialog.auto.isChecked())
        self.assertFalse(dialog.restore_button.isEnabled())
        dialog.auto.setChecked(True)
        self.assertTrue(client.state["automatic_backups"])
        client.save_state.assert_called_once()
        dialog.jobs.busy.add("restore")
        dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.Accepted)
        dialog.jobs.busy.clear()
        dialog.accept()
        dialog.deleteLater()
    def test_background_job_delivers_result_on_ui_thread(self):
        jobs = Jobs()
        seen = []
        main_thread = threading.get_ident()
        def complete(name, result, error):
            seen.append((name, result, error, threading.get_ident()))
            jobs.finished(name)
        jobs.completed.connect(complete)
        jobs.start("test", lambda: 123)
        deadline = time.monotonic() + 2
        while not seen and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.assertEqual(seen, [("test", 123, None, main_thread)])
        self.assertNotIn("test", jobs.busy)
        jobs.close()

    def test_network_outage_does_not_block_qt_events_or_duplicate_jobs(self):
        jobs = Jobs()
        entered, release = threading.Event(), threading.Event()
        results = []
        jobs.completed.connect(lambda *result: results.append(result))
        def outage():
            entered.set()
            release.wait(2)
            raise ConnectionError('offline')
        jobs.start('renew', outage)
        self.assertTrue(entered.wait(1))
        jobs.start('renew', outage)
        self.assertEqual(jobs.busy, {'renew'})
        from PySide6.QtCore import QTimer
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        self.app.processEvents()
        self.assertEqual(ticks, [True])
        self.assertEqual(results, [])
        release.set()
        deadline = time.monotonic() + 2
        while not results and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.assertEqual(len(results), 1)
        jobs.close()

    def test_sync_failure_uses_bounded_exponential_backoff_without_locking_gui(self):
        window = QMainWindow()
        client = Mock()
        client.allowed.return_value = True
        client.require_lease.return_value = {'school_name': 'Test', 'student_limit': 900,
            'subscription_expires_at': time.time() + 86400, 'backups_enabled': False}
        controller = CloudController(self.app, window, client, Path('unused.db'))
        controller.timer.stop()
        for _ in range(10):
            controller.done_job('renew', None, ConnectionError('offline'))
        self.assertEqual(controller.retry_seconds, 3600)
        self.assertFalse(controller.locked)
        controller.done_job('renew', {}, None)
        self.assertEqual(controller.retry_seconds, 60)
        controller.jobs.close()
        window.deleteLater()

    def test_offline_startup_skips_online_login(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Mock()
            client.allowed.return_value = True
            window = QMainWindow()
            window.camera_workers = {}
            window.stop_camera = Mock()
            with patch('src.cloud.ui.ClientConfig.load'), patch('src.cloud.ui.CloudClient', return_value=client), \
                 patch('src.cloud.ui.LoginDialog') as login, patch('src.cloud.ui.AccountDialog') as account, \
                 patch('src.cloud.ui.CloudController'), patch('src.cloud.ui.QMessageBox.question', return_value=16384), \
                 patch.object(self.app, 'exec', return_value=0):
                import sqlite3
                with sqlite3.connect(root / 'attendance.db') as connection:
                    connection.execute('CREATE TABLE app_settings(key TEXT, value TEXT)')
                    connection.execute("INSERT INTO app_settings VALUES('cloud_school_id','test-school')")
                account.return_value.exec.return_value = QDialog.Accepted
                account.return_value.restored = False
                self.assertEqual(run_application(self.app, lambda: window, Mock(), Mock(return_value=True), root / 'attendance.db', root), 0)
                login.assert_not_called()
            window.deleteLater()

    def test_automatic_backup_waits_for_successful_background_sync(self):
        window = QMainWindow()
        client = Mock()
        client.allowed.return_value = True
        client.state = {'automatic_backups': True}
        client.require_lease.return_value = {'school_name': 'Test', 'student_limit': 900,
            'subscription_expires_at': time.time() + 86400, 'backups_enabled': True}
        controller = CloudController(self.app, window, client, Path('unused.db'))
        controller.timer.stop()
        with patch.object(controller.jobs, 'start') as start:
            controller.tick()
            start.assert_not_called()
            controller.done_job('renew', {}, None)
            controller.tick()
            self.assertEqual(start.call_args.args[0], 'backup')
        controller.jobs.close()
        window.deleteLater()
