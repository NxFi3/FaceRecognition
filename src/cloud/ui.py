"""Qt account UI and asynchronous control plane; no vision model scheduling."""
from datetime import datetime
from pathlib import Path
import threading
import time
import webbrowser

from filelock import FileLock, Timeout
from PySide6.QtCore import QObject, Signal, QTimer, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit, QLabel,
    QPushButton, QMessageBox, QCheckBox, QListWidget, QDialogButtonBox)
from .client import AccessDenied, ClientConfig, CloudClient
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
    def __init__(self, client, parent=None):
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
        if not client.remembered:
            layout.addWidget(QLabel("ذخیره‌سازی امن سیستم‌عامل در دسترس نیست؛ ورود در اجرای بعدی دوباره لازم است."))
        notice = QLabel("بکاپ شامل تمام دیتابیس، اطلاعات دانش‌آموزان، حضور و غیاب و داده‌های ثبت‌شدهٔ چهره است. روی سرور رمزگذاری می‌شود. تصاویر خارج از دیتابیس در این بکاپ نیستند.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.auto = QCheckBox("با ارسال خودکار روزانهٔ دیتابیس به سرور موافقم")
        self.auto.setChecked(bool(client.state.get("automatic_backups", False)))
        self.auto.setEnabled(bool(lease.get("backups_enabled")))
        self.auto.toggled.connect(self.auto_changed)
        layout.addWidget(self.auto)
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
        self.renew_timer.start()
        self.finished.connect(lambda _: self.renew_timer.stop())
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
        self.next_renewal = time.monotonic() + 300
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
                QMessageBox.warning(None, "اشتراک", "اعتبار دسترسی پایان یافته است. اینترنت و اشتراک مدرسه را بررسی کنید و دوباره وارد شوید.")
                self.app.quit()
            return
        now = time.monotonic()
        if now >= self.next_renewal:
            self.next_renewal = now + 60  # bounded retry after transient failure
            self.jobs.start("renew", self.client.renew)
        if lease.get("backups_enabled") and self.client.state.get("automatic_backups") and time.time() - self.client.state.get("last_backup_at", 0) >= 86400 and now >= self.next_backup_attempt:
            self.next_backup_attempt = now + 3600
            self.jobs.start("backup", lambda: self.client.backup(self.database_path))
    def done_job(self, name, result, error):
        self.jobs.finished(name)
        if name == "renew" and not error:
            self.next_renewal = time.monotonic() + 300
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
        if LoginDialog(client).exec() != QDialog.Accepted:
            return 0
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
