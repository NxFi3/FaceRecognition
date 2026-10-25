from importlib import import_module
import json
import uuid
from django.contrib.auth import get_user_model
from django.core import mail, signing
from django.db import connection
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django.apps import apps
from .models import School, CustomerProfile, AuditEvent


@override_settings(STORAGES={'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class UnifiedAccountTests(TestCase):
    def test_default_english_and_persistent_persian_preserve_workflow(self):
        response = self.client.get('/login/?next=/account/', secure=True)
        self.assertContains(response, 'lang="en" dir="ltr"')
        self.assertContains(response, 'Sign in to NxFi')
        changed = self.client.post('/language/', {'language':'fa', 'next':'/login/?next=/account/'}, secure=True)
        self.assertEqual(changed['Location'], '/login/?next=/account/')
        self.assertTrue(changed.cookies['django_language']['httponly'])
        self.assertContains(self.client.get(changed['Location'], secure=True), 'ورود به NxFi')
        self.assertContains(self.client.get('/', secure=True), 'lang="fa" dir="rtl"')
        self.client.post('/language/', {'language':'en', 'next':'/account/'}, secure=True)
        self.assertContains(self.client.get('/', secure=True), 'Thoughtful software.')

    def test_language_post_requires_csrf_and_rejects_external_next(self):
        csrf = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf.post('/language/', {'language':'fa'}, secure=True).status_code, 403)
        response = self.client.post('/language/', {'language':'en', 'next':'https://evil.invalid/'}, secure=True)
        self.assertNotIn('evil.invalid', response['Location'])

    def test_register_verify_login_without_school_or_entitlement(self):
        response = self.client.post('/register/', {'username':'synthetic-unified@example.invalid',
            'password1':'Synthetic-Secure-578123!', 'password2':'Synthetic-Secure-578123!'}, secure=True)
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username='synthetic-unified@example.invalid')
        self.assertFalse(user.is_active)
        self.assertFalse(School.objects.filter(user=user).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Verify your NxFi account', mail.outbox[0].subject)
        token = signing.dumps({'user':user.pk,'email':user.email}, salt='verify-school-email')
        self.assertEqual(self.client.get(reverse('verify', args=[token]), secure=True).status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.nxfi_profile.email_verified_at)
        self.assertTrue(self.client.login(username=user.username, password='Synthetic-Secure-578123!'))
        self.assertContains(self.client.get('/account/', secure=True), 'No product workspace yet.')
        self.assertFalse(user.is_staff)
        self.assertEqual(self.client.get('/owner/', secure=True).status_code, 403)
        self.assertEqual(self.client.post('/api/v1/login/', data=json.dumps({'email':user.email, 'password':'Synthetic-Secure-578123!', 'device_id':str(uuid.uuid4()), 'nonce':'x'*43, 'license_version':2}), content_type='application/json', secure=True).status_code, 403)

    def verified(self, name):
        user = get_user_model().objects.create_user(name, name, 'Synthetic-Other-716134!')
        CustomerProfile.objects.create(user=user, email_verified_at=timezone.now())
        return user

    def test_workspace_creation_idempotent_and_does_not_grant_paid_access(self):
        user = self.verified('synthetic-workspace@example.invalid')
        self.client.force_login(user)
        for name in ['Synthetic school', 'Replacement must not overwrite']:
            self.assertEqual(self.client.post('/account/products/facerecognition/create/', {'school_name':name}, secure=True).status_code, 302)
        school = School.objects.get(user=user)
        self.assertEqual(school.name, 'Synthetic school')
        self.assertFalse(school.entitled)
        self.assertEqual(AuditEvent.objects.filter(action='customer.workspace.created', school=school).count(), 1)

    def test_unverified_workspace_is_denied_and_school_id_input_ignored(self):
        other = self.verified('synthetic-other@example.invalid')
        victim = School.objects.create(user=other, name='Victim')
        user = get_user_model().objects.create_user('synthetic-unverified@example.invalid')
        CustomerProfile.objects.create(user=user)
        self.client.force_login(user)
        response = self.client.post('/account/products/facerecognition/create/', {'school_name':'Attacker', 'school_id':str(victim.pk)}, secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(School.objects.filter(user=user).exists())
        victim.refresh_from_db()
        self.assertEqual(victim.name, 'Victim')

    def test_company_profile_does_not_modify_school_identity(self):
        user = self.verified('synthetic-profile@example.invalid')
        school = School.objects.create(user=user, name='Original school')
        self.client.force_login(user)
        self.client.post('/account/profile/company/', {'display_name':'New display', 'user':999, 'is_staff':'on'}, secure=True)
        user.refresh_from_db(); school.refresh_from_db()
        self.assertEqual(user.nxfi_profile.display_name, 'New display')
        self.assertEqual(school.name, 'Original school')
        self.assertFalse(user.is_staff)
        self.client.post('/language/', {'language':'fa', 'next':'/account/'}, secure=True)
        user.nxfi_profile.refresh_from_db()
        self.assertEqual(user.nxfi_profile.preferred_language, 'fa')

    def test_owner_cannot_turn_its_account_into_a_school(self):
        from .models import CompanyOwner
        from .owner_auth import is_company_owner
        user = self.verified('synthetic-company-owner@example.invalid')
        CompanyOwner.objects.create(user=user)
        self.client.force_login(user)
        self.assertEqual(self.client.post('/account/products/facerecognition/create/', {'school_name':'Forbidden'}, secure=True).status_code, 403)
        self.assertTrue(is_company_owner(user))
        self.assertFalse(School.objects.filter(user=user).exists())
        customer = self.verified('synthetic-unassigned@example.invalid')
        self.assertContains(self.client.get('/owner/', secure=True), customer.email)
        self.client.force_login(customer)
        self.assertEqual(self.client.get('/owner/', secure=True).status_code, 403)

    def test_operator_product_copy_is_preserved_and_seed_summary_is_translated(self):
        from .models import Product
        from django.utils.translation import override
        product = Product(summary='حضور و غیاب تصویری مدارس با پردازش محلی و مجوز آفلاین')
        with override('en'):
            self.assertEqual(str(product.localized_summary), 'Local visual school attendance with offline licensing')
        with override('fa'):
            self.assertEqual(str(product.localized_summary), product.summary)
        product.summary = 'Operator-authored copy <script>not trusted</script>'
        self.assertEqual(product.localized_summary, product.summary)

    def test_migration_preserves_legacy_school_and_verification(self):
        user = get_user_model().objects.create_user('synthetic-legacy@example.invalid')
        verified = timezone.now()
        school = School.objects.create(user=user, name='Legacy', email_verified_at=verified)
        operation = import_module('service.migrations.0006_customerprofile').preserve_accounts
        with _Editor(connection) as editor:
            operation(apps, editor)
            operation(apps, editor)
        customer = CustomerProfile.objects.get(user=user)
        school.refresh_from_db()
        self.assertEqual(customer.email_verified_at, verified)
        self.assertEqual(customer.preferred_language, 'fa')
        self.assertEqual(school.name, 'Legacy')
        self.assertFalse(school.entitled)


class _Editor:
    def __init__(self, connection): self.connection = connection
    def __enter__(self): return self
    def __exit__(self, *args): pass
