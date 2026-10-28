import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import requests

from src.cloud.client import AccessDenied, ClientConfig, CloudClient, CloudError, license_clock
from src.cloud.entitlement import bind_gate, runtime_allowed
from src import database


class MemoryVault:
    def __init__(self):
        self.values = {}
    @property
    def token(self):
        return next((v for k, v in self.values.items() if ':' not in k and k != 'installation-id'), None)
    def get(self, installation):
        return self.values.get(installation)
    def set(self, installation, credential):
        self.values[installation] = credential
        return True
    def delete(self, installation):
        self.values.pop(installation, None)


class Response:
    def __init__(self, content=None, status=200, raw=None, headers=None):
        self.status_code = status
        self.raw = json.dumps(content).encode() if raw is None else raw
        self.headers = headers or {}
    def iter_content(self, size):
        for start in range(0, len(self.raw), size):
            yield self.raw[start:start + size]
    def close(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


class CloudClientTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.signer = Ed25519PrivateKey.generate()
        key = self.signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.config = ClientConfig("https://schools.example.com", key)
        self.school_id = str(uuid.uuid4())
        self.vault = MemoryVault()
        self.client = CloudClient(self.config, self.root / "cloud-state.json", store=self.vault, transport=self.transport)
        self.addCleanup(lambda: bind_gate(None))
        self.database_patch = patch.object(database, "DB_PATH", self.root / "attendance.db")
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        database.init_db()
    def envelope(self, base_nonce, **changes):
        lease = {"version": 2, "license_id": str(uuid.uuid4()), "status": "active", "device_limit": 1,
            "subscription_starts_at": time.time() - 86400, "audience": "face-recognition-desktop", "school_id": self.school_id,
            "school_name": "Test school", "device_id": self.client.installation, "nonce": base_nonce,
            "issued_at": time.time(), "lease_expires_at": time.time() + 600,
            "subscription_expires_at": time.time() + 86400, "student_limit": 900, "backups_enabled": True}
        lease.update(changes)
        data = json.dumps(lease).encode()
        return {"payload": base64.b64encode(data).decode(), "signature": base64.b64encode(self.signer.sign(data)).decode()}
    def transport(self, method, url, **kwargs):
        self.assertTrue(kwargs["allow_redirects"] is False)
        if url.endswith("login/") or url.endswith("lease/"):
            return Response({"credential": "test-only-opaque-token", "lease": self.envelope(kwargs["json"]["nonce"])})
        return Response({"ok": True})
    def login(self):
        self.client.login("school@example.com", "not-persisted-password")
        self.client.bind_database(database.DB_PATH)
    def test_login_signature_and_no_secrets_in_state(self):
        self.login()
        self.assertTrue(self.client.allowed())
        content = self.client.state_path.read_text()
        self.assertNotIn("opaque-token", content)
        self.assertNotIn("not-persisted-password", content)
        self.assertEqual(self.client.require_lease()["student_limit"], 900)
    def test_valid_offline_state_admits_after_repeated_restart(self):
        self.login()
        restarted = CloudClient(self.config, self.client.state_path, store=self.vault, transport=self.transport)
        self.assertTrue(restarted.allowed())
        with patch.object(restarted, 'transport', side_effect=requests.ConnectionError()):
            for _ in range(3):
                restarted.checkpoint_time()
                restarted = CloudClient(self.config, self.client.state_path, store=self.vault,
                                        transport=lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError()))
                self.assertTrue(restarted.allowed())
    def test_replay_wrong_nonce_device_key_school_and_ttl_rejected(self):
        for changes in [{"nonce": "wrong"}, {"device_id": str(uuid.uuid4())}, {"subscription_expires_at": time.time() - 1}, {"student_limit": True}]:
            with self.subTest(changes=changes):
                with self.assertRaises(AccessDenied):
                    self.client.accept_lease(self.envelope("n" * 43, **changes), "n" * 43, license_clock())
        envelope = self.envelope("n" * 43)
        envelope["signature"] = base64.b64encode(b"x" * 64).decode()
        with self.assertRaises(AccessDenied):
            self.client.accept_lease(envelope, "n" * 43, license_clock())
        self.login()
        with self.assertRaises(AccessDenied):
            self.client.accept_lease(self.envelope("n" * 43, school_id=str(uuid.uuid4())), "n" * 43, license_clock())
    def test_clock_rollback_does_not_extend_running_lease(self):
        self.login()
        with patch("src.cloud.client.time.time", return_value=0):
            self.assertFalse(self.client.allowed())
            with patch("src.cloud.client.license_clock", return_value=self.client._deadline + 1):
                self.assertFalse(self.client.allowed())
                with self.assertRaises(AccessDenied):
                    self.client.require_lease()
    def test_network_failure_or_unsigned_http_denial_preserves_paid_license(self):
        self.login()
        with patch.object(self.client, "transport", side_effect=requests.ConnectionError()):
            with self.assertRaises(CloudError):
                self.client.renew()
        self.assertTrue(self.client.allowed())
        with patch.object(self.client, "transport", return_value=Response({}, status=403)):
            with self.assertRaises(AccessDenied):
                self.client.renew()
        self.assertTrue(self.client.allowed())
    def test_capacity_check_is_atomic_under_concurrent_registration(self):
        self.login()
        self.client._lease["student_limit"] = 1
        bind_gate(self.client)
        def add(index):
            try:
                database.add_student(str(index), "Test")
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(add, range(2)))
        self.assertEqual(sorted(results), [False, True])
        with closing(sqlite3.connect(database.DB_PATH)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM students").fetchone()[0], 1)
        with patch("src.cloud.client.license_clock", return_value=self.client._deadline + 1):
            self.assertFalse(runtime_allowed())
            with self.assertRaises(AccessDenied):
                database.add_student("3", "Blocked")
    def test_backup_snapshot_integrity_and_upload(self):
        self.login()
        self.client.configure_backup_key(self.root / "recovery.frkey")
        database.add_student("123", "Snapshot student")
        def upload(method, url, **kwargs):
            value = kwargs["data"].read()
            self.assertEqual(hashlib.sha256(value).hexdigest(), kwargs["headers"]["X-Backup-SHA256"])
            path = self.root / "received.frbackup"
            path.write_bytes(value)
            self.assertNotIn(b'Snapshot student', value)
            from src.cloud.recovery import unpack_bundle
            extracted = self.root / 'extracted'
            extracted.mkdir()
            unpack_bundle(path, extracted, self.school_id, self.client.backup_key())
            with closing(sqlite3.connect(extracted / 'attendance.db')) as connection:
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM students").fetchone()[0], 1)
            return Response({"id": str(uuid.uuid4())}, status=201)
        self.client.transport = upload
        self.assertIn("id", self.client.backup(database.DB_PATH))
        self.assertIn("last_backup_at", self.client.state)
    def download(self, raw, digest=None):
        return Response(raw=raw, headers={"X-Backup-SHA256": digest or hashlib.sha256(raw).hexdigest()})
    def test_restore_preserves_original_and_rejects_tampering(self):
        self.login()
        database.add_student("old", "Old record")
        with closing(sqlite3.connect(database.DB_PATH)) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        raw = database.DB_PATH.read_bytes()
        database.add_student("new", "New record")
        self.client.transport = lambda *args, **kwargs: self.download(raw, "0" * 64)
        with self.assertRaises(CloudError):
            self.client.restore(uuid.uuid4(), database.DB_PATH)
        with closing(sqlite3.connect(database.DB_PATH)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM students").fetchone()[0], 2)
        self.client.transport = lambda *args, **kwargs: self.download(raw)
        self.client.restore(uuid.uuid4(), database.DB_PATH)
        with closing(sqlite3.connect(database.DB_PATH)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM students").fetchone()[0], 1)
        rollback = list(self.root.glob("before-restore-*.sqlite3"))
        self.assertEqual(len(rollback), 1)
        with closing(sqlite3.connect(rollback[0])) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM students").fetchone()[0], 2)
    def test_restore_wrong_school_never_replaces_database(self):
        self.login()
        other = self.root / "other.db"
        with closing(sqlite3.connect(database.DB_PATH)) as source, closing(sqlite3.connect(other)) as target:
            source.backup(target)
            target.execute("UPDATE app_settings SET value=? WHERE key='cloud_school_id'", (str(uuid.uuid4()),))
            target.commit()
        self.client.transport = lambda *args, **kwargs: self.download(other.read_bytes())
        with self.assertRaises(CloudError):
            self.client.restore(uuid.uuid4(), database.DB_PATH)
        self.client.bind_database(database.DB_PATH)
    def test_logout_clears_credentials_even_on_network_failure(self):
        self.login()
        with patch.object(self.client, "transport", side_effect=requests.ConnectionError()):
            with self.assertRaises(CloudError):
                self.client.logout()
        self.assertIsNone(self.vault.token)
        self.assertFalse(self.client.allowed())
    def test_config_rejects_insecure_origin(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({"service_url": "http://example.com", "public_key": ""}))
        with self.assertRaises(CloudError):
            ClientConfig.load(path)

    def test_closed_session_cannot_be_revived_by_late_network_reply(self):
        self.login()
        envelope = self.envelope("n" * 43)
        self.client.close()
        with self.assertRaises(AccessDenied):
            self.client.accept_lease(envelope, "n" * 43, license_clock())
        self.assertFalse(self.client.allowed())

    def test_backup_feature_disabled_does_not_revoke_valid_subscription(self):
        self.login()
        self.client.configure_backup_key(self.root / "recovery.frkey")
        with patch.object(self.client, "transport", return_value=Response({"error": "backups_disabled"}, status=403)):
            with self.assertRaises(CloudError):
                self.client.backup(database.DB_PATH)
        self.assertTrue(self.client.allowed())

    def test_state_persistence_does_not_hold_camera_entitlement_lock(self):
        import threading
        self.login()
        saving, release, checked = threading.Event(), threading.Event(), threading.Event()
        def blocked_save():
            saving.set()
            release.wait(2)
        with patch.object(self.client, "save_state", side_effect=blocked_save):
            with ThreadPoolExecutor(max_workers=2) as executor:
                renewal = executor.submit(self.client.renew)
                try:
                    self.assertTrue(saving.wait(2))
                    def check():
                        result = self.client.allowed()
                        checked.set()
                        return result
                    observed = executor.submit(check)
                    self.assertTrue(checked.wait(.5), "disk persistence held the camera gate lock")
                    self.assertTrue(observed.result())
                finally:
                    release.set()
                renewal.result(2)


    @unittest.skipUnless(hasattr(time, "CLOCK_BOOTTIME"), "Linux suspend-aware clock")
    def test_linux_suspend_expires_lease_even_if_monotonic_did_not_advance(self):
        with patch("src.cloud.client.time.clock_gettime", return_value=1000.0):
            self.login()
            self.assertTrue(self.client.allowed())
        # Simulate a long suspend and a rolled-back desktop wall clock. The
        # ordinary monotonic clock has not advanced, but boot time has.
        with patch("src.cloud.client.time.clock_gettime", return_value=100000.0), \
             patch("src.cloud.client.time.monotonic", return_value=1000.0), \
             patch("src.cloud.client.time.time", return_value=0):
            self.assertFalse(self.client.allowed())

    def test_offline_expiration_then_online_renewal_and_multiple_annual_cycles(self):
        self.login()
        original = time.time()
        for year in range(1, 4):
            current = original + 366 * 86400 * year
            with patch('src.cloud.client.time.time', return_value=current), patch('src.cloud.client.license_clock', return_value=current):
                self.assertFalse(self.client.allowed())
                self.client.renew()
                self.assertTrue(self.client.allowed())
                restarted = CloudClient(self.config, self.client.state_path, store=self.vault, transport=self.transport)
                self.assertTrue(restarted.allowed())
                self.client = restarted
        self.assertTrue(database.DB_PATH.exists())

    def test_signed_revocation_persists_offline_and_renewal_restores_access(self):
        self.login()
        with patch.object(self.client, 'transport', side_effect=lambda *a, **kw: Response({'lease': self.envelope(kw['json']['nonce'], status='revoked')})):
            with self.assertRaises(AccessDenied):
                self.client.renew()
        self.assertFalse(self.client.allowed())
        restarted = CloudClient(self.config, self.client.state_path, store=self.vault)
        self.assertFalse(restarted.allowed())
        self.client.renew()
        self.assertTrue(self.client.allowed())

    def test_clock_and_license_state_rollback_require_online_verification(self):
        self.login()
        for change in ('clock', 'signature', 'generation', 'device', 'school'):
            state = dict(self.client.state)
            with self.subTest(change=change):
                if change == 'signature':
                    state['signed_license'] = dict(state['signed_license'], signature=base64.b64encode(b'x' * 64).decode())
                elif change == 'generation':
                    state['license_generation'] += 20
                elif change == 'device':
                    state['installation_id'] = str(uuid.uuid4())
                elif change == 'school':
                    state['school_id'] = str(uuid.uuid4())
                self.client.state_path.write_text(json.dumps(state))
                with patch('src.cloud.client.time.time', return_value=0 if change == 'clock' else time.time()):
                    restarted = CloudClient(self.config, self.client.state_path, store=self.vault)
                    self.assertFalse(restarted.allowed())
        self.client.save_state()
        self.client.renew()
        self.assertTrue(self.client.allowed())

    def test_activation_requires_operating_system_protected_storage(self):
        with patch.object(self.vault, 'set', return_value=False):
            with self.assertRaises(CloudError):
                self.client.login('school@example.com', 'password')
        self.assertFalse(self.client.allowed())

    def test_capacity_reduction_preserves_access_restore_and_archival_history(self):
        import numpy as np
        self.login()
        bind_gate(self.client)
        first = database.add_student('first', 'First')
        database.add_embedding(first, np.ones(128, dtype=np.float64))
        database.mark_attendance(first)
        database.add_student('second', 'Second')
        self.client.accept_lease(self.envelope('n' * 43, student_limit=1), 'n' * 43, license_clock())
        self.assertTrue(self.client.allowed())
        with self.assertRaises(ValueError):
            database.add_student('third', 'Third')
        database.set_student_capacity_archived(first, True)
        self.assertTrue(database.get_student(first))
        self.assertEqual(len(database.load_students()), 1)  # Archive NEVER changes biometric decisions.
        with self.assertRaises(ValueError):
            database.set_student_capacity_archived(first, False)
        with closing(sqlite3.connect(database.DB_PATH)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM attendance').fetchone()[0], 1)
        self.client.bind_database(database.DB_PATH)

    def test_old_local_database_migrates_without_record_or_embedding_loss(self):
        self.login()
        database.add_student('old', 'Existing school')
        with closing(sqlite3.connect(database.DB_PATH)) as connection:
            connection.execute('ALTER TABLE students DROP COLUMN capacity_archived')
            connection.commit()
        database.init_db()
        database.init_db()
        with closing(sqlite3.connect(database.DB_PATH)) as connection:
            self.assertEqual(connection.execute('SELECT capacity_archived FROM students').fetchall(), [(0,)])

    def test_checkpoint_time_detects_rollback_across_offline_restart(self):
        self.login()
        with patch('src.cloud.client.time.time', return_value=time.time() + 1000):
            self.client.checkpoint_time()
        restarted = CloudClient(self.config, self.client.state_path, store=self.vault)
        self.assertFalse(restarted.allowed())
        self.client.renew()
        self.assertTrue(self.client.allowed())

    def test_signed_revocation_blocks_even_when_vault_write_fails(self):
        self.login()
        with patch.object(self.vault, 'set', return_value=False):
            with self.assertRaises(AccessDenied):
                self.client.accept_lease(self.envelope('n' * 43, status='revoked'), 'n' * 43, license_clock())
        self.assertFalse(self.client.allowed())
        restarted = CloudClient(self.config, self.client.state_path, store=self.vault)
        self.assertFalse(restarted.allowed())

    def test_old_license_file_rollback_does_not_restore_previous_entitlement(self):
        self.login()
        previous = self.client.state_path.read_bytes()
        self.client.renew()
        self.client.state_path.write_bytes(previous)
        restarted = CloudClient(self.config, self.client.state_path, store=self.vault, transport=self.transport)
        self.assertFalse(restarted.allowed())
        restarted.renew()
        self.assertTrue(restarted.allowed())

    def test_damaged_metadata_has_a_recoverable_online_path(self):
        self.login()
        self.client.state_path.write_bytes(b'{partial-write')
        restarted = CloudClient(self.config, self.client.state_path, store=self.vault, transport=self.transport)
        self.assertFalse(restarted.allowed())
        self.assertEqual(restarted.installation, self.client.installation)
        restarted.renew()
        self.assertTrue(restarted.allowed())

    def test_wrong_local_clock_at_activation_uses_trusted_server_time(self):
        server_time = time.time()
        def server_response(*args, **kwargs):
            return Response({'credential': 'token', 'lease': self.envelope(kwargs['json']['nonce'],
                issued_at=server_time, subscription_starts_at=server_time-86400,
                subscription_expires_at=server_time+365*86400)})
        self.client.transport = server_response
        with patch('src.cloud.client.time.time', return_value=server_time+2*365*86400):
            self.client.login('school@example.com', 'password')
            self.assertTrue(self.client.allowed())
            restarted = CloudClient(self.config, self.client.state_path, store=self.vault)
            self.assertTrue(restarted.allowed())

    def test_interrupted_online_mirror_write_recovers_new_offline_license(self):
        self.login()
        old_expiry = self.client.require_lease()['subscription_expires_at']
        with patch.object(self.client, 'save_state', side_effect=OSError('power lost before mirror replacement')):
            with self.assertRaises(OSError):
                self.client.accept_lease(self.envelope('n' * 43, subscription_expires_at=old_expiry+365*86400), 'n' * 43, license_clock())
        # Public file remains OLD, protected state is NEW. This is an interrupted
        # two-store commit, not permission to restore the older subscription.
        restarted = CloudClient(self.config, self.client.state_path, store=self.vault)
        self.assertTrue(restarted.allowed())
        self.assertEqual(restarted.require_lease()['subscription_expires_at'], old_expiry+365*86400)

    def test_separate_installation_paths_do_not_share_an_activation_anchor(self):
        self.login()
        other = CloudClient(self.config, self.root / 'other-school' / 'cloud-state.json', store=self.vault)
        self.assertNotEqual(other.installation, self.client.installation)
        self.assertFalse(other.allowed())
