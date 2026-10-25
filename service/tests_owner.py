"""Security regressions: explicit owner, least privilege and password step-up."""
from io import StringIO
import os
import subprocess
import sys
import time
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import path

from .models import School
from .owner_auth import (STEP_UP_SESSION_KEY, is_company_owner, owner_reauthenticate,
                         owner_required)
from .owner_models import CompanyOwner


@owner_required
def owner_read(request):
    return HttpResponse('owner')


@owner_required(recent=True)
def owner_sensitive(request):
    return HttpResponse('sensitive')


urlpatterns = [path('admin/', admin.site.urls), path('owner/', owner_read),
               path('owner/sensitive/', owner_sensitive),
               path('owner/reauth/', owner_reauthenticate)]
from .urls import urlpatterns as project_patterns
urlpatterns += [entry for entry in project_patterns if getattr(entry, 'namespace', None) != 'admin']
PASSWORD = 'Owner-test-password!42'


@override_settings(ROOT_URLCONF=__name__, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class OwnerSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user('owner@example.com', password=PASSWORD, is_staff=True)
        self.company = CompanyOwner.objects.create(id=1, user=self.owner)

    def login_owner(self):
        self.client.force_login(self.owner)

    def reauthenticate(self, password=PASSWORD, target='/owner/sensitive/'):
        return self.client.post('/owner/reauth/', {'password': password, 'next': target}, secure=True)

    def test_staff_superuser_and_school_never_implicitly_become_owner(self):
        for flags in ({'is_staff': True}, {'is_staff': True, 'is_superuser': True}, {}):
            user = get_user_model().objects.create_user('user'+str(get_user_model().objects.count()), password=PASSWORD, **flags)
            if not flags:
                School.objects.create(user=user, name='Synthetic school')
            self.assertFalse(is_company_owner(user))
            self.client.force_login(user)
            self.assertEqual(self.client.get('/owner/', secure=True).status_code, 403)
            self.assertEqual(self.client.get('/owner/reauth/', secure=True).status_code, 403)
            self.assertEqual(self.client.get('/admin/', secure=True).status_code, 302)
        self.assertTrue(is_company_owner(self.owner))

    def test_step_up_protects_sensitive_routes_and_admin_until_valid_password(self):
        self.login_owner()
        self.assertEqual(self.client.get('/owner/', secure=True).status_code, 200)
        self.assertEqual(self.client.post('/owner/sensitive/', secure=True).status_code, 302)
        self.assertEqual(self.client.get('/admin/', secure=True).status_code, 302)
        self.assertEqual(self.reauthenticate('wrong').status_code, 200)
        self.assertEqual(self.client.post('/owner/sensitive/', secure=True).status_code, 302)
        old_session = self.client.session.session_key
        self.assertEqual(self.reauthenticate().status_code, 302)
        self.assertNotEqual(self.client.session.session_key, old_session)
        self.assertEqual(self.client.post('/owner/sensitive/', secure=True).status_code, 200)
        self.assertEqual(self.client.get('/admin/', secure=True).status_code, 200)
        self.assertNotIn('/admin/auth/user/', self.client.get('/admin/', secure=True).content.decode())
        self.assertEqual(self.client.get('/admin/service/school/', secure=True).status_code, 200)
        self.assertEqual(self.client.get('/admin/auth/user/', secure=True).status_code, 403)

    def test_step_up_expires_and_rejects_wrong_revision_future_time(self):
        self.login_owner()
        self.reauthenticate()
        valid = dict(self.client.session[STEP_UP_SESSION_KEY])
        for change in ({'issued': time.time()-301}, {'issued': time.time()+100}, {'revision': 99}, {'auth': 'forged'}):
            session = self.client.session
            session[STEP_UP_SESSION_KEY] = dict(valid, **change)
            session.save()
            self.assertEqual(self.client.post('/owner/sensitive/', secure=True).status_code, 302)
        session = self.client.session
        session[STEP_UP_SESSION_KEY] = valid
        session.save()
        self.assertEqual(self.client.post('/owner/sensitive/', secure=True).status_code, 200)

    def test_owner_replacement_and_disabled_owner_revoke_existing_session(self):
        self.login_owner()
        self.reauthenticate()
        other = get_user_model().objects.create_user('next@example.com', password=PASSWORD, is_staff=True)
        self.company.user = other
        self.company.revision += 1
        self.company.save()
        self.assertEqual(self.client.post('/owner/sensitive/', secure=True).status_code, 403)
        self.assertEqual(self.client.get('/admin/', secure=True).status_code, 302)
        self.client.force_login(other)
        other.is_active = False
        other.save()
        self.assertFalse(is_company_owner(other))
        self.assertNotEqual(self.client.get('/owner/', secure=True).status_code, 200)

    def test_csrf_and_open_redirect_protection(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.owner)
        self.assertEqual(strict.post('/owner/reauth/', {'password': PASSWORD}, secure=True).status_code, 403)
        self.login_owner()
        response = self.reauthenticate(target='https://attacker.invalid/steal')
        self.assertEqual(response['Location'], '/owner/')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn("frame-ancestors 'none'", response['Content-Security-Policy'])

    def test_bruteforce_and_unavailable_rate_limit_fail_closed(self):
        self.login_owner()
        for _ in range(5):
            self.assertEqual(self.reauthenticate('wrong').status_code, 200)
        self.assertEqual(self.reauthenticate().status_code, 429)
        with patch('service.owner_auth.throttled', side_effect=ConnectionError('unavailable')):
            self.assertEqual(self.reauthenticate().status_code, 503)
        self.assertNotIn(STEP_UP_SESSION_KEY, self.client.session)

    def test_generic_superuser_admin_login_is_rejected_even_with_correct_password(self):
        user = get_user_model().objects.create_superuser('root@example.com', password=PASSWORD)
        response = self.client.post('/admin/login/', {'username': user.username, 'password': PASSWORD, 'next': '/admin/'}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_owner_admin_password_login_counts_as_recent_authentication(self):
        response = self.client.post('/admin/login/', {'username': self.owner.username, 'password': PASSWORD, 'next': '/admin/'}, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/admin/', secure=True).status_code, 200)

    def test_database_constraint_prevents_a_second_owner(self):
        other = get_user_model().objects.create_user('second@example.com', password=PASSWORD)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CompanyOwner.objects.create(id=2, user=other)

    def test_school_cannot_hold_owner_authority_even_if_record_is_misconfigured(self):
        School.objects.create(user=self.owner, name='Misconfigured owner school')
        self.assertFalse(is_company_owner(self.owner))
        self.login_owner()
        self.assertEqual(self.client.get('/owner/', secure=True).status_code, 403)

    def test_command_refuses_implicit_or_school_provision_and_requires_replacement_auth(self):
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('provision_owner', email='second@example.com', stdout=output)
        with patch('service.management.commands.provision_owner.getpass', return_value='wrong'):
            with self.assertRaises(CommandError):
                call_command('provision_owner', email='second@example.com', replace=True, stdout=output)
        school = get_user_model().objects.create_user('school@example.com', password=PASSWORD)
        School.objects.create(user=school, name='School')
        with patch('service.management.commands.provision_owner.getpass', return_value=PASSWORD):
            with self.assertRaises(CommandError):
                call_command('provision_owner', email=school.username, replace=True, stdout=output)
        self.assertEqual(CompanyOwner.objects.get(pk=1).user_id, self.owner.pk)

    def test_explicit_replacement_creates_minimal_owner_and_logs_no_password(self):
        output = StringIO()
        new_password = 'Separate-strong-owner-password!753'
        with patch('service.management.commands.provision_owner.getpass', side_effect=[PASSWORD, new_password, new_password]):
            call_command('provision_owner', email='new-owner@example.com', replace=True, stdout=output)
        owner = CompanyOwner.objects.get(pk=1).user
        self.assertEqual(owner.username, 'new-owner@example.com')
        self.assertTrue(owner.is_staff)
        self.assertFalse(owner.is_superuser)
        self.assertTrue(is_company_owner(owner))
        self.assertFalse(is_company_owner(self.owner))
        self.assertNotIn(new_password, output.getvalue())

    def test_first_provision_needs_no_preexisting_staff_or_superuser(self):
        CompanyOwner.objects.all().delete()
        output = StringIO()
        with patch('service.management.commands.provision_owner.getpass', side_effect=[PASSWORD, PASSWORD]):
            call_command('provision_owner', email='first-owner@example.com', stdout=output)
        owner = CompanyOwner.objects.get(pk=1).user
        self.assertTrue(owner.is_staff)
        self.assertFalse(owner.is_superuser)
        self.assertTrue(is_company_owner(owner))


class PublicOriginConfigurationTests(TestCase):
    def test_password_only_userinfo_query_fragment_and_path_are_rejected(self):
        for origin in ('https://:password@company.invalid', 'https://company.invalid?unsafe=1',
                       'https://company.invalid#fragment', 'https://company.invalid/path'):
            with self.subTest(origin=origin):
                environment = dict(os.environ, SERVICE_TESTING='1', PUBLIC_URL=origin)
                result = subprocess.run([sys.executable, '-c', 'import service.settings'], env=environment, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('PUBLIC_URL must be an HTTPS origin', result.stderr)
