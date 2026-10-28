from datetime import timedelta
import hashlib
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import CompanyOwner, School, Device
from .owner_auth import STEP_UP_SESSION_KEY


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], STORAGES={'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class OwnerCustomerTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('owner-synthetic@example.com', is_staff=True)
        CompanyOwner.objects.create(user=self.owner)
        user = get_user_model().objects.create_user('school-synthetic@example.com')
        self.school = School.objects.create(user=user, name='Synthetic school')
        self.device = Device.objects.create(school=self.school, installation_id=uuid.uuid4(), label='Synthetic desktop', token_digest=hashlib.sha256(b'synthetic-token').hexdigest(), password_stamp=user.get_session_auth_hash(), credential_expires_at=timezone.now()+timedelta(days=365))
    def owner_login(self):
        import time
        self.client.force_login(self.owner)
        session = self.client.session
        session[STEP_UP_SESSION_KEY] = {'issued': time.time(), 'user': str(self.owner.pk), 'revision': 1, 'auth': self.owner.get_session_auth_hash()}
        session.save()
    def test_school_cannot_access_owner_customer_management(self):
        self.client.force_login(self.school.user)
        url = reverse('owner-school', args=[self.school.pk])
        self.assertEqual(self.client.get(url, secure=True).status_code, 403)
        self.assertEqual(self.client.post(reverse('owner-device-revoke', args=[self.school.pk,self.device.pk]), secure=True).status_code, 403)
    def test_customer_form_and_explicit_device_revocation_preserve_sync_digest(self):
        self.owner_login()
        response = self.client.get(reverse('owner-school', args=[self.school.pk]), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('user', response.context['form'].fields)
        digest = self.device.token_digest
        self.assertEqual(self.client.post(reverse('owner-device-revoke', args=[self.school.pk,self.device.pk]), secure=True).status_code, 302)
        self.device.refresh_from_db()
        self.assertTrue(self.device.revoked)
        self.assertEqual(self.device.token_digest, digest)
    def test_device_cannot_be_revoked_using_another_school_path(self):
        self.owner_login()
        another = get_user_model().objects.create_user('another-synthetic@example.com')
        school = School.objects.create(user=another, name='Another school')
        self.assertEqual(self.client.post(reverse('owner-device-revoke', args=[school.pk,self.device.pk]), secure=True).status_code, 404)
        self.device.refresh_from_db()
        self.assertFalse(self.device.revoked)
