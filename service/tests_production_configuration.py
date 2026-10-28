"""Synthetic subprocess checks; no database/network connection or real secrets."""
import base64
import json
import os
import subprocess
import sys
from django.test import SimpleTestCase


class ProductionConfigurationTests(SimpleTestCase):
    def settings_process(self, **changes):
        environment = dict(os.environ)
        for name in ('SERVICE_TESTING', 'SERVICE_TEST_DATABASE_URL', 'DJANGO_SETTINGS_MODULE', 'NXFI_LOCAL_DEVELOPMENT'):
            environment.pop(name, None)
        environment.update({
            'DJANGO_SECRET_KEY': 'synthetic-test-only-key-' * 3,
            'PUBLIC_URL': 'https://company.example.invalid',
            'DATABASE_URL': 'postgresql://synthetic_user:synthetic_password@db.invalid/synthetic_db',
            'REDIS_URL': 'redis://cache.invalid:6379/0',
            'LICENSE_SIGNING_KEY': base64.b64encode(b's' * 32).decode(),
            'BACKUP_MASTER_KEY': base64.b64encode(b'b' * 32).decode(),
        })
        environment.update(changes)
        program = """
import json
from service import settings
print(json.dumps({'database': settings.DATABASES['default']['NAME'],
                  'host': settings.DATABASES['default']['HOST'],
                  'options': settings.DATABASES['default'].get('OPTIONS', {}),
                  'cache': settings.CACHES['default']['LOCATION'],
                  'session_secure': settings.SESSION_COOKIE_SECURE,
                  'csrf_secure': settings.CSRF_COOKIE_SECURE,
                  'ssl_redirect': settings.SECURE_SSL_REDIRECT}))
"""
        return subprocess.run([sys.executable, '-c', program], env=environment,
                              capture_output=True, text=True, timeout=10)

    def assert_rejected_without_secrets(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('synthetic_password', result.stderr)
        self.assertNotIn('synthetic-test-only-key', result.stderr)
        self.assertNotIn('postgresql://', result.stderr)

    def test_production_database_cannot_be_redirected_by_test_database_url(self):
        result = self.settings_process(SERVICE_TEST_DATABASE_URL='postgresql://test_user:test_password@test.invalid/test_db')
        self.assert_rejected_without_secrets(result)
        self.assertIn('forbidden outside isolated', result.stderr)

    def test_explicit_postgres_certificate_verification_options_are_preserved(self):
        value = 'postgresql://synthetic_user:synthetic_password@db.invalid/synthetic_db?sslmode=verify-full&sslrootcert=%2Frun%2Fdb-ca.pem&connect_timeout=7'
        result = self.settings_process(DATABASE_URL=value)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['options'], {'sslmode': 'verify-full', 'sslrootcert': '/run/db-ca.pem', 'connect_timeout': 7})
        self.assertTrue(value['session_secure'])
        self.assertTrue(value['csrf_secure'])
        self.assertTrue(value['ssl_redirect'])

    def test_invalid_or_ignored_postgres_options_and_missing_endpoint_fail_closed(self):
        values = ('postgresql:///database', 'postgresql://user:password@db.invalid/',
                  'postgresql://user:password@db.invalid:invalid/database',
                  'postgresql://user:password@db.invalid:0/database',
                  'postgresql://user:password@db.invalid/database?sslmode=verify-full&sslmode=disable',
                  'postgresql://user:password@db.invalid/database?sslmode=invalid',
                  'postgresql://user:password@db.invalid/database?sslmode=',
                  'postgresql://user:password@db.invalid/database?unsupported=value',
                  'postgresql://user:password@db.invalid/database?connect_timeout=0')
        for value in values:
            with self.subTest(value=value):
                self.assert_rejected_without_secrets(self.settings_process(DATABASE_URL=value))

    def test_invalid_https_origin_and_empty_userinfo_are_rejected(self):
        for value in ('https://@company.invalid', 'https://company.invalid:invalid',
                      'https://company.invalid:99999', 'https://company.invalid:0', 'https://company.invalid\n',
                      'https://company.invalid?query=1', 'http://company.invalid'):
            with self.subTest(value=value):
                self.assert_rejected_without_secrets(self.settings_process(PUBLIC_URL=value))

    def test_shared_cache_requires_valid_explicit_location(self):
        for value in ('', 'http://cache.invalid', 'redis://', 'redis://cache.invalid:invalid/0',
                      'redis://cache.invalid:0/0', 'redis://cache.invalid/invalid', 'redis://cache.invalid/0#fragment'):
            with self.subTest(value=value):
                self.assert_rejected_without_secrets(self.settings_process(REDIS_URL=value))

    def test_private_compose_style_database_defaults_still_work(self):
        result = self.settings_process()
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['database'], 'synthetic_db')
        self.assertEqual(value['host'], 'db.invalid')
        self.assertEqual(value['options'], {})

    def test_existing_redis_connection_and_tls_options_are_forwarded_unchanged(self):
        location = 'rediss://cache.invalid:6380/0?socket_connect_timeout=3&ssl_cert_reqs=required'
        result = self.settings_process(REDIS_URL=location)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['cache'], location)
