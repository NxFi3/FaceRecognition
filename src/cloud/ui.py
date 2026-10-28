"""Qt account UI and asynchronous control plane; no vision model scheduling."""
from datetime import datetime
from pathlib import Path
import threading
import time
import webbrowser

from filelock import FileLock, Timeout
from PySide6.QtCore import QObject, Signal, QTimer, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit, QLabel,
    QPushButton, QMessageBox, QCheckBox, QListWidget, QDialogButtonBox, QFileDialog, QComboBox, QApplication)
from .client import AccessDenied, ClientConfig, CloudClient, CloudError
from .entitlement import bind_gate
try:
    from ..localization import text as tr, configure, language, error_text, save_language_preference, preferred_language
except ImportError:
    from localization import text as tr, configure, language, error_text, save_language_preference, preferred_language


def operation_error(error):
    """Useful translated failure feedback without exposing local paths/tokens."""
    from cryptography.exceptions import InvalidTag
    import requests
    if isinstance(error, InvalidTag):
        return tr('Recovery key is incorrect or the backup is corrupted. Existing school data was not changed.')
    if isinstance(error, requests.RequestException):
        return tr('The connection was interrupted. Check connectivity and try again; a valid offline license remains usable.')
    if isinstance(error, FileExistsError):
        return tr('The destination file already exists. Choose a new file name; existing data was not overwritten.')
    if isinstance(error, PermissionError):
        return tr('Storage permission was denied. Choose an accessible location and check the school account permissions.')
    if isinstance(error, OSError):
        return tr('Storage operation failed. Check free disk space, permissions and recovery media, then try again.')
    return error_text(error) or tr('The operation failed. Check the selected file, account permissions and available storage.')


class Jobs(QObject):
    completed = Signal(str, object, object)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.closed = threading.Event()
        self.busy = set()
    def start(self, name, function):
        if name in self.busy or self.closed.is_set():
            return
        self.busy.add(name)
        def run():
            result, error = None, None
            try:
                result = function()
            except Exception as exc:
                error = exc
            if not self.closed.is_set():
                try:
                    self.completed.emit(name, result, error)
                except RuntimeError:
                    pass  # QApplication has already destroyed its QObject tree.
        threading.Thread(target=run, name="cloud-" + name, daemon=True).start()
    def finished(self, name):
        self.busy.discard(name)
    def close(self):
        self.closed.set()


class LoginDialog(QDialog):
    def __init__(self, client, parent=None, recovery=None, apply_language_live=True):
        super().__init__(parent)
        self.client = client
        self.apply_language_live = apply_language_live
        self.setWindowTitle("NxFi · FaceRecognition")
        self.setMinimumWidth(480)
        self.setStyleSheet("""
            QDialog { background: #f5f6fc; color: #17223b; }
            QLabel { color: #17223b; font-size: 14px; }
            QLabel#nxfi-brand { font-size: 25px; font-weight: 700; color: #5145cd; }
            QLineEdit, QComboBox { background: white; border: 1px solid #d7dced;
                border-radius: 8px; padding: 10px; min-height: 22px; }
            QLineEdit:focus, QComboBox:focus { border: 2px solid #6554e8; }
            QPushButton { background: white; color: #344161; border: 1px solid #d7dced;
                border-radius: 8px; padding: 12px; font-weight: 600; }
            QPushButton#nxfi-primary { background: #6554e8; color: white; border: none; }
            QPushButton:disabled { background: #e3e5ef; color: #727b92; }
            QLabel#nxfi-status { color: #9f3d31; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(16)
        brand = QLabel('NxFi Technologies')
        brand.setObjectName('nxfi-brand')
        layout.addWidget(brand)
        self.language_box = QComboBox()
        self.language_box.addItem('English', 'en')
        self.language_box.addItem('فارسی', 'fa')
        selected_language = language() if apply_language_live else preferred_language()
        self.language_box.setCurrentIndex(1 if selected_language == 'fa' else 0)
        layout.addWidget(self.language_box)
        info = QLabel()
        info.setTextFormat(Qt.PlainText)
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        self.email = QLineEdit()
        self.email.setMaxLength(150)
        self.email.setLayoutDirection(Qt.LeftToRight)
        self.password = QLineEdit()
        self.password.setMaxLength(1024)
        self.password.setLayoutDirection(Qt.LeftToRight)
        self.password.setEchoMode(QLineEdit.Password)
        self.email_label, self.password_label = QLabel(), QLabel()
        form.addRow(self.email_label, self.email)
        form.addRow(self.password_label, self.password)
        layout.addLayout(form)
        self.remember_checkbox = QCheckBox(tr('Remember me on this device'))
        self.remember_checkbox.setObjectName('nxfi-remember-me')
        self.remember_checkbox.setChecked(True)
        layout.addWidget(self.remember_checkbox)
        self.status = QLabel("")
        self.status.setObjectName("nxfi-status")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.login_button = QPushButton(tr('Sign in and activate'))
        self.login_button.setObjectName("nxfi-primary")
        self.login_button.clicked.connect(self.login)
        layout.addWidget(self.login_button)
        website = QPushButton()
        website.clicked.connect(lambda: webbrowser.open(client.config.url + "/login/"))
        layout.addWidget(website)
        if recovery:
            export = QPushButton(tr('Offline export & recovery'))
            export.clicked.connect(recovery)
            layout.addWidget(export)
        self.jobs = Jobs(self)
        self.jobs.completed.connect(self.done_job, Qt.QueuedConnection)
        self.finished.connect(lambda _: self.jobs.close())
        self.info, self.website, self.export = info, website, export if recovery else None
        self._status_key = None
        self.language_box.currentIndexChanged.connect(self.change_language)
        self.retranslate()
        if client.credential:
            self.login_button.setEnabled(False)
            self.set_status("Checking saved subscription…")
            QTimer.singleShot(0, lambda: self.jobs.start("login", client.renew))
    def retranslate(self):
        self.info.setText(tr('Sign in with your NxFi account to activate FaceRecognition.') + '\n' + self.client.config.url)
        self.email_label.setText(tr('NxFi account email'))
        self.password_label.setText(tr('Password'))
        self.login_button.setText(tr('Sign in and activate'))
        self.remember_checkbox.setText(tr('Remember me on this device'))
        self.website.setText(tr('Create an account / reset password'))
        self.language_box.setAccessibleName(tr('Language'))
        if self.export:
            self.export.setText(tr('Offline export & recovery'))
        if self._status_key:
            self.status.setText(tr(self._status_key))

    def set_status(self, key):
        self._status_key = key
        self.status.setText(tr(key))

    def change_language(self):
        try:
            if self.apply_language_live:
                configure(QApplication.instance(), self.language_box.currentData(), persist=True)
                self.retranslate()
            else:
                save_language_preference(self.language_box.currentData())
                self.set_status('Language preference saved. Restart the application to apply it safely.')
        except OSError as error:
            self._status_key = None
            self.status.setText(operation_error(error))

    def login(self):
        self.login_button.setEnabled(False)
        email, password = self.email.text().strip(), self.password.text()
        remember = self.remember_checkbox.isChecked()  # Read Qt state on GUI thread.
        self.password.clear()
        self.set_status("Connecting securely…")
        self.jobs.start("login", lambda: self.client.login(email, password, remember=remember))
    def done_job(self, name, result, error):
        self.jobs.finished(name)
        self.login_button.setEnabled(True)
        if error:
            self._status_key = None
            self.status.setText(operation_error(error))
        else:
            self.accept()


class AccountDialog(QDialog):
    def __init__(self, client, database_path, can_restore=False, parent=None):
        super().__init__(parent)
        self.client, self.database_path, self.can_restore = client, database_path, can_restore
        self.restored = False
        self.setWindowTitle(tr('School subscription & backups'))
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        language_label = QLabel(tr('Language for the next application launch'))
        layout.addWidget(language_label)
        self.language_choice = QComboBox()
        self.language_choice.addItem('English', 'en')
        self.language_choice.addItem('فارسی', 'fa')
        self.language_choice.setCurrentIndex(1 if preferred_language() == 'fa' else 0)
        self.language_choice.setAccessibleName(tr('Language for the next application launch'))
        self.language_choice.currentIndexChanged.connect(self.save_language_choice)
        layout.addWidget(self.language_choice)
        lease = client.require_lease()
        expiry = datetime.fromtimestamp(lease["subscription_expires_at"]).strftime("%Y-%m-%d %H:%M")
        info = QLabel(tr('School: {school}\nSubscription expires (local system time): {expiry}\nStudent enrollment capacity: {capacity}').format(school=lease['school_name'], expiry=expiry, capacity=lease['student_limit']))
        info.setTextFormat(Qt.PlainText)
        info.setWordWrap(True)
        layout.addWidget(info)
        online = QPushButton(tr('Check or renew online / sign in again'))
        online.clicked.connect(lambda: LoginDialog(client, self, apply_language_live=False).exec())
        layout.addWidget(online)
        if not client.remembered:
            layout.addWidget(QLabel(tr("Online sign-in was not saved; the offline license is stored in the operating system's secure vault.")))
        notice = QLabel(tr('The backup includes the full database, student records, attendance and registered face data, including event images. It is encrypted on this device with the school key before upload. The server does not receive the recovery key. Recovery on a new device is impossible without the key.'))
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.auto = QCheckBox(tr('I consent to automatic daily upload of encrypted face-data and image backups'))
        self.auto.setChecked(bool(client.state.get("automatic_backups", False)))
        self.auto.setEnabled(bool(lease.get("backups_enabled")))
        self.auto.toggled.connect(self.auto_changed)
        layout.addWidget(self.auto)
        key_export = QPushButton(tr('Save the school recovery key in a secure location'))
        key_export.clicked.connect(lambda: self.configure_key(False))
        layout.addWidget(key_export)
        key_import = QPushButton(tr('Import an existing school recovery key'))
        key_import.clicked.connect(lambda: self.configure_key(True))
        layout.addWidget(key_import)
        roster = QPushButton(tr('Manage active / archived capacity roster'))
        roster.clicked.connect(self.roster)
        layout.addWidget(roster)
        self.upload = QPushButton(tr('Create and upload backup now'))
        self.upload.setEnabled(bool(lease.get("backups_enabled")))
        self.upload.clicked.connect(self.backup)
        layout.addWidget(self.upload)
        self.list_button = QPushButton(tr('Load backup list'))
        self.list_button.clicked.connect(lambda: self.jobs.start("list", client.backups))
        layout.addWidget(self.list_button)
        self.listing = QListWidget()
        layout.addWidget(self.listing)
        self.items = []
        self.restore_button = QPushButton(tr('Restore selected backup'))
        self.restore_button.setEnabled(can_restore)
        self.restore_button.clicked.connect(self.restore)
        layout.addWidget(self.restore_button)
        if not can_restore:
            label = QLabel(tr('To restore, close the application and reopen this page before entering the camera workspace.'))
            label.setWordWrap(True)
            layout.addWidget(label)
        self.status = QLabel("")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr('Continue'))
        buttons.button(QDialogButtonBox.Cancel).setText(tr('Close'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.jobs = Jobs(self)
        self.jobs.completed.connect(self.done_job, Qt.QueuedConnection)
        self.finished.connect(lambda _: self.jobs.close())
        self.renew_timer = QTimer(self)
        self.renew_timer.setInterval(300000)
        self.renew_timer.timeout.connect(lambda: self.jobs.start("renew", client.renew))
        # Main controller handles opportunistic sync after startup; no startup network dependency.
        self.finished.connect(lambda _: self.renew_timer.stop())
    def save_language_choice(self):
        try:
            save_language_preference(self.language_choice.currentData())
        except OSError as error:
            self.status.setText(operation_error(error))
        else:
            self.status.setText(tr('Language preference saved. Restart the application to apply it safely.'))

    def roster(self):
        RosterDialog(self.database_path, self).exec()

    def configure_key(self, importing):
        if importing:
            path, _ = QFileDialog.getOpenFileName(self, tr('School recovery key'), '', tr('Recovery key (*.frkey)'))
        else:
            QMessageBox.information(self, tr('Confidential school key'), tr('Keep the key on separate storage in a secure place; do not upload it with the backup. Losing every copy makes recovery impossible. The key file is unencrypted; restrict access to it.'))
            path, _ = QFileDialog.getSaveFileName(self, tr('New recovery key or another export of the existing key'), '', tr('Recovery key (*.frkey)'))
        if path:
            self.jobs.start('key', lambda: self.client.configure_backup_key(path, importing))

    def auto_changed(self, checked):
        self.client.state["automatic_backups"] = checked
        try:
            self.client.save_state()
        except OSError:
            # Never start new automatic uploads after a consent-save failure.
            # An older persisted preference may remain until storage recovers;
            # warn explicitly rather than silently reporting a durable opt-out.
            self.client.state["automatic_backups"] = False
            blocked = self.auto.blockSignals(True)
            try:
                self.auto.setChecked(False)
            finally:
                self.auto.blockSignals(blocked)
            self.status.setText(tr('The backup preference could not be saved. No new automatic uploads will start in this session. Check storage and confirm the preference before restart; an older saved preference may remain.'))
    def backup(self):
        if QMessageBox.question(self, tr('Upload backup'), tr("Upload this school's full database to the server?"), QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.upload.setEnabled(False)
        self.status.setText(tr('Creating and uploading backup…'))
        self.jobs.start("backup", lambda: self.client.backup(self.database_path))
    def restore(self):
        row = self.listing.currentRow()
        if not 0 <= row < len(self.items):
            return
        if QMessageBox.warning(self, tr('Restore database'), tr('Replace the current database with the selected backup? A local copy of the current state will be retained. Continue?'), QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        item = self.items[row]
        self.setEnabled(False)
        self.jobs.start("restore", lambda: self.client.restore(item["id"], self.database_path))
    def done_job(self, name, result, error):
        self.jobs.finished(name)
        self.setEnabled(True)
        self.upload.setEnabled(bool(self.client.require_lease().get("backups_enabled")) if self.client.allowed() else False)
        if error:
            self.status.setText(operation_error(error))
        elif name == "list":
            self.items = result
            self.listing.clear()
            for item in result:
                self.listing.addItem(f"{item['created_at']} — {item['size']} bytes")
            self.status.setText(tr('Backup list loaded.'))
        else:
            if name == "restore":
                self.restored = True
            self.status.setText(tr('Operation completed successfully.'))
    def reject(self):
        if "restore" in self.jobs.busy:
            self.status.setText(tr('Wait until recovery finishes.'))
            return
        super().reject()
    def accept(self):
        if "restore" in self.jobs.busy:
            self.status.setText(tr('Wait until recovery finishes.'))
            return
        super().accept()


class CloudController(QObject):
    def __init__(self, app, window, client, database_path):
        super().__init__(window)
        self.app, self.window, self.client, self.database_path = app, window, client, database_path
        self.jobs = Jobs(self)
        self.jobs.completed.connect(self.done_job, Qt.QueuedConnection)
        app.aboutToQuit.connect(self.jobs.close)
        self.next_renewal = time.monotonic() + 5
        self.retry_seconds = 60
        self.online_ready = False
        self.next_checkpoint = time.monotonic() + 60
        self.next_backup_attempt = 0
        self.locked = False
        self.license_label = QLabel()
        self.license_label.setTextFormat(Qt.PlainText)
        window.statusBar().addPermanentWidget(self.license_label)
        self.update_label()
        menu = window.menuBar().addMenu(tr('Subscription & backups'))
        menu.addAction(tr('Subscription validity & backups'), self.account)
        menu.addAction(tr('School account on the website'), lambda: webbrowser.open(client.config.url + "/account/"))
        menu.addAction(tr('Sign out of the school account'), lambda: self.jobs.start("logout", client.logout))
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
    def update_label(self):
        try:
            lease = self.client.require_lease()
        except AccessDenied:
            self.license_label.setText(tr('Invalid subscription'))
            return
        expiry = datetime.fromtimestamp(lease["subscription_expires_at"]).strftime("%Y-%m-%d")
        self.license_label.setText(tr('{school} | Valid until {expiry} | Capacity {capacity}').format(school=lease['school_name'], expiry=expiry, capacity=lease['student_limit']))
    def account(self):
        try:
            AccountDialog(self.client, self.database_path, parent=self.window).exec()
        except AccessDenied:
            self.tick()
    def tick(self):
        try:
            lease = self.client.require_lease()
        except AccessDenied:
            if not self.locked:
                self.locked = True
                self.window.hide()
                self.window.stop_camera()
                QMessageBox.warning(None, tr('Subscription'), tr('Access has expired or online clock verification is required. Data has not been deleted. On the next launch, sign in online to renew, or use local export / recovery.'))
                self.app.quit()
            return
        now = time.monotonic()
        if now >= self.next_renewal:
            self.next_renewal = now + self.retry_seconds
            self.jobs.start("renew", self.client.renew)
        if now >= self.next_checkpoint:
            self.next_checkpoint = now + 60
            self.jobs.start('checkpoint', self.client.checkpoint_time)
        if self.online_ready and lease.get("backups_enabled") and self.client.state.get("automatic_backups") and time.time() - self.client.state.get("last_backup_at", 0) >= 86400 and now >= self.next_backup_attempt:
            self.next_backup_attempt = now + 3600
            self.jobs.start("backup", lambda: self.client.backup(self.database_path))
    def done_job(self, name, result, error):
        self.jobs.finished(name)
        if name == 'renew':
            self.online_ready = error is None
            self.retry_seconds = min(self.retry_seconds * 2, 3600) if error else 60
            self.next_renewal = time.monotonic() + (self.retry_seconds if error else 3600)
            self.update_label()
        if name == "logout":
            if error:
                QMessageBox.warning(self.window, tr('Local sign-out'), tr('Saved sign-in was removed from this device, but server revocation was not confirmed. Use the website account to revoke the device definitively.'))
            self.app.quit()
        if name == "backup":
            self.window.statusBar().showMessage(tr('Online backup uploaded.') if not error else tr('Backup upload failed; check the subscription menu.'), 15000)


def run_application(app, window_class, init_db, ensure_local_admin, database_path, base_dir, open_reports=False):
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(database_path.parent / ".application.lock"))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        QMessageBox.warning(None, tr('Application is already running'), tr('Close the other application instance first.'))
        return 1
    client, window = None, None
    try:
        try:
            config = ClientConfig.packaged(base_dir)
            client = CloudClient(config, database_path.parent / "cloud-state.json")
        except Exception:
            guidance = tr('The trusted service configuration is missing or invalid. Reinstall the official package or contact NxFi support.')
            if not getattr(__import__('sys'), 'frozen', False):
                guidance += '\n\n' + tr('Source checkouts require a valid subscription-client.json for a trusted HTTPS service. The HTTP development website cannot activate this desktop.')
            QMessageBox.critical(None, tr('Incomplete installation'), guidance)
            return 1
        from .recovery import recover_interrupted_restore
        recover_interrupted_restore(database_path)
        init_db()
        recovered = []
        def local_recovery():
            # The pre-activation data-ownership path must retain the same local
            # administrator boundary as the operational application.
            if not ensure_local_admin():
                return
            dialog = RecoveryDialog(database_path)
            dialog.exec()
            if dialog.restored:
                recovered.append(True)
        if not client.allowed() and LoginDialog(client, recovery=local_recovery).exec() != QDialog.Accepted:
            return 0
        if recovered:
            init_db()
        if not ensure_local_admin():
            return 0
        try:
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(database_path)) as connection:
                bound = connection.execute("SELECT value FROM app_settings WHERE key='cloud_school_id'").fetchone()
            if not bound and QMessageBox.question(None, tr('Bind database to school'), tr('Bind this device database to school “{school}”?').format(school=client.require_lease()['school_name']), QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return 0
            client.bind_database(database_path)
            bind_gate(client)
        except Exception as exc:
            QMessageBox.critical(None, tr('School binding'), operation_error(exc))
            return 1
        startup_account = AccountDialog(client, database_path, can_restore=True)
        if startup_account.exec() != QDialog.Accepted:
            return 0
        if startup_account.restored:
            init_db()
            if not ensure_local_admin():
                return 0
        if not client.allowed():
            return 1
        window = window_class()
        window._cloud_controller = CloudController(app, window, client, database_path)
        window.show()
        if open_reports:
            window.show_page(5)
            window.report_tabs.setCurrentIndex(1)
        return app.exec()
    finally:
        if client is not None:
            client.close()
        if window is not None:
            window._shutting_down = True
            window._camera_supervisor_enabled = False
            workers = list(window.camera_workers.values())
            window.stop_camera()
            # Retain QThread ownership until native inference has returned. Never
            # release the restore/instance lock while an old camera can write DB.
            for worker in workers:
                if worker.isRunning():
                    worker.stop()
                    worker.wait()
        lock.release()
        bind_gate(None)


class RosterDialog(QDialog):
    """Capacity roster only; does not alter recognition or attendance decisions."""
    def __init__(self, database_path, parent=None):
        super().__init__(parent)
        self.database_path = database_path
        self.setWindowTitle(tr('Active / archived capacity roster'))
        layout = QVBoxLayout(self)
        note = QLabel(tr('Archiving only classifies the capacity roster. It does not delete records or faces, or change recognition and attendance decisions. It does not disable recognition of a person.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.listing = QListWidget()
        layout.addWidget(self.listing)
        button = QPushButton(tr("Toggle the selected student's archive status"))
        button.clicked.connect(self.toggle)
        layout.addWidget(button)
        self.refresh()
    def refresh(self):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.rows = connection.execute('SELECT id, student_code, name, capacity_archived FROM students ORDER BY id').fetchall()
        self.listing.clear()
        for _, code, name, archived in self.rows:
            self.listing.addItem(f'{code} — {name} — ' + (tr('Archived') if archived else tr('Active')))
    def toggle(self):
        row = self.listing.currentRow()
        if not 0 <= row < len(self.rows):
            return
        if QMessageBox.question(self, tr('Change capacity roster'), tr('Change the selected status? Historical records will be preserved.'), QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            # Use the same module/path as the running application, not a second DB.
            import sys
            db = sys.modules.get('database') or sys.modules.get('src.database')
            db.set_student_capacity_archived(self.rows[row][0], not self.rows[row][3])
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, tr('Capacity'), operation_error(exc))


class RecoveryDialog(QDialog):
    """Offline data ownership path, behind the existing local-admin password."""
    def __init__(self, database_path, parent=None):
        super().__init__(parent)
        self.database_path = Path(database_path)
        self.restored = False
        self.setWindowTitle(tr('Local export & recovery — no active subscription required'))
        layout = QVBoxLayout(self)
        note = QLabel(tr('Cameras do not run in this mode. Export attendance CSV or a full encrypted backup using the school key. Restore requires a backup for this school and the correct key. Export files contain confidential information; choose a secure location.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel('')
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        for title, operation in [(tr('Export attendance CSV'), self.export_csv),
                                 (tr('Export full encrypted backup'), lambda: self.bundle(False)),
                                 (tr('Restore full local backup'), lambda: self.bundle(True))]:
            button = QPushButton(title)
            button.clicked.connect(operation)
            layout.addWidget(button)
        layout.addWidget(self.status)
        self.jobs = Jobs(self)
        self.jobs.completed.connect(self.done_job, Qt.QueuedConnection)
        self.finished.connect(lambda _: self.jobs.close())
    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, tr('Confidential attendance export'), '', tr('CSV (*.csv)'))
        if path:
            from .local_export import export_attendance
            self.jobs.start('csv', lambda: export_attendance(self.database_path, path))
    def bundle(self, restoring):
        from .local_export import bound_school
        from .recovery import import_key, make_bundle, unpack_bundle, install_bundle
        import tempfile
        try:
            school = bound_school(self.database_path)
            keypath, _ = QFileDialog.getOpenFileName(self, tr('Recovery key for this school'), '', tr('Recovery key (*.frkey)'))
            if not keypath:
                return
            key = import_key(keypath, school)
            if restoring:
                path, _ = QFileDialog.getOpenFileName(self, tr('Full backup'), '', tr('Backup (*.frbackup)'))
            else:
                path, _ = QFileDialog.getSaveFileName(self, tr('Full encrypted backup'), '', tr('Backup (*.frbackup)'))
            if not path:
                return
            if restoring and QMessageBox.warning(self, tr('Replace local data'), tr('Replace the database and event images? The previous version will be retained.'), QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
            def work():
                if restoring:
                    with tempfile.TemporaryDirectory(prefix='.local-restore-', dir=self.database_path.parent) as directory:
                        unpack_bundle(path, directory, school, key)
                        install_bundle(directory, self.database_path)
                else:
                    make_bundle(self.database_path, path, school, key)
            self.setEnabled(False)
            self.jobs.start('restore' if restoring else 'export', work)
        except Exception as exc:
            self.status.setText(operation_error(exc))
    def done_job(self, name, result, error):
        self.jobs.finished(name)
        self.setEnabled(True)
        if name == 'restore' and not error:
            self.restored = True
        self.status.setText(operation_error(error) if error else tr('Operation completed. Restart the application for normal access.'))
    def reject(self):
        if self.jobs.busy:
            return
        super().reject()
