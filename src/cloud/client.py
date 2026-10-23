"""Desktop account client. No cached/offline admission at process startup.

A fresh server challenge is mandatory. Running sessions use a monotonic deadline
so changing the desktop clock cannot extend their signed lease.
"""
import base64
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import time
from urllib.parse import urlparse
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import requests

MAX_BACKUP_BYTES = 128 * 1024 * 1024


class CloudError(Exception):
    pass


class AccessDenied(CloudError):
    pass


@dataclass(frozen=True)
class ClientConfig:
    url: str
    public_key: bytes
    @classmethod
    def load(cls, path):
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        url = value["service_url"].rstrip("/")
        parts = urlparse(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
            raise CloudError("نشانی سرویس باید HTTPS باشد.")
        key = base64.b64decode(value["public_key"], validate=True)
        Ed25519PublicKey.from_public_bytes(key)
        return cls(url, key)


class CredentialStore:
    """Only OS credential vaults; no plaintext file fallback."""
    def __init__(self, service):
        self.service = service
        self.backend = None
        try:
            import keyring
            backend = keyring.get_keyring()
            choices = getattr(backend, "backends", [backend])
            for candidate in choices:
                if type(candidate).__module__.startswith(("keyring.backends.Windows", "keyring.backends.macOS", "keyring.backends.SecretService", "keyring.backends.libsecret", "keyring.backends.kwallet")):
                    self.backend = candidate
                    break
        except Exception:
            pass
    def get(self, installation):
        try:
            return self.backend.get_password(self.service, installation) if self.backend else None
        except Exception:
            return None
    def set(self, installation, credential):
        try:
            if self.backend is None:
                return False
            self.backend.set_password(self.service, installation, credential)
            return True
        except Exception:
            return False
    def delete(self, installation):
        try:
            if self.backend:
                self.backend.delete_password(self.service, installation)
        except Exception:
            pass


class CloudClient:
    def __init__(self, config, state_path, store=None, transport=None):
        self.config = config
        self.state_path = Path(state_path)
        self._lock = threading.RLock()
        self._backup_lock = threading.Lock()
        self._state_io_lock = threading.Lock()
        self._closed = False
        self.state = {}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.installation = str(uuid.UUID(self.state.setdefault("installation_id", str(uuid.uuid4()))))
        self.store = store or CredentialStore("FaceRecognition:" + config.url)
        self.credential = self.store.get(self.installation)
        self.remembered = bool(self.credential)
        self._lease = None
        self._deadline = 0
        self._database_path = None
        self.transport = transport or requests.request
        self.save_state()

    def save_state(self):
        # Disk/fsync work must not hold the entitlement lock used by camera loops.
        with self._state_io_lock:
            with self._lock:
                state = dict(self.state)
            self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self.state_path.parent, prefix=".cloud-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump(state, output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.state_path)
            finally:
                Path(temporary).unlink(missing_ok=True)

    def _request(self, method, path, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        if self.credential and path != "/api/v1/login/":
            headers["Authorization"] = "Bearer " + self.credential
        try:
            response = self.transport(method, self.config.url + path, headers=headers,
                                      timeout=(5, 30), allow_redirects=False, stream=True, **kwargs)
        except requests.RequestException as exc:
            raise CloudError("ارتباط امن با سرویس برقرار نشد؛ اینترنت را بررسی کنید.") from exc
        if response.status_code in {401, 403}:
            code = None
            try:
                raw = bytearray()
                for chunk in response.iter_content(1024):
                    raw.extend(chunk)
                    if len(raw) > 8192:
                        break
                error_data = json.loads(raw) if len(raw) <= 8192 else None
                if isinstance(error_data, dict):
                    code = error_data.get("error")
            except (ValueError, requests.RequestException):
                pass
            response.close()
            if code == "backups_disabled":
                raise CloudError("بکاپ توسط مدیر سرویس غیرفعال شده است.")
            with self._lock:
                self._lease, self._deadline = None, 0
            messages = {
                "invalid_credentials": "ایمیل یا رمز درست نیست، یا ایمیل هنوز تأیید نشده است.",
                "subscription_inactive": "اشتراک مدرسه فعال نیست یا پایان یافته است؛ حساب سایت را بررسی کنید.",
                "device_limit_reached": "ظرفیت دستگاه‌ها تکمیل شده است؛ دستگاه قبلی را از سایت لغو کنید یا با پشتیبانی تماس بگیرید.",
                "device_revoked": "دسترسی این دستگاه لغو شده است؛ با پشتیبانی تماس بگیرید.",
                "invalid_device_credential": "ورود ذخیره‌شده معتبر نیست؛ دوباره با ایمیل و رمز وارد شوید.",
            }
            raise AccessDenied(messages.get(code, "ورود یا اشتراک معتبر نیست، یا دسترسی دستگاه لغو شده است."))
        if not 200 <= response.status_code < 300:
            status = response.status_code
            response.close()
            raise CloudError(f"سرویس درخواست را نپذیرفت (کد {status}).")
        return response

    def _json(self, method, path, **kwargs):
        with self._request(method, path, **kwargs) as response:
            raw = bytearray()
            for chunk in response.iter_content(8192):
                raw.extend(chunk)
                if len(raw) > 65536:
                    raise CloudError("پاسخ سرویس بیش از حد مجاز است.")
            try:
                return json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise CloudError("پاسخ سرویس معتبر نیست.") from exc

    def accept_lease(self, envelope, nonce, started):
        try:
            raw = base64.b64decode(envelope["payload"], validate=True)
            signature = base64.b64decode(envelope["signature"], validate=True)
            Ed25519PublicKey.from_public_bytes(self.config.public_key).verify(signature, raw)
            lease = json.loads(raw)
            if (lease["version"] != 1 or lease["audience"] != "face-recognition-desktop"
                    or lease["nonce"] != nonce or lease["device_id"] != self.installation):
                raise ValueError()
            uuid.UUID(lease["school_id"])
            if type(lease["student_limit"]) is not int or not 1 <= lease["student_limit"] <= 1000000:
                raise ValueError()
            issued, expires, subscription = (float(lease[k]) for k in ("issued_at", "lease_expires_at", "subscription_expires_at"))
            if not all(math.isfinite(v) for v in (issued, expires, subscription)) or not 0 < expires - issued <= 900.001 or expires > subscription:
                raise ValueError()
            deadline = started + expires - issued
            if deadline <= time.monotonic():
                raise ValueError()
            previous = self.state.get("school_id")
            if previous is not None and previous != lease["school_id"]:
                raise AccessDenied("این نصب به مدرسهٔ دیگری متصل است؛ برای انتقال با پشتیبانی تماس بگیرید.")
        except AccessDenied:
            raise
        except Exception as exc:
            raise AccessDenied("امضا یا اعتبار مجوز سرویس تأیید نشد.") from exc
        if self._database_path is not None:
            with closing(sqlite3.connect(f"{self._database_path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
                count = connection.execute("SELECT COUNT(*) FROM students").fetchone()[0]
            if count > lease["student_limit"]:
                with self._lock:
                    self._lease, self._deadline = None, 0
                raise AccessDenied("ظرفیت اشتراک جدید از تعداد رکوردهای مدرسه کمتر است؛ با پشتیبانی تماس بگیرید.")
        with self._lock:
            if self._closed:
                raise AccessDenied("نشست برنامه بسته شده است.")
            self._lease, self._deadline = lease, deadline
            self.state["school_id"] = lease["school_id"]
        self.save_state()
        return lease

    def login(self, email, password):
        nonce, started = secrets.token_urlsafe(32), time.monotonic()
        result = self._json("POST", "/api/v1/login/", json={"email": email, "password": password,
            "device_id": self.installation, "label": "School desktop", "nonce": nonce})
        lease = self.accept_lease(result["lease"], nonce, started)
        self.credential = result["credential"]
        self.remembered = self.store.set(self.installation, self.credential)
        return lease

    def renew(self):
        if not self.credential:
            raise AccessDenied("ابتدا وارد حساب مدرسه شوید.")
        nonce, started = secrets.token_urlsafe(32), time.monotonic()
        result = self._json("POST", "/api/v1/lease/", json={"nonce": nonce})
        return self.accept_lease(result["lease"], nonce, started)

    def close(self):
        # A late network reply must never reactivate a session during shutdown.
        with self._lock:
            self._closed = True
            self._lease, self._deadline = None, 0

    def allowed(self):
        with self._lock:
            return not self._closed and self._lease is not None and time.monotonic() < self._deadline

    def require_lease(self):
        with self._lock:
            if not self.allowed():
                raise AccessDenied("اشتراک یا مهلت اتصال معتبر نیست؛ دوباره وارد حساب شوید.")
            return dict(self._lease)

    def logout(self):
        try:
            if self.credential:
                self._json("POST", "/api/v1/logout/", json={})
        finally:
            self.store.delete(self.installation)
            with self._lock:
                self.credential, self._lease, self._deadline = None, None, 0

    def bind_database(self, path):
        lease = self.require_lease()
        with closing(sqlite3.connect(path)) as connection, connection:
            check_database(connection, lease)
            row = connection.execute("SELECT value FROM app_settings WHERE key='cloud_school_id'").fetchone()
            if row and row[0] != lease["school_id"]:
                raise AccessDenied("دیتابیس متعلق به مدرسهٔ دیگری است.")
            connection.execute("INSERT INTO app_settings(key,value) VALUES('cloud_school_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (lease["school_id"],))
        self._database_path = Path(path)

    def backup(self, database_path):
        if not self._backup_lock.acquire(blocking=False):
            raise CloudError("یک بکاپ دیگر در حال ارسال است.")
        try:
            return self._backup(database_path)
        finally:
            self._backup_lock.release()

    def _backup(self, database_path):
        lease = self.require_lease()
        if not lease.get("backups_enabled"):
            raise CloudError("بکاپ برای این اشتراک غیرفعال است.")
        with tempfile.TemporaryDirectory(prefix="face-backup-") as directory:
            snapshot = Path(directory) / "snapshot.sqlite3"
            source = sqlite3.connect(f"{Path(database_path).resolve().as_uri()}?mode=ro", uri=True)
            destination = sqlite3.connect(snapshot)
            try:
                source.backup(destination, pages=128, sleep=.01)
                check_database(destination, lease, require_binding=True)
            finally:
                source.close()
                destination.close()
            if snapshot.stat().st_size > MAX_BACKUP_BYTES:
                raise CloudError("حجم بکاپ بیشتر از ۱۲۸ مگابایت است؛ با پشتیبانی تماس بگیرید.")
            digest = file_digest(snapshot)
            with snapshot.open("rb") as content:
                result = self._json("POST", "/api/v1/backups/upload/", data=content,
                    headers={"Content-Type": "application/octet-stream", "X-Backup-SHA256": digest})
            with self._lock:
                self.state["last_backup_at"] = time.time()
            self.save_state()
            return result

    def backups(self):
        return self._json("GET", "/api/v1/backups/")["backups"]

    def restore(self, backup_id, database_path):
        """Caller must own the app instance lock, with no live DB users/cameras."""
        lease = self.require_lease()
        backup_id = str(uuid.UUID(str(backup_id)))
        target = Path(database_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".restore-")
        temporary = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as output, self._request("GET", f"/api/v1/backups/{backup_id}/") as response:
                digest, size = hashlib.sha256(), 0
                expected = response.headers.get("X-Backup-SHA256")
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > MAX_BACKUP_BYTES:
                        raise CloudError("فایل بازیابی بیش از حد بزرگ است.")
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest() != expected:
                raise CloudError("صحت فایل بازیابی تأیید نشد؛ دیتابیس فعلی حفظ شد.")
            with closing(sqlite3.connect(f"{temporary.resolve().as_uri()}?mode=ro", uri=True)) as connection:
                check_database(connection, lease, require_binding=True)
            with self._lock:
                self.require_lease()
                if target.exists():
                    # Preserve the whole prior database, including any WAL, using SQLite's snapshot API.
                    rollback = target.with_name(f"before-restore-{time.time_ns()}.sqlite3")
                    with closing(sqlite3.connect(target)) as source, closing(sqlite3.connect(rollback)) as destination:
                        source.backup(destination)
                        if source.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
                            raise CloudError("دیتابیس در حال استفاده است؛ بازیابی متوقف شد.")
                    # All users are stopped and the process lock is held by the caller.
                    for suffix in ("-wal", "-shm"):
                        Path(str(target) + suffix).unlink(missing_ok=True)
                os.replace(temporary, target)
            return str(target)
        finally:
            temporary.unlink(missing_ok=True)


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as content:
        for chunk in iter(lambda: content.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_database(connection, lease, require_binding=False):
    connection.execute("PRAGMA query_only=ON")
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"students", "attendance", "events", "app_settings", "face_embeddings"} <= tables:
            raise CloudError("ساختار دیتابیس پشتیبان سازگار نیست.")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise CloudError("دیتابیس پشتیبان سالم نیست.")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise CloudError("ارتباط رکوردهای دیتابیس معتبر نیست.")
        if connection.execute("SELECT COUNT(*) FROM students").fetchone()[0] > lease["student_limit"]:
            raise CloudError("تعداد دانش‌آموزان دیتابیس از ظرفیت اشتراک بیشتر است.")
        row = connection.execute("SELECT value FROM app_settings WHERE key='cloud_school_id'").fetchone()
        if row and row[0] != lease["school_id"] or require_binding and not row:
            raise CloudError("فایل پشتیبان به این مدرسه تعلق ندارد.")
    finally:
        connection.execute("PRAGMA query_only=OFF")
