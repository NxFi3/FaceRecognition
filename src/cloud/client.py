"""Offline-first signed entitlement; network operations are called by background jobs."""
import base64
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
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
from .offline import Checkpoint, verify, boot_id, CLOCK_TOLERANCE, envelope_hash

MAX_BACKUP_BYTES = 128 * 1024 * 1024


def license_clock():
    # Linux CLOCK_MONOTONIC stops during suspend; CLOCK_BOOTTIME includes it.
    # Windows monotonic/QPC includes sleep. Wall-clock changes remain irrelevant.
    clock_id = getattr(time, "CLOCK_BOOTTIME", None)
    if clock_id is not None:
        return time.clock_gettime(clock_id)
    return time.monotonic()


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
        self._auth_network_lock = threading.Lock()
        self._state_io_lock = threading.Lock()
        self._closed = False
        self._closing = False
        self._session_epoch = 0
        self.store = store or CredentialStore("FaceRecognition:" + config.url)
        self.state = {}
        damaged = False
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text(encoding='utf-8'))
                if not isinstance(self.state, dict):
                    raise ValueError('Invalid metadata')
            except (ValueError, UnicodeError):
                damaged = True
                self.state = {}
        self._anchor_key = 'installation-id:' + hashlib.sha256(os.path.normcase(str(self.state_path.resolve())).encode()).hexdigest()
        protected_id = self.store.get(self._anchor_key)
        candidate = protected_id or self.state.get('installation_id') or str(uuid.uuid4())
        try:
            self.installation = str(uuid.UUID(candidate))
        except (ValueError, TypeError):
            self.installation, damaged = str(uuid.uuid4()), True
        changed_id = bool(protected_id and self.state.get('installation_id', protected_id) != protected_id)
        self.state['installation_id'] = self.installation
        self.credential = self.store.get(self.installation)
        self.remembered = bool(self.credential)
        self._lease = None
        self._deadline = 0
        self._database_path = None
        self.transport = transport or requests.request
        self._checkpoint = Checkpoint(self.store, self.installation)
        self._generation = 0
        self._anchor_time, self._anchor_tick = time.time(), license_clock()
        self._wall_anchor = time.time()
        self._suspicious = False
        self._envelope = None
        self._mirror_pending = False
        self._load_offline()
        if damaged or changed_id:
            self._suspicious = True
        self.save_state()
        if self._lease is not None and not self._suspicious:
            try:
                self._checkpoint.finish()
            except OSError:
                pass  # Still safely use the authoritative protected generation.

    def _load_offline(self):
        try:
            if self.state.get('signed_out'):
                raise ValueError('Local session was explicitly signed out')
            saved = self._checkpoint.load()
            if not saved:
                return  # Legacy online-only clients activate once with the new server.
            value = verify(saved['envelope'], self.config.public_key, self.installation)
            previous_school = self.state.get('school_id')
            self.state['school_id'] = value['school_id']
            if previous_school is not None and previous_school != value['school_id']:
                raise ValueError('School metadata tampering')
            if saved.get('suspicious'):
                raise ValueError('Online clock verification required')
            self._generation = int(saved['generation'])
            # Vault checkpoint is authoritative, including after an interrupted JSON mirror write.
            if int(self.state.get('license_generation', self._generation)) > self._generation:
                raise ValueError('Protected state rollback')
            mirror = self.state.get('signed_license')
            if mirror is not None and mirror != saved['envelope']:
                previous = saved.get('previous')
                if not previous or previous['hash'] != envelope_hash(mirror) or previous['generation'] != self.state.get('license_generation'):
                    raise ValueError('License file rollback/tampering')
                # Crash between vault commit and mirror replacement: keep the NEW
                # protected license/time, never roll back to the old public file.
            elapsed = max(0, license_clock() - saved['tick']) if saved['boot'] and saved['boot'] == boot_id() else 0
            observed_wall = float(saved['wall'])
            floor = float(saved['time']) + max(elapsed, time.time() - observed_wall)
            if time.time() < observed_wall + elapsed - CLOCK_TOLERANCE:
                raise ValueError('Clock rollback')
            self._anchor_time = floor
            self._wall_anchor = time.time()
            self._anchor_tick = license_clock()
            self._lease, self._envelope = value, saved['envelope']
            self.state.update(school_id=value['school_id'], signed_license=saved['envelope'], license_generation=self._generation)
            self._deadline = self._anchor_tick + value['subscription_expires_at'] - self._anchor_time
        except Exception:
            self._suspicious = True
            self._lease = None

    def checkpoint_time(self):
        # Called by the background control plane, never the frame or GUI thread.
        with self._state_io_lock:
            with self._lock:
                if self._closed or self._envelope is None or self._mirror_pending:
                    return
                envelope = self._envelope
                current = self._anchor_time + max(0, license_clock() - self._anchor_tick, time.time() - self._wall_anchor)
                generation = self._generation + 1
            self._checkpoint.save(envelope, current, license_clock(), generation, suspicious=self._suspicious)
            with self._lock:
                self._generation = generation
                self.state['license_generation'] = generation
        self.save_state()

    def save_state(self):
        # Disk/fsync work must not hold the entitlement lock used by camera loops.
        with self._state_io_lock:
            with self._lock:
                if self._closed:
                    return
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
            headers.setdefault("Authorization", "Bearer " + self.credential)
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

    def _auth_epoch(self):
        with self._lock:
            if self._closed or self._closing:
                raise AccessDenied("نشست برنامه بسته شده است.")
            return self._session_epoch

    def _check_auth_epoch(self, epoch):
        if self._closed or self._closing or (epoch is not None and epoch != self._session_epoch):
            raise AccessDenied("نشست ورود تغییر کرده است؛ دوباره وارد شوید.")

    def accept_lease(self, envelope, nonce, started, session_epoch=None):
        try:
            value = verify(envelope, self.config.public_key, self.installation,
                           self.state.get('school_id'), nonce)
            elapsed = license_clock() - started
            if not 0 <= elapsed <= 120:
                raise ValueError('Stale synchronization response')
        except Exception as exc:
            raise AccessDenied("امضا یا اعتبار مجوز سرویس تأیید نشد.") from exc
        with self._state_io_lock:
            with self._lock:
                self._check_auth_epoch(session_epoch)
                generation = self._generation + 1
                previous = {'hash': envelope_hash(self.state.get('signed_license')), 'generation': self.state.get('license_generation')}
                self._mirror_pending = True
            # A verified denial must stop the current session even if persistence fails.
            if value['status'] != 'active':
                with self._lock:
                    self._lease, self._envelope, self._deadline = value, envelope, 0
                    self.state.update(school_id=value['school_id'], signed_license=envelope,
                                      license_generation=generation)
            # Persist before granting access. No plaintext fallback for protected state.
            trusted = value['issued_at'] + elapsed
            try:
                if not self.store.set(self._anchor_key, self.installation):
                    raise OSError('ذخیره‌سازی امن شناسهٔ نصب انجام نشد.')
                self._checkpoint.save(envelope, trusted, license_clock(), generation, previous=previous)
            except OSError as exc:
                if value['status'] != 'active':
                    from .recovery import atomic_json
                    try:
                        atomic_json(self.state_path, dict(self.state))
                    except OSError:
                        pass  # Current session stays denied; unavailable storage cannot persist.
                    raise AccessDenied('دسترسی لغو شده است؛ ذخیرهٔ وضعیت امن هم در دسترس نیست.') from exc
                self._mirror_pending = False
                raise CloudError(str(exc)) from exc
            with self._lock:
                self._check_auth_epoch(session_epoch)
                self._generation = generation
                self._anchor_time, self._anchor_tick = trusted, license_clock()
                self._wall_anchor = time.time()
                self._deadline = self._anchor_tick + value['subscription_expires_at'] - trusted
                self._suspicious = False
                self._lease, self._envelope = value, envelope
                self.state.update(school_id=value['school_id'], signed_license=envelope, license_generation=generation)
                self.state.pop('signed_out', None)
        self.save_state()
        with self._state_io_lock:
            if self._generation == generation:
                self._checkpoint.finish()
                self._mirror_pending = False
        with self._lock:
            self._check_auth_epoch(session_epoch)
        if value['status'] != 'active':
            raise AccessDenied("دسترسی مدرسه یا این نصب توسط سرویس لغو شده است.")
        return value

    def login(self, email, password):
        epoch = self._auth_epoch()
        # A controller sync and an account dialog can coexist. Serialize their
        # exchanges so an older response/token cannot overwrite newer revocation
        # or renewal evidence. Callers run these operations in background jobs.
        with self._auth_network_lock:
            with self._lock:
                self._check_auth_epoch(epoch)
            nonce, started = secrets.token_urlsafe(32), license_clock()
            result = self._json("POST", "/api/v1/login/", json={"email": email, "password": password,
                "device_id": self.installation, "label": "School desktop", "nonce": nonce, "license_version": 2})
            lease = self.accept_lease(result["lease"], nonce, started, session_epoch=epoch)
            with self._state_io_lock:
                with self._lock:
                    self._check_auth_epoch(epoch)
                    credential = result["credential"]
                remembered = self.store.set(self.installation, credential)
                with self._lock:
                    self._check_auth_epoch(epoch)
                    self.credential, self.remembered = credential, remembered
            return lease

    def renew(self):
        epoch = self._auth_epoch()
        with self._auth_network_lock:
            with self._lock:
                self._check_auth_epoch(epoch)
                if not self.credential:
                    raise AccessDenied("ابتدا وارد حساب مدرسه شوید.")
            nonce, started = secrets.token_urlsafe(32), license_clock()
            result = self._json("POST", "/api/v1/lease/", json={"nonce": nonce, "license_version": 2})
            return self.accept_lease(result["lease"], nonce, started, session_epoch=epoch)

    def close(self):
        with self._lock:
            if self._closed or self._closing:
                return
            self._closing = True
            self._session_epoch += 1
        try:
            self.checkpoint_time()
        except OSError:
            pass  # Keep the preceding durable checkpoint if disk/vault is unavailable.
        # A late network reply must never reactivate a session during shutdown.
        with self._lock:
            self._closed = True
            self._lease, self._envelope, self._deadline = None, None, 0

    def allowed(self):
        with self._lock:
            if time.time() < self._wall_anchor + max(0, license_clock() - self._anchor_tick) - CLOCK_TOLERANCE:
                self._suspicious = True
            return (not self._closed and not self._closing and not self._suspicious and self._lease is not None
                    and self._lease.get('status') == 'active' and license_clock() < self._deadline
                    and self._anchor_time + max(0, license_clock() - self._anchor_tick, time.time() - self._wall_anchor) < self._lease['subscription_expires_at'])

    def require_lease(self):
        with self._lock:
            if not self.allowed():
                raise AccessDenied("اشتراک پایان یافته یا بررسی آنلاینِ اعتبار و ساعت لازم است؛ اطلاعات مدرسه حفظ شده است.")
            return dict(self._lease)

    def logout(self):
        # Invalidate local admission before a slow/offline HTTP request. Serialize
        # with checkpoint writes so shutdown cannot resurrect the old envelope.
        with self._state_io_lock:
            with self._lock:
                credential = self.credential
                self._session_epoch += 1
                self.credential, self._lease, self._envelope = None, None, None
                self._deadline = 0
                self._mirror_pending = False
                self.remembered = False
                self.state.pop('signed_license', None)
                self.state.pop('license_generation', None)
                self.state['signed_out'] = True
            self.store.delete(self.installation)
            self.store.delete(self._checkpoint.key)
        self.save_state()
        if credential:
            self._json("POST", "/api/v1/logout/", json={},
                       headers={"Authorization": "Bearer " + credential})

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
        from .recovery import make_bundle
        key = self.backup_key()
        database_path = Path(database_path)
        with tempfile.TemporaryDirectory(prefix='.face-backup-', dir=database_path.parent) as directory:
            snapshot = Path(directory) / 'school.frbackup'
            size = make_bundle(database_path, snapshot, lease['school_id'], key)
            if size > MAX_BACKUP_BYTES:
                raise CloudError(f"حجم بکاپ رمزگذاری‌شده {size} بایت است و از سقف ۱۲۸ مگابایت بیشتر است؛ خروجی کامل محلی بگیرید و با پشتیبانی تماس بگیرید.")
            digest = file_digest(snapshot)
            with snapshot.open('rb') as content:
                result = self._json('POST', '/api/v1/backups/upload/', data=content,
                    headers={'Content-Type': 'application/octet-stream', 'X-Backup-SHA256': digest,
                             'X-Backup-Format': 'client-v2'})
            with self._lock:
                self.state['last_backup_at'] = time.time()
            self.save_state()
            return result

    def backup_key(self):
        school = self.state.get('school_id')
        raw = self.store.get('backup-key:' + str(school))
        if not raw:
            raise CloudError('ابتدا کلید بازیابی مدرسه را در یک محل امنِ جداگانه ذخیره یا وارد کنید.')
        key = base64.b64decode(raw, validate=True)
        if len(key) != 32:
            raise CloudError('کلید بازیابی معتبر نیست.')
        return key

    def configure_backup_key(self, path, importing=False):
        from .recovery import export_key, import_key
        school = self.state['school_id']
        if importing:
            key = import_key(path, school)
            existing = self.store.get('backup-key:' + school)
            if existing and base64.b64decode(existing, validate=True) != key:
                raise CloudError('کلید واردشده با کلید فعلی متفاوت است؛ برای جلوگیری از از دست رفتن بازیابی، جایگزین نشد.')
        else:
            # Re-export the SAME key. Never silently rotate away from older backups.
            raw = self.store.get('backup-key:' + school)
            key = base64.b64decode(raw, validate=True) if raw else os.urandom(32)
            export_key(path, school, key)
        if not self.store.set('backup-key:' + school, base64.b64encode(key).decode()):
            raise CloudError('ذخیرهٔ امن کلید بازیابی انجام نشد؛ فایل کلید را نگه دارید.')
        return True

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
            from .recovery import MAGIC, unpack_bundle, install_bundle
            with temporary.open('rb') as source:
                encrypted = source.read(len(MAGIC)) == MAGIC
            if encrypted:
                with tempfile.TemporaryDirectory(prefix='.restore-private-', dir=target.parent) as directory:
                    unpack_bundle(temporary, directory, lease['school_id'], self.backup_key())
                    self.require_lease()
                    install_bundle(directory, target)
                return str(target)
            # Explicit legacy compatibility: old backups contained SQLite only.
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
        row = connection.execute("SELECT value FROM app_settings WHERE key='cloud_school_id'").fetchone()
        if row and row[0] != lease["school_id"] or require_binding and not row:
            raise CloudError("فایل پشتیبان به این مدرسه تعلق ندارد.")
    finally:
        connection.execute("PRAGMA query_only=OFF")
