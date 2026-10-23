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
