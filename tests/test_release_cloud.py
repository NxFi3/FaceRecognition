"""Release regressions for local recovery authorization and key publication."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from PySide6.QtWidgets import QApplication, QDialog
from src.cloud import recovery
from src.cloud.ui import run_application


class ReleaseCloudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def run_recovery_callback(self, approved):
        client = Mock()
        client.allowed.return_value = False
        authenticate = Mock(return_value=approved)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def login_dialog(_client, recovery):
                recovery()
                dialog = Mock()
                dialog.exec.return_value = QDialog.Rejected
                return dialog
            with patch('src.cloud.ui.ClientConfig.packaged'), \
                 patch('src.cloud.ui.CloudClient', return_value=client), \
                 patch('src.cloud.ui.LoginDialog', side_effect=login_dialog), \
                 patch('src.cloud.ui.RecoveryDialog') as recovery_dialog:
                recovery_dialog.return_value.restored = False
                self.assertEqual(run_application(self.app, Mock(), Mock(), authenticate,
                    root/'attendance.db', root), 0)
        return authenticate, recovery_dialog

    def test_recovery_before_activation_requires_existing_local_admin(self):
        authenticate, recovery_dialog = self.run_recovery_callback(False)
        authenticate.assert_called_once_with()
        recovery_dialog.assert_not_called()

    def test_offline_recovery_still_available_after_local_admin_authentication(self):
        authenticate, recovery_dialog = self.run_recovery_callback(True)
        authenticate.assert_called_once_with()
        recovery_dialog.assert_called_once()
        recovery_dialog.return_value.exec.assert_called_once_with()

    def test_key_export_write_failure_does_not_publish_incomplete_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'school.frkey'
            with patch.object(recovery.os, 'fsync', side_effect=OSError('synthetic disk full')):
                with self.assertRaises(OSError):
                    recovery.export_key(path, str(uuid.uuid4()), b'k'*32)
            self.assertFalse(path.exists())
            self.assertFalse(list(Path(directory).glob('.key-*')))

    def test_key_export_permission_failure_preserves_existing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'school.frkey'
            school = str(uuid.uuid4())
            recovery.export_key(path, school, b'k'*32)
            previous = path.read_bytes()
            with self.assertRaises(FileExistsError):
                recovery.export_key(path, school, b'x'*32)
            self.assertEqual(path.read_bytes(), previous)
            other = Path(directory)/'new.frkey'
            with patch.object(recovery.tempfile, 'mkstemp', side_effect=PermissionError('synthetic denied')):
                with self.assertRaises(PermissionError):
                    recovery.export_key(other, school, b'x'*32)
            self.assertFalse(other.exists())
            self.assertEqual(recovery.import_key(path, school), b'k'*32)

    def test_wrong_key_and_storage_failures_have_actionable_nonempty_feedback(self):
        from cryptography.exceptions import InvalidTag
        from src.cloud.ui import operation_error
        from src import localization
        previous = localization.language()
        localization.configure(self.app, 'en')
        try:
            self.assertEqual(str(InvalidTag()), '')
            message = operation_error(InvalidTag())
            self.assertIn('key', message)
            self.assertIn('not changed', message)
            secret_path = '/private/synthetic-school/attendance.db'
            message = operation_error(PermissionError(secret_path))
            self.assertIn('permission', message.lower())
            self.assertNotIn(secret_path, message)
            self.assertIn('disk', operation_error(OSError('synthetic disk full')))
        finally:
            localization.configure(self.app, previous)

    def client_for_dialog(self):
        import time
        client = Mock()
        client.credential = None
        client.require_lease.return_value = {'school_name': 'Synthetic school', 'student_limit': 900,
            'subscription_expires_at': time.time()+86400, 'backups_enabled': True}
        client.state = {}
        client.remembered = True
        client.config.url = 'https://synthetic.invalid'
        return client

    def test_account_language_change_is_saved_without_mutating_active_camera_ui(self):
        from src.cloud.ui import AccountDialog, LoginDialog
        from src import localization
        from PySide6.QtCore import Qt
        localization.configure(self.app, 'en')
        client = self.client_for_dialog()
        with patch('src.cloud.ui.preferred_language', return_value='en'), \
             patch('src.cloud.ui.save_language_preference') as save:
            account = AccountDialog(client, 'unused.db')
            account.language_choice.setCurrentIndex(1)
            save.assert_called_once_with('fa')
            self.assertEqual(localization.language(), 'en')
            self.assertEqual(self.app.layoutDirection(), Qt.LeftToRight)
            self.assertIn('Restart', account.status.text())
            nested = LoginDialog(client, account, apply_language_live=False)
            nested.language_box.setCurrentIndex(1)
            self.assertEqual(localization.language(), 'en')
            self.assertIn('Restart', nested.status.text())
            client.login.assert_not_called()
            client.renew.assert_not_called()
            with patch('src.cloud.ui.save_language_preference', side_effect=OSError('synthetic storage failure')):
                account.language_choice.setCurrentIndex(0)
                self.assertNotIn('saved', account.status.text())
                self.assertIn('Storage', account.status.text())
            nested.reject(); account.reject()
            nested.deleteLater(); account.deleteLater()

    def test_account_and_recovery_screens_render_reviewed_persian_catalog(self):
        from src.cloud.ui import AccountDialog, RecoveryDialog, operation_error
        from src import localization
        from cryptography.exceptions import InvalidTag
        from PySide6.QtCore import Qt
        previous = localization.language()
        localization.configure(self.app, 'fa')
        try:
            client = self.client_for_dialog()
            account = AccountDialog(client, 'unused.db')
            local = RecoveryDialog('unused.db')
            self.assertEqual(self.app.layoutDirection(), Qt.RightToLeft)
            self.assertEqual(account.windowTitle(), 'اشتراک و پشتیبان مدرسه')
            self.assertEqual(account.upload.text(), 'ساخت و ارسال بکاپ اکنون')
            self.assertEqual(local.windowTitle(), 'خروجی و بازیابی محلی — بدون نیاز به اشتراک فعال')
            self.assertIn('کلید', operation_error(InvalidTag()))
            account.reject(); local.reject()
            account.deleteLater(); local.deleteLater()
        finally:
            localization.configure(self.app, previous)

    def test_backup_consent_storage_failure_disables_future_session_uploads(self):
        from src.cloud.ui import AccountDialog
        from src import localization
        localization.configure(self.app, 'en')
        for selected in (True, False):
            with self.subTest(consent=selected):
                client = self.client_for_dialog()
                client.state['automatic_backups'] = True
                client.save_state.side_effect = PermissionError('synthetic denied')
                dialog = AccountDialog(client, 'unused.db')
                dialog.auto_changed(selected)
                self.assertFalse(client.state['automatic_backups'])
                self.assertFalse(dialog.auto.isChecked())
                self.assertIn('this session', dialog.status.text())
                self.assertIn('restart', dialog.status.text())
                dialog.reject(); dialog.deleteLater()
