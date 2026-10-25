"""Guard local-only settings against accidental production/database use."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from django.test import SimpleTestCase


class LocalDevelopmentGuardTests(SimpleTestCase):
    def run_settings(self, **changes):
        environment = dict(os.environ)
        for key in ('NXFI_LOCAL_DEVELOPMENT', 'DATABASE_URL', 'SERVICE_TEST_DATABASE_URL'):
            environment.pop(key, None)
        environment.update(changes)
        program = """
import json
import service.dev_settings as value
print(json.dumps({'hosts': value.ALLOWED_HOSTS, 'ssl_redirect': value.SECURE_SSL_REDIRECT,
                  'session_secure': value.SESSION_COOKIE_SECURE,
                  'database': str(value.DATABASES['default']['NAME']),
                  'engine': value.DATABASES['default']['ENGINE']}))
"""
        return subprocess.run([sys.executable, '-c', program], env=environment,
                              capture_output=True, text=True, timeout=10)

    def test_development_settings_require_explicit_opt_in(self):
        result = self.run_settings()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('explicit NXFI_LOCAL_DEVELOPMENT=1', result.stderr)

    def test_development_settings_reject_existing_database_urls(self):
        for key in ('DATABASE_URL', 'SERVICE_TEST_DATABASE_URL'):
            with self.subTest(key=key):
                result = self.run_settings(NXFI_LOCAL_DEVELOPMENT='1', **{key: 'postgresql://not-used.invalid/private'})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Unset database URLs', result.stderr)
                self.assertNotIn('postgresql://', result.stderr)

    def test_opted_in_profile_uses_isolated_sqlite_and_loopback_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.db'
            result = self.run_settings(NXFI_LOCAL_DEVELOPMENT='1', NXFI_DEV_DATABASE=str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value['database'], str(path))
            self.assertEqual(value['engine'], 'django.db.backends.sqlite3')
            self.assertEqual(value['hosts'], ['127.0.0.1', 'localhost', '[::1]'])
            self.assertFalse(value['ssl_redirect'])
            self.assertFalse(value['session_secure'])
