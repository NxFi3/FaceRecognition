"""Independent web regressions. All accounts/IPs/data are synthetic."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import School


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WebHardeningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('audit-school@example.invalid',
            email='audit-school@example.invalid', password='Synthetic-password!542')
        self.school = School.objects.create(user=self.user, name='Synthetic audit school')

    def test_login_account_limit_normalizes_whitespace_across_different_ips(self):
        # EmailField accepts all these as the exact same account. Distinct IPs
        # ensure this tests the shared account guard rather than its IP guard.
        for index in range(15):
            response = self.client.post(reverse('login'), {
                'username': ' ' * index + self.user.username + '\t', 'password': 'incorrect'},
                REMOTE_ADDR=f'198.51.100.{index+1}', secure=True)
            self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse('login'), {
            'username': '\t' + self.user.username + ' ' * 16, 'password': 'incorrect'},
            REMOTE_ADDR='203.0.113.16', secure=True)
        self.assertEqual(response.status_code, 429)

    def owner_login(self):
        from types import SimpleNamespace
        from .models import CompanyOwner
        from .owner_auth import mark_owner_reauthenticated
        owner = get_user_model().objects.create_user('audit-owner@example.invalid',
            password='Synthetic-owner-password!543', is_staff=True)
        CompanyOwner.objects.create(user=owner)
        self.client.force_login(owner)
        session = self.client.session
        mark_owner_reauthenticated(SimpleNamespace(user=owner, session=session))
        session.save()
        return owner

    def test_admin_unicode_username_aliases_share_one_account_budget(self):
        from django.contrib.auth.forms import UsernameField
        from django.test import RequestFactory
        from .security import throttled
        fullwidth = ''.join(chr(ord(c)+0xFEE0) if 0x21 <= ord(c) <= 0x7E else c for c in self.user.username)
        self.assertEqual(UsernameField().clean(fullwidth), self.user.username)
        factory = RequestFactory()
        for index in range(15):
            identity = (' ' * index) + (fullwidth if index % 2 else self.user.username.upper())
            request = factory.post('/admin/login/', REMOTE_ADDR=f'198.51.100.{index+1}')
            self.assertFalse(throttled(request, 'independent-admin-alias', identity, limit=15))
        request = factory.post('/admin/login/', REMOTE_ADDR='203.0.113.19')
        self.assertTrue(throttled(request, 'independent-admin-alias', self.user.username, limit=15))

    def test_reset_and_resend_account_limits_canonicalize_email(self):
        for endpoint in ('password_reset', 'resend'):
            with self.subTest(endpoint=endpoint):
                cache.clear()
                for index in range(15):
                    response = self.client.post(reverse(endpoint), {'email': ' ' * index+self.user.email+'\t'},
                        REMOTE_ADDR=f'198.51.100.{index+1}', secure=True)
                    self.assertEqual(response.status_code, 302)
                response = self.client.post(reverse(endpoint), {'email': '\t'+self.user.email+' '*17},
                    REMOTE_ADDR='203.0.113.20', secure=True)
                self.assertEqual(response.status_code, 429)
                self.assertIn("default-src 'self'", response['Content-Security-Policy'])

    def test_protection_backend_failure_fails_closed_as_503_with_security_headers(self):
        with patch('service.security.throttled', side_effect=OSError('Synthetic Redis outage')):
            response = self.client.post(reverse('login'), {'username': self.user.username,
                'password': 'Synthetic-password!542'}, secure=True)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertIn("default-src 'self'", response['Content-Security-Policy'])
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertNotContains(response, 'Synthetic Redis outage', status_code=503)

    def test_owner_cannot_delete_device_digest_needed_for_signed_revocation(self):
        import hashlib
        import uuid
        from datetime import timedelta
        from django.utils import timezone
        from .models import Device
        self.owner_login()
        device = Device.objects.create(school=self.school, installation_id=uuid.uuid4(), label='Synthetic desktop',
            token_digest=hashlib.sha256(b'synthetic-device-credential').hexdigest(),
            password_stamp=self.user.get_session_auth_hash(), credential_expires_at=timezone.now()+timedelta(days=365))
        response = self.client.post(reverse('admin:service_device_delete', args=[device.pk]), {'post': 'yes'}, secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Device.objects.filter(pk=device.pk).exists())
        # The supported mutation retains authentication evidence for the signed
        # server denial instead of silently converting revocation to a 401.
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse('revoke-device', args=[device.pk]), secure=True).status_code, 302)
        device.refresh_from_db()
        self.assertTrue(device.revoked)
        self.assertIsNotNone(device.token_digest)

    def test_owner_health_reports_partial_database_probe_failure_without_500(self):
        self.owner_login()
        with patch('service.platform.EmailOutbox.objects.filter', side_effect=OSError('Synthetic database outage')):
            response = self.client.get(reverse('owner-health'), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['health']['database'])
        self.assertIsNone(response.context['health']['email_pending'])
        self.assertTrue(response.context['health']['probe_error'])
        self.assertNotContains(response, 'Synthetic database outage')
        self.assertNotContains(response, '>None<')

    def test_device_admin_revocation_never_overwrites_newer_network_credentials(self):
        import hashlib
        import uuid
        from datetime import timedelta
        from types import SimpleNamespace
        from django.contrib import admin
        from django.utils import timezone
        from .admin import DeviceAdmin
        from .models import Device
        owner = self.owner_login()
        device = Device.objects.create(school=self.school, installation_id=uuid.uuid4(), label='Synthetic desktop',
            token_digest=hashlib.sha256(b'old-synthetic-credential').hexdigest(), password_stamp='old-auth-stamp',
            credential_expires_at=timezone.now()+timedelta(days=30))
        stale_form_instance = Device.objects.get(pk=device.pk)
        stale_form_instance.revoked = True
        fresh_digest = hashlib.sha256(b'new-synthetic-credential').hexdigest()
        fresh_expiry = timezone.now()+timedelta(days=365)
        # A login/renewal committed after the admin view read its form instance.
        Device.objects.filter(pk=device.pk).update(token_digest=fresh_digest,
            password_stamp='current-auth-stamp', credential_expires_at=fresh_expiry)
        DeviceAdmin(Device, admin.site).save_model(SimpleNamespace(user=owner), stale_form_instance, None, True)
        device.refresh_from_db()
        self.assertTrue(device.revoked)
        self.assertEqual(device.token_digest, fresh_digest)
        self.assertEqual(device.password_stamp, 'current-auth-stamp')
        self.assertEqual(device.credential_expires_at, fresh_expiry)

    def test_plan_edit_and_audit_are_atomic_on_database_failure(self):
        from django.db import DatabaseError
        from .models import Plan
        self.owner_login()
        plan = Plan.objects.create(slug='audit-price-test', name='Synthetic annual plan', price_irr=1000,
            duration_days=365, student_limit=900, device_limit=1, is_active=True, is_public=True)
        payload = {'product': plan.product_id, 'slug': plan.slug, 'name': plan.name,
            'description': 'Synthetic only', 'price_irr': 2000, 'duration_days': 365,
            'student_limit': 900, 'device_limit': 1, 'is_active': 'on', 'is_public': 'on', 'sort_order': 0}
        with patch('service.platform.AuditEvent.objects.create', side_effect=DatabaseError('Synthetic audit database failure')):
            with self.assertRaises(DatabaseError):
                self.client.post(reverse('owner-plan', args=[plan.pk]), payload, secure=True)
        plan.refresh_from_db()
        self.assertEqual(plan.price_irr, 1000)

    def test_customer_profile_audit_failure_rolls_back_name(self):
        from django.db import DatabaseError
        self.client.force_login(self.user)
        original = self.school.name
        with patch('service.platform.AuditEvent.objects.create', side_effect=DatabaseError('Synthetic audit outage')):
            with self.assertRaises(DatabaseError):
                self.client.post(reverse('profile'), {'name': 'Replacement synthetic name'}, secure=True)
        self.school.refresh_from_db()
        self.assertEqual(self.school.name, original)

    def test_release_and_contact_mutations_rollback_if_audit_database_write_fails(self):
        from django.db import DatabaseError
        from .models import Release, ContactRequest
        self.owner_login()
        release = Release.objects.create(version='synthetic', artifact_name='synthetic.zip', sha256='a'*64,
            size_bytes=1, notes='Synthetic metadata only', is_published=True)
        contact = ContactRequest.objects.create(name='Synthetic', email='synthetic@example.invalid',
            school_name='', kind='contact', body='Synthetic request', consent=True)
        for name, identifier, record, field, original in (
                ('owner-release-withdraw', release.pk, release, 'is_published', True),
                ('owner-contact', contact.pk, contact, 'handled', False)):
            with self.subTest(action=name), patch('service.platform.AuditEvent.objects.create', side_effect=DatabaseError('Synthetic audit outage')):
                with self.assertRaises(DatabaseError):
                    self.client.post(reverse(name, args=[identifier]), secure=True)
                record.refresh_from_db()
                self.assertEqual(getattr(record, field), original)

    def test_missing_trial_order_returns_404_instead_of_unhandled_exception(self):
        import uuid
        self.owner_login()
        response = self.client.post(reverse('owner-trial', args=[uuid.uuid4()]), secure=True)
        self.assertEqual(response.status_code, 404)

    def test_school_hard_delete_cannot_cascade_issued_device_credentials(self):
        import hashlib
        import uuid
        from datetime import timedelta
        from django.contrib import admin
        from django.utils import timezone
        from .admin import SchoolAdmin
        from .models import Device
        owner = self.owner_login()
        School.objects.filter(pk=self.school.pk).update(status='active', student_limit=900,
            starts_at=timezone.now()-timedelta(days=1), expires_at=timezone.now()+timedelta(days=365))
        device = Device.objects.create(school=self.school, installation_id=uuid.uuid4(), label='Synthetic desktop',
            token_digest=hashlib.sha256(b'synthetic-issued-token').hexdigest(), password_stamp=self.user.get_session_auth_hash(),
            credential_expires_at=timezone.now()+timedelta(days=365))
        # No Order/Backup/ProductSubscription PROTECT rows: the lifecycle guard,
        # not an incidental related-row restriction, must stop this deletion.
        response = self.client.post(reverse('admin:service_school_delete', args=[self.school.pk]), {'post': 'yes'}, secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(School.objects.filter(pk=self.school.pk).exists())
        self.assertTrue(Device.objects.filter(pk=device.pk).exists())
        self.assertFalse(SchoolAdmin(School, admin.site).has_delete_permission(type('Request', (), {'user': owner})()))

    def test_school_bulk_delete_action_is_unavailable_and_cannot_erase_rows(self):
        from types import SimpleNamespace
        from django.contrib import admin
        from .admin import SchoolAdmin
        owner = self.owner_login()
        request = SimpleNamespace(user=owner, GET={}, POST={})
        self.assertNotIn('delete_selected', SchoolAdmin(School, admin.site).get_actions(request))
        response = self.client.post(reverse('admin:service_school_changelist'), {
            'action': 'delete_selected', '_selected_action': [str(self.school.pk)], 'post': 'yes'}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(School.objects.filter(pk=self.school.pk).exists())
