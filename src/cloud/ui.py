"""Qt account UI and asynchronous control plane; no vision model scheduling."""
from datetime import datetime
from pathlib import Path
import threading
import time
import webbrowser

from filelock import FileLock, Timeout
from PySide6.QtCore import QObject, Signal, QTimer, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit, QLabel,
    QPushButton, QMessageBox, QCheckBox, QListWidget, QDialogButtonBox, QFileDialog)
from .client import AccessDenied, ClientConfig, CloudClient, CloudError
from .entitlement import bind_gate


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
    def __init__(self, client, parent=None, recovery=None):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("ورود به حساب مدرسه")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        info = QLabel("برای استفاده از برنامه، حساب مدرسه و اشتراک فعال لازم است.\n" + client.config.url)
        info.setTextFormat(Qt.PlainText)
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        self.email = QLineEdit()
        self.email.setMaxLength(150)
        self.password = QLineEdit()
        self.password.setMaxLength(1024)
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("ایمیل مدرسه", self.email)
        form.addRow("رمز حساب سایت", self.password)
        layout.addLayout(form)
        self.status = QLabel("")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.login_button = QPushButton("ورود و دریافت اعتبار اشتراک")
        self.login_button.clicked.connect(self.login)
        layout.addWidget(self.login_button)
        website = QPushButton("ثبت‌نام / بازیابی رمز در سایت")
        website.clicked.connect(lambda: webbrowser.open(client.config.url + "/login/"))
        layout.addWidget(website)
        if recovery:
            export = QPushButton('خروجی / بازیابی محلی اطلاعات مدرسه (بدون اینترنت)')
            export.clicked.connect(recovery)
            layout.addWidget(export)
        self.jobs = Jobs(self)
        self.jobs.completed.connect(self.done_job, Qt.QueuedConnection)
        self.finished.connect(lambda _: self.jobs.close())
        if client.credential:
            self.login_button.setEnabled(False)
            self.status.setText("در حال بررسی اشتراک ذخیره‌شده…")
            QTimer.singleShot(0, lambda: self.jobs.start("login", client.renew))
    def login(self):
        self.login_button.setEnabled(False)
        email, password = self.email.text().strip(), self.password.text()
        self.password.clear()
        self.status.setText("در حال ارتباط با سرویس…")
        self.jobs.start("login", lambda: self.client.login(email, password))
    def done_job(self, name, result, error):
        self.jobs.finished(name)
        self.login_button.setEnabled(True)
        if error:
            self.status.setText(str(error) if isinstance(error, Exception) else "ورود انجام نشد.")
        else:
            self.accept()


class AccountDialog(QDialog):
    def __init__(self, client, database_path, can_restore=False, parent=None):
        super().__init__(parent)
        self.client, self.database_path, self.can_restore = client, database_path, can_restore
        self.restored = False
        self.setWindowTitle("اشتراک و پشتیبان مدرسه")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        lease = client.require_lease()
        expiry = datetime.fromtimestamp(lease["subscription_expires_at"]).strftime("%Y-%m-%d %H:%M")
        info = QLabel(f"مدرسه: {lease['school_name']}\nپایان اشتراک به وقت این سیستم: {expiry}\nظرفیت ثبت دانش‌آموز: {lease['student_limit']}")
        info.setTextFormat(Qt.PlainText)
        info.setWordWrap(True)
        layout.addWidget(info)
        online = QPushButton('بررسی / تمدید آنلاین و ورود دوباره به حساب سایت')
        online.clicked.connect(lambda: LoginDialog(client, self).exec())
        layout.addWidget(online)
        if not client.remembered:
            layout.addWidget(QLabel("ورود حساب سایت ذخیره نشده؛ مجوز آفلاین در خزانهٔ امن سیستم نگهداری می‌شود."))
        notice = QLabel("بکاپ شامل تمام دیتابیس، اطلاعات دانش‌آموزان، حضور و غیاب و داده‌های ثبت‌شدهٔ چهره است. پیش از ارسال، با کلید مدرسه در همین دستگاه رمزگذاری می‌شود و تصاویر رویدادها را هم شامل می‌شود. سرور کلید بازیابی را دریافت نمی‌کند. بدون کلید، بازیابی روی دستگاه جدید ممکن نیست.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.auto = QCheckBox("با ارسال خودکار روزانهٔ بکاپ رمزگذاری‌شدهٔ اطلاعات چهره و تصاویر به سرور موافقم")
        self.auto.setChecked(bool(client.state.get("automatic_backups", False)))
        self.auto.setEnabled(bool(lease.get("backups_enabled")))
        self.auto.toggled.connect(self.auto_changed)
        layout.addWidget(self.auto)
        key_export = QPushButton('ذخیرهٔ کلید بازیابی مدرسه در محل امن')
        key_export.clicked.connect(lambda: self.configure_key(False))
        layout.addWidget(key_export)
        key_import = QPushButton('واردکردن کلید بازیابی موجود مدرسه')
        key_import.clicked.connect(lambda: self.configure_key(True))
        layout.addWidget(key_import)
        roster = QPushButton('مدیریت فهرست فعال / آرشیو ظرفیت')
        roster.clicked.connect(self.roster)
        layout.addWidget(roster)
        self.upload = QPushButton("ساخت و ارسال بکاپ اکنون")
        self.upload.setEnabled(bool(lease.get("backups_enabled")))
        self.upload.clicked.connect(self.backup)
        layout.addWidget(self.upload)
        self.list_button = QPushButton("دریافت فهرست بکاپ‌ها")
        self.list_button.clicked.connect(lambda: self.jobs.start("list", client.backups))
        layout.addWidget(self.list_button)
        self.listing = QListWidget()
        layout.addWidget(self.listing)
        self.items = []
        self.restore_button = QPushButton("بازیابی بکاپ انتخاب‌شده")
        self.restore_button.setEnabled(can_restore)
        self.restore_button.clicked.connect(self.restore)
        layout.addWidget(self.restore_button)
        if not can_restore:
            label = QLabel("برای بازیابی، برنامه را ببندید و در اجرای بعد، پیش از ورود به محیط دوربین از همین صفحه استفاده کنید.")
            label.setWordWrap(True)
            layout.addWidget(label)
        self.status = QLabel("")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("ادامه")
        buttons.button(QDialogButtonBox.Cancel).setText("بستن صفحه")
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
    def roster(self):
        RosterDialog(self.database_path, self).exec()

    def configure_key(self, importing):
        if importing:
            path, _ = QFileDialog.getOpenFileName(self, 'کلید بازیابی مدرسه', '', 'Recovery key (*.frkey)')
        else:
            QMessageBox.information(self, 'کلید محرمانهٔ مدرسه', 'کلید را روی حافظهٔ جداگانه در محل امن نگه دارید؛ همراه بکاپ روی سرور بارگذاری نکنید. گم‌شدن همهٔ نسخه‌های کلید یعنی از دست رفتن امکان بازیابی. فایل کلید بدون رمز است؛ دسترسی به آن را محدود کنید.')
            path, _ = QFileDialog.getSaveFileName(self, 'کلید جدید یا خروجی دوبارهٔ کلید فعلی', '', 'Recovery key (*.frkey)')
        if path:
            self.jobs.start('key', lambda: self.client.configure_backup_key(path, importing))

    def auto_changed(self, checked):
        self.client.state["automatic_backups"] = checked
        self.client.save_state()
    def backup(self):
        if QMessageBox.question(self, "ارسال بکاپ", "دیتابیس کامل این مدرسه به سرور ارسال شود؟", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.upload.setEnabled(False)
        self.status.setText("در حال ساخت و ارسال بکاپ…")
        self.jobs.start("backup", lambda: self.client.backup(self.database_path))
    def restore(self):
        row = self.listing.currentRow()
        if not 0 <= row < len(self.items):
            return
        if QMessageBox.warning(self, "بازیابی دیتابیس", "دیتابیس فعلی با بکاپ انتخاب‌شده جایگزین می‌شود. یک نسخهٔ محلی از وضعیت قبلی نگه داشته خواهد شد. ادامه می‌دهید؟", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        item = self.items[row]
        self.setEnabled(False)
        self.jobs.start("restore", lambda: self.client.restore(item["id"], self.database_path))
    def done_job(self, name, result, error):
        self.jobs.finished(name)
        self.setEnabled(True)
        self.upload.setEnabled(bool(self.client.require_lease().get("backups_enabled")) if self.client.allowed() else False)
        if error:
            self.status.setText(str(error))
        elif name == "list":
            self.items = result
            self.listing.clear()
            for item in result:
                self.listing.addItem(f"{item['created_at']} — {item['size']} bytes")
            self.status.setText("فهرست دریافت شد.")
        else:
            if name == "restore":
                self.restored = True
            self.status.setText("عملیات با موفقیت انجام شد.")
    def reject(self):
        if "restore" in self.jobs.busy:
            self.status.setText("تا پایان بازیابی منتظر بمانید.")
            return
        super().reject()
    def accept(self):
        if "restore" in self.jobs.busy:
            self.status.setText("تا پایان بازیابی منتظر بمانید.")
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
        menu = window.menuBar().addMenu("اشتراک و پشتیبان")
        menu.addAction("اعتبار اشتراک و بکاپ", self.account)
        menu.addAction("حساب مدرسه در سایت", lambda: webbrowser.open(client.config.url + "/account/"))
        menu.addAction("خروج از حساب مدرسه", lambda: self.jobs.start("logout", client.logout))
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
    def update_label(self):
        try:
            lease = self.client.require_lease()
        except AccessDenied:
            self.license_label.setText("اشتراک نامعتبر")
            return
        expiry = datetime.fromtimestamp(lease["subscription_expires_at"]).strftime("%Y-%m-%d")
        self.license_label.setText(f"{lease['school_name']} | اعتبار تا {expiry} | ظرفیت {lease['student_limit']}")
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
                QMessageBox.warning(None, "اشتراک", "اعتبار دسترسی پایان یافته یا بررسی آنلاین ساعت لازم است. اطلاعات حذف نشده است. در اجرای بعد از ورود آنلاین برای تمدید یا خروجی / بازیابی محلی استفاده کنید.")
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
                QMessageBox.warning(self.window, "خروج محلی", "ورود ذخیره‌شدهٔ این دستگاه پاک شد، اما لغو روی سرور تأیید نشد. برای لغو قطعی دستگاه از حساب سایت استفاده کنید.")
            self.app.quit()
        if name == "backup":
            self.window.statusBar().showMessage("بکاپ آنلاین ارسال شد." if not error else "ارسال بکاپ انجام نشد؛ از منوی اشتراک بررسی کنید.", 15000)


def run_application(app, window_class, init_db, ensure_local_admin, database_path, base_dir, open_reports=False):
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(database_path.parent / ".application.lock"))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        QMessageBox.warning(None, "برنامه در حال اجراست", "ابتدا نسخهٔ دیگر برنامه را ببندید.")
        return 1
    client, window = None, None
    try:
        try:
            config = ClientConfig.load(Path(base_dir) / "subscription-client.json")
            client = CloudClient(config, database_path.parent / "cloud-state.json")
        except Exception:
            QMessageBox.critical(None, "تنظیم سرویس", "فایل معتبر subscription-client.json کنار برنامه نصب نشده است. بستهٔ کامل را از پشتیبانی دریافت کنید.")
            return 1
        from .recovery import recover_interrupted_restore
        recover_interrupted_restore(database_path)
        init_db()
        if not ensure_local_admin():
            return 0
        recovered = []
        def local_recovery():
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
            if not bound and QMessageBox.question(None, "اتصال دیتابیس به مدرسه", f"دیتابیس این دستگاه به مدرسهٔ «{client.require_lease()['school_name']}» متصل شود؟", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return 0
            client.bind_database(database_path)
            bind_gate(client)
        except Exception as exc:
            QMessageBox.critical(None, "اتصال مدرسه", str(exc))
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
        self.setWindowTitle('فهرست فعال / آرشیو ظرفیت')
        layout = QVBoxLayout(self)
        note = QLabel('آرشیو فقط طبقه‌بندی فهرست ظرفیت است؛ سوابق و چهره حذف نمی‌شوند و منطق تشخیص و حضور و غیاب تغییر نمی‌کند. این گزینه ابزار قطع تشخیص فرد نیست.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.listing = QListWidget()
        layout.addWidget(self.listing)
        button = QPushButton('تغییر وضعیت دانش‌آموز انتخاب‌شده')
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
            self.listing.addItem(f'{code} — {name} — ' + ('آرشیو' if archived else 'فعال'))
    def toggle(self):
        row = self.listing.currentRow()
        if not 0 <= row < len(self.rows):
            return
        if QMessageBox.question(self, 'تغییر فهرست ظرفیت', 'وضعیت انتخاب‌شده تغییر کند؟ سوابق حفظ می‌شوند.', QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            # Use the same module/path as the running application, not a second DB.
            import sys
            db = sys.modules.get('database') or sys.modules.get('src.database')
            db.set_student_capacity_archived(self.rows[row][0], not self.rows[row][3])
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, 'ظرفیت', str(exc))


class RecoveryDialog(QDialog):
    """Offline data ownership path, behind the existing local-admin password."""
    def __init__(self, database_path, parent=None):
        super().__init__(parent)
        self.database_path = Path(database_path)
        self.restored = False
        self.setWindowTitle('خروجی و بازیابی محلی — بدون نیاز به اشتراک فعال')
        layout = QVBoxLayout(self)
        note = QLabel('دوربین‌ها در این حالت اجرا نمی‌شوند. خروجی CSV سوابق، یا بکاپ کامل رمزگذاری‌شده با کلید مدرسه بگیرید. بازیابی فقط از بکاپ همین مدرسه و با کلید درست انجام می‌شود. فایل‌های خروجی حاوی اطلاعات محرمانه‌اند؛ محل امن انتخاب کنید.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel('')
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        for title, operation in [('خروجی CSV حضور و غیاب', self.export_csv),
                                 ('خروجی کامل رمزگذاری‌شده', lambda: self.bundle(False)),
                                 ('بازیابی بکاپ کامل محلی', lambda: self.bundle(True))]:
            button = QPushButton(title)
            button.clicked.connect(operation)
            layout.addWidget(button)
        layout.addWidget(self.status)
        self.jobs = Jobs(self)
        self.jobs.completed.connect(self.done_job, Qt.QueuedConnection)
        self.finished.connect(lambda _: self.jobs.close())
    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, 'خروجی محرمانهٔ حضور و غیاب', '', 'CSV (*.csv)')
        if path:
            from .local_export import export_attendance
            self.jobs.start('csv', lambda: export_attendance(self.database_path, path))
    def bundle(self, restoring):
        from .local_export import bound_school
        from .recovery import import_key, make_bundle, unpack_bundle, install_bundle
        import tempfile
        try:
            school = bound_school(self.database_path)
            keypath, _ = QFileDialog.getOpenFileName(self, 'کلید بازیابی همین مدرسه', '', 'Recovery key (*.frkey)')
            if not keypath:
                return
            key = import_key(keypath, school)
            if restoring:
                path, _ = QFileDialog.getOpenFileName(self, 'بکاپ کامل', '', 'Backup (*.frbackup)')
            else:
                path, _ = QFileDialog.getSaveFileName(self, 'بکاپ کامل رمزگذاری‌شده', '', 'Backup (*.frbackup)')
            if not path:
                return
            if restoring and QMessageBox.warning(self, 'جایگزینی اطلاعات محلی', 'دیتابیس و تصاویر رویدادها جایگزین شوند؟ نسخهٔ قبل نگه داشته می‌شود.', QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
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
            self.status.setText(str(exc))
    def done_job(self, name, result, error):
        self.jobs.finished(name)
        self.setEnabled(True)
        if name == 'restore' and not error:
            self.restored = True
        self.status.setText(str(error) if error else 'عملیات انجام شد؛ برای ورود عادی برنامه را دوباره اجرا کنید.')
    def reject(self):
        if self.jobs.busy:
            return
        super().reject()
