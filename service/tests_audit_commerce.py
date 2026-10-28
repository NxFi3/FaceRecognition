"""Independent regressions for stale device authorization; synthetic credentials only."""
import base64
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import skipUnless
import hashlib
import inspect
import json
import uuid
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.db import connection, connections, transaction
from django.utils import timezone

from . import api
from .models import AuditEvent, Device, School


PASSWORD = 'Synthetic-audit-password!945'


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class DeviceAuthorizationRaceTests(TestCase):
    def setUp(self):
        cache.clear()
        signer = Ed25519PrivateKey.generate()
        raw = signer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        settings = override_settings(SIGNING_PRIVATE_KEY=base64.b64encode(raw).decode())
        settings.enable()
        self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_user('device-audit@example.invalid', password=PASSWORD)
        self.school = School.objects.create(user=self.user, name='Synthetic device audit', status='active', student_limit=900,
            starts_at=timezone.now()-timedelta(days=1), expires_at=timezone.now()+timedelta(days=365))
        self.installation = str(uuid.uuid4())
        response = self.post('/api/v1/login/', {'email': self.user.username, 'password': PASSWORD,
            'device_id': self.installation, 'nonce': 'n'*43, 'license_version': 2})
        self.assertEqual(response.status_code, 200)
        self.token = response.json()['credential']
        self.device = Device.objects.get()
    def post(self, url, payload, token=None):
        headers = {'HTTP_AUTHORIZATION': 'Bearer '+token} if token else {}
        return self.client.post(url, data=json.dumps(payload), content_type='application/json', secure=True, **headers)
    def sync_after_authentication_change(self, mutate):
        original = api.body
        def changed_before_body(request):
            # The device_auth wrapper has admitted the initial snapshot, but no
            # license has yet been signed. Another request commits here.
            mutate()
            return original(request)
        with patch('service.api.body', side_effect=changed_before_body):
            return self.post('/api/v1/lease/', {'nonce': 'x'*43, 'license_version': 2}, self.token)
    def decoded(self, response):
        self.assertEqual(response.status_code, 200)
        return json.loads(base64.b64decode(response.json()['lease']['payload']))
    def test_device_revocation_committed_after_authentication_is_signed_as_revoked(self):
        response = self.sync_after_authentication_change(lambda: Device.objects.filter(pk=self.device.pk).update(revoked=True))
        self.assertEqual(self.decoded(response)['status'], 'revoked')
    def test_school_suspension_committed_after_authentication_is_signed_as_revoked(self):
        response = self.sync_after_authentication_change(lambda: School.objects.filter(pk=self.school.pk).update(status='suspended'))
        self.assertEqual(self.decoded(response)['status'], 'revoked')
    def test_token_rotation_committed_after_authentication_cannot_mint_a_license(self):
        response = self.sync_after_authentication_change(lambda: Device.objects.filter(pk=self.device.pk).update(token_digest=hashlib.sha256(b'new-session-token').hexdigest()))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error'], 'invalid_device_credential')
    def test_password_change_committed_after_authentication_cannot_mint_a_license(self):
        def change_password():
            user = get_user_model().objects.get(pk=self.user.pk)
            user.set_password('Synthetic-new-password!936')
            user.save()
        self.assertEqual(self.sync_after_authentication_change(change_password).status_code, 401)
    def test_stale_logout_cannot_delete_a_newly_authenticated_session(self):
        # Deliberately invoke the view body with the exact stale context set by
        # device_auth. This isolates the interleaving between wrapper and view.
        request = RequestFactory().post('/api/v1/logout/', HTTP_AUTHORIZATION='Bearer '+self.token)
        request.device, request.school = self.device, self.school
        request._device_auth_digest = hashlib.sha256(self.token.encode()).hexdigest()
        new_digest = hashlib.sha256(b'new-session-token').hexdigest()
        Device.objects.filter(pk=self.device.pk).update(token_digest=new_digest)
        response = inspect.unwrap(api.logout)(request)
        self.assertEqual(response.status_code, 401)
        self.device.refresh_from_db()
        self.assertEqual(self.device.token_digest, new_digest)
        self.assertFalse(AuditEvent.objects.filter(action='device.logout').exists())

    def test_password_change_after_password_authentication_cannot_activate_a_device(self):
        original = api.authenticate
        old_digest = self.device.token_digest
        def changed_after_authentication(*args, **kwargs):
            authenticated = original(*args, **kwargs)
            user = get_user_model().objects.get(pk=self.user.pk)
            user.set_password('Synthetic-password-rotation!857')
            user.save()
            return authenticated
        with patch('service.api.authenticate', side_effect=changed_after_authentication):
            response = self.post('/api/v1/login/', {'email': self.user.username, 'password': PASSWORD,
                'device_id': self.installation, 'nonce': 'z'*43, 'license_version': 2})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error'], 'invalid_credentials')
        self.device.refresh_from_db()
        self.assertEqual(self.device.token_digest, old_digest)

    def test_fresh_authorization_helper_refuses_use_without_transaction(self):
        # The transaction boundary is a required part of the helper contract.
        request = RequestFactory().post('/api/v1/logout/')
        with patch('service.api.transaction.get_connection') as get_connection:
            get_connection.return_value.in_atomic_block = False
            with self.assertRaises(RuntimeError):
                api.locked_authenticated_device(request)

    def test_expired_network_credential_can_be_logged_out_and_cannot_sync_again(self):
        Device.objects.filter(pk=self.device.pk).update(credential_expires_at=timezone.now()-timedelta(days=1))
        response = self.post('/api/v1/logout/', {}, self.token)
        sync = self.post('/api/v1/lease/', {'nonce': 'e'*43, 'license_version': 2}, self.token)
        # Previously logout rejected the expired token, while the sync route
        # accepted that SAME secret and restored an active long-lived lease.
        self.assertEqual(sync.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.device.refresh_from_db()
        self.assertIsNone(self.device.token_digest)

    def test_revoked_current_credential_can_invalidate_itself_without_granting_access(self):
        Device.objects.filter(pk=self.device.pk).update(revoked=True)
        response = self.post('/api/v1/logout/', {}, self.token)
        self.assertEqual(response.status_code, 200)
        self.device.refresh_from_db()
        self.assertTrue(self.device.revoked)
        self.assertIsNone(self.device.token_digest)
        self.assertEqual(self.post('/api/v1/lease/', {'nonce': 'r'*43, 'license_version': 2}, self.token).status_code, 401)


@skipUnless(connection.vendor == 'postgresql', 'Signing/revocation serialization requires PostgreSQL row locks')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class DeviceSigningConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        signer = Ed25519PrivateKey.generate()
        raw = signer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        overrides = override_settings(SIGNING_PRIVATE_KEY=base64.b64encode(raw).decode())
        overrides.enable()
        self.addCleanup(overrides.disable)
        user = get_user_model().objects.create_user('signing-race@example.invalid', password=PASSWORD)
        self.school = School.objects.create(user=user, name='Synthetic signing race', status='active', student_limit=900,
            starts_at=timezone.now()-timedelta(days=1), expires_at=timezone.now()+timedelta(days=365))
        self.token = 'synthetic-signing-race-token'
        self.device = Device.objects.create(school=self.school, installation_id=uuid.uuid4(), label='Synthetic desktop',
            token_digest=hashlib.sha256(self.token.encode()).hexdigest(), password_stamp=user.get_session_auth_hash(),
            credential_expires_at=timezone.now()+timedelta(days=365))
    def test_revocation_committed_while_request_waits_for_body_cannot_receive_active_license(self):
        admitted, revoked = Event(), Event()
        original = api.body
        def body_after_revocation(request):
            admitted.set()
            if not revoked.wait(5):
                raise AssertionError('Revocation interleaving was not scheduled')
            return original(request)
        def request_license():
            try:
                response = Client().post('/api/v1/lease/', data=json.dumps({'nonce': 'q'*43, 'license_version': 2}),
                    content_type='application/json', secure=True, HTTP_AUTHORIZATION='Bearer '+self.token)
                return response.status_code, response.json()
            finally:
                connections.close_all()
        with patch('service.api.body', side_effect=body_after_revocation), ThreadPoolExecutor(max_workers=1) as executor:
            job = executor.submit(request_license)
            try:
                self.assertTrue(admitted.wait(5), 'Request never reached authenticated body parsing')
                with transaction.atomic():
                    School.objects.select_for_update().get(pk=self.school.pk)
                    Device.objects.filter(pk=self.device.pk).update(revoked=True)
            finally:
                revoked.set()
            status, data = job.result(timeout=5)
        self.assertEqual(status, 200)
        payload = json.loads(base64.b64decode(data['lease']['payload']))
        self.assertEqual(payload['status'], 'revoked')
