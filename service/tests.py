import base64
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from service.backups import location
from service.models import Backup, Device, School

PASSWORD = "Account-test-password!95"


class ServiceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.signer = Ed25519PrivateKey.generate()
        raw = cls.signer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        cls.overrides = override_settings(SIGNING_PRIVATE_KEY=base64.b64encode(raw).decode(),
            BACKUP_MASTER_KEY=base64.b64encode(b"x" * 32).decode(), BACKUP_ROOT=Path(cls.directory.name),
            STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
            PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
        cls.overrides.enable()
        super().setUpClass()
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.overrides.disable()
        cls.directory.cleanup()
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("school@example.com", email="school@example.com", password=PASSWORD)
        self.school = School.objects.create(user=self.user, name="School One", status="active", student_limit=900,
            starts_at=timezone.now() - timedelta(days=1), expires_at=timezone.now() + timedelta(days=365), email_verified_at=timezone.now())
        self.installation = str(uuid.uuid4())
    def post(self, url, payload, token=None):
        headers = {"HTTP_AUTHORIZATION": "Bearer " + token} if token else {}
        return self.client.post(url, data=json.dumps(payload), content_type="application/json", secure=True, **headers)
    def login(self, installation=None, email="school@example.com", password=PASSWORD):
        return self.post("/api/v1/login/", {"email": email, "password": password,
            "device_id": installation or self.installation, "nonce": "n" * 43})
    def token(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        return response.json()["credential"]
    def upload(self, token, value=b"SQLite format 3\x00" + b"sample-data" * 50):
        return self.client.post("/api/v1/backups/upload/", data=value, content_type="application/octet-stream", secure=True,
            HTTP_AUTHORIZATION="Bearer " + token, HTTP_X_BACKUP_SHA256=hashlib.sha256(value).hexdigest())
    def test_lease_signature_nonce_and_limits(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        envelope = response.json()["lease"]
        raw = base64.b64decode(envelope["payload"])
        self.signer.public_key().verify(base64.b64decode(envelope["signature"]), raw)
        lease = json.loads(raw)
        self.assertEqual(lease["student_limit"], 900)
        self.assertEqual(lease["nonce"], "n" * 43)
        self.assertLessEqual(lease["lease_expires_at"] - lease["issued_at"], 900)
        self.assertNotIn(response.json()["credential"], Device.objects.get().token_digest)
    def test_inactive_expired_future_and_banned_deny_login(self):
        for status, start, end in [("banned", -1, 1), ("suspended", -1, 1), ("active", -2, -1), ("active", 1, 2)]:
            with self.subTest(status=status, start=start):
                self.school.status = status
                self.school.starts_at = timezone.now() + timedelta(days=start)
                self.school.expires_at = timezone.now() + timedelta(days=end)
                self.school.save()
                self.assertEqual(self.login().status_code, 403)
    def test_device_limit_revocation_and_token_rotation(self):
        first = self.token()
        self.assertEqual(self.login(str(uuid.uuid4())).json()["error"], "device_limit_reached")
        second = self.token()
        self.assertNotEqual(first, second)
        self.assertEqual(self.post("/api/v1/lease/", {"nonce": "x" * 43}, first).status_code, 401)
        device = Device.objects.get()
        device.revoked = True
        device.save()
        self.assertEqual(self.post("/api/v1/lease/", {"nonce": "x" * 43}, second).status_code, 401)
        self.assertEqual(self.login().json()["error"], "device_revoked")
    def test_password_change_invalidates_device_credential(self):
        token = self.token()
        self.user.set_password("A-different-secure-password!67")
        self.user.save()
        self.assertEqual(self.post("/api/v1/lease/", {"nonce": "x" * 43}, token).status_code, 401)
    def test_ban_and_expiry_reject_renewal(self):
        token = self.token()
        self.school.status = "banned"
        self.school.save()
        self.assertEqual(self.post("/api/v1/lease/", {"nonce": "x" * 43}, token).status_code, 403)
    def test_logout_invalidates_token(self):
        token = self.token()
        self.assertEqual(self.post("/api/v1/logout/", {}, token).status_code, 200)
        self.assertEqual(self.post("/api/v1/lease/", {"nonce": "x" * 43}, token).status_code, 401)
    def test_login_rate_limit(self):
        for _ in range(15):
            self.assertEqual(self.login(password="wrong").status_code, 401)
        self.assertEqual(self.login(password="wrong").status_code, 429)
    def test_cookie_session_cannot_authenticate_device_api(self):
        self.client.force_login(self.user)
        self.assertEqual(self.post("/api/v1/lease/", {"nonce": "x" * 43}).status_code, 401)
    def test_json_required_and_nonce_validated(self):
        self.assertEqual(self.client.post("/api/v1/login/", {"email": self.user.email}, secure=True).status_code, 400)
        self.assertEqual(self.post("/api/v1/login/", {"device_id": self.installation, "nonce": "short"}).status_code, 400)
    def test_encrypted_backup_roundtrip_and_tenant_isolation(self):
        token = self.token()
        plain = b"SQLite format 3\x00" + b"sensitive-student-data" * 10
        uploaded = self.upload(token, plain)
        self.assertEqual(uploaded.status_code, 201)
        item = Backup.objects.get(pk=uploaded.json()["id"])
        blob = location(item.school_id, item.blob_name).read_bytes()
        self.assertNotIn(b"sensitive-student-data", blob)
        response = self.client.get(f"/api/v1/backups/{item.pk}/", secure=True, HTTP_AUTHORIZATION="Bearer " + token)
        self.assertEqual(b"".join(response.streaming_content), plain)
        other = get_user_model().objects.create_user("other@example.com", password=PASSWORD)
        School.objects.create(user=other, name="Other", status="active", student_limit=1, starts_at=self.school.starts_at, expires_at=self.school.expires_at)
        other_token = self.login(str(uuid.uuid4()), "other@example.com").json()["credential"]
        self.assertEqual(self.client.get(f"/api/v1/backups/{item.pk}/", secure=True, HTTP_AUTHORIZATION="Bearer " + other_token).status_code, 404)
        listing = self.client.get("/api/v1/backups/", secure=True, HTTP_AUTHORIZATION="Bearer " + other_token)
        self.assertEqual(listing.json()["backups"], [])
        self.client.force_login(other)
        self.assertEqual(self.client.get(f"/account/backups/{item.pk}/", secure=True).status_code, 404)
    def test_corruption_never_returns_plaintext(self):
        token = self.token()
        self.upload(token)
        item = Backup.objects.get()
        path = location(item.school_id, item.blob_name)
        value = bytearray(path.read_bytes())
        value[-1] ^= 1
        path.write_bytes(value)
        response = self.client.get(f"/api/v1/backups/{item.pk}/", secure=True, HTTP_AUTHORIZATION="Bearer " + token)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.streaming)
    def test_backup_bounds_quota_and_cleanup(self):
        token = self.token()
        with override_settings(MAX_BACKUP_BYTES=20):
            self.assertEqual(self.upload(token).status_code, 413)
        self.assertEqual(self.upload(token, b"not a database at all").status_code, 400)
        self.school.backup_quota_bytes = 20
        self.school.save()
        self.assertEqual(self.upload(token).status_code, 409)
        self.assertFalse(Backup.objects.exists())
        self.assertFalse(list((settings.BACKUP_ROOT / str(self.school.pk)).iterdir()))
    def test_retention_and_expired_account_can_export_old_data(self):
        token = self.token()
        self.school.backup_retention = 1
        self.school.save()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.upload(token).status_code, 201)
            self.assertEqual(self.upload(token).status_code, 201)
        self.assertEqual(Backup.objects.count(), 1)
        self.assertEqual(len(list((settings.BACKUP_ROOT / str(self.school.pk)).glob("*.enc"))), 1)
        self.school.expires_at = timezone.now() - timedelta(seconds=1)
        self.school.save()
        self.assertEqual(self.upload(token).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/backups/", secure=True, HTTP_AUTHORIZATION="Bearer " + token).status_code, 200)
    def test_registration_email_verification_and_no_auto_subscription(self):
        response = self.client.post("/register/", {"username": "New@Example.com", "school_name": "New school", "password1": PASSWORD, "password2": PASSWORD}, secure=True)
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="new@example.com")
        self.assertFalse(user.is_active)
        url = mail.outbox[-1].body.strip().splitlines()[-1]
        self.assertEqual(self.client.get(url, secure=True).status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertFalse(user.school.entitled)
        user.is_active = False
        user.save()
        self.assertEqual(self.client.get(url, secure=True).status_code, 403)
    def test_web_csrf_staff_and_security_headers(self):
        browser = Client(enforce_csrf_checks=True)
        self.assertEqual(browser.post("/login/", {"username": self.user.username, "password": PASSWORD}, secure=True).status_code, 403)
        self.client.force_login(self.user)
        response = self.client.get("/account/", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertEqual(self.client.get("/admin/", secure=True).status_code, 302)
    def test_school_cannot_revoke_other_device(self):
        self.token()
        other = get_user_model().objects.create_user("other@example.com", password=PASSWORD)
        self.client.force_login(other)
        self.assertEqual(self.client.post(f"/account/devices/{Device.objects.get().pk}/revoke/", secure=True).status_code, 404)


    def test_verification_resend_after_admin_sets_subscription_dates(self):
        user = get_user_model().objects.create_user("pending@example.com", email="pending@example.com", password=PASSWORD, is_active=False)
        School.objects.create(user=user, name="Pending", status="active", student_limit=900,
            starts_at=self.school.starts_at, expires_at=self.school.expires_at)
        response = self.client.post("/resend-verification/", {"email": user.email}, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        link = mail.outbox[-1].body.strip().splitlines()[-1]
        self.assertEqual(self.client.get(link, secure=True).status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_password_reset_roundtrip_invalidates_old_device(self):
        credential = self.token()
        response = self.client.post("/password-reset/", {"email": self.user.email}, secure=True)
        self.assertEqual(response.status_code, 302)
        link = next(line.strip() for line in mail.outbox[-1].body.splitlines() if line.startswith("https://"))
        redirected = self.client.get(link, secure=True)
        self.assertEqual(redirected.status_code, 302)
        changed = self.client.post(redirected["Location"], {"new_password1": "New-strong-reset-password!84", "new_password2": "New-strong-reset-password!84"}, secure=True)
        self.assertEqual(changed.status_code, 302)
        self.assertEqual(self.post("/api/v1/lease/", {"nonce": "x" * 43}, credential).status_code, 401)


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.db import connection, connections
from django.test import TransactionTestCase


@skipUnless(connection.vendor == "postgresql", "Concurrent admission requires the production PostgreSQL backend")
class ConcurrentAdmissionTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        key = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        override = override_settings(SIGNING_PRIVATE_KEY=base64.b64encode(key).decode(),
            BACKUP_MASTER_KEY=base64.b64encode(b"z" * 32).decode(), BACKUP_ROOT=Path(self.directory.name),
            PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
        override.enable()
        self.addCleanup(override.disable)
        user = get_user_model().objects.create_user("race@example.com", password=PASSWORD)
        self.school = School.objects.create(user=user, name="Race", status="active", student_limit=900,
            starts_at=timezone.now() - timedelta(days=1), expires_at=timezone.now() + timedelta(days=1))
    def test_parallel_logins_cannot_exceed_device_limit(self):
        barrier = Barrier(2)
        def attempt(_):
            try:
                barrier.wait(timeout=5)
                return Client().post("/api/v1/login/", data=json.dumps({"email": "race@example.com", "password": PASSWORD,
                    "device_id": str(uuid.uuid4()), "nonce": "n" * 43}), content_type="application/json", secure=True).status_code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, range(2)))
        self.assertEqual(sorted(results), [200, 403])
        self.assertEqual(Device.objects.filter(school=self.school).count(), 1)
    def test_parallel_uploads_cannot_exceed_quota(self):
        self.school.backup_quota_bytes = 150
        self.school.save()
        response = self.client.post("/api/v1/login/", data=json.dumps({"email": "race@example.com", "password": PASSWORD,
            "device_id": str(uuid.uuid4()), "nonce": "n" * 43}), content_type="application/json", secure=True)
        token = response.json()["credential"]
        data = b"SQLite format 3\x00" + b"x" * 100
        barrier = Barrier(2)
        def attempt(_):
            try:
                barrier.wait(timeout=5)
                return Client().post("/api/v1/backups/upload/", data=data, content_type="application/octet-stream", secure=True,
                    HTTP_AUTHORIZATION="Bearer " + token, HTTP_X_BACKUP_SHA256=hashlib.sha256(data).hexdigest()).status_code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, range(2)))
        self.assertEqual(sorted(results), [201, 409])
        self.assertEqual(Backup.objects.count(), 1)
        self.assertEqual(len(list(settings.BACKUP_ROOT.rglob("*.enc"))), 1)
