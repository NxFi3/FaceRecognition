"""Explicit loopback development only. Never use this module on a public host.

NXFI_LOCAL_DEVELOPMENT=1 DJANGO_SETTINGS_MODULE=service.dev_settings
python -m service.manage runserver 127.0.0.1:8000

Uses synthetic business data, console email and no production secrets. Desktop
license signing is intentionally not configured: use the isolated license test
or a properly configured HTTPS staging service for desktop activation.
"""
import os
from pathlib import Path

if os.environ.get('NXFI_LOCAL_DEVELOPMENT') != '1':
    raise RuntimeError('Local development requires explicit NXFI_LOCAL_DEVELOPMENT=1')
os.environ['SERVICE_TESTING'] = '1'
# A production/test PostgreSQL URL must not accidentally redirect this dev profile.
if os.environ.get('SERVICE_TEST_DATABASE_URL') or os.environ.get('DATABASE_URL'):
    raise RuntimeError('Unset database URLs before using isolated local development')

from .settings import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '[::1]']
PUBLIC_URL = 'http://127.0.0.1:8000'
CSRF_TRUSTED_ORIGINS = ['http://127.0.0.1:8000', 'http://localhost:8000']
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
DEV_DATABASE = Path(os.environ.get('NXFI_DEV_DATABASE', str(BASE_DIR.parent / 'data' / 'commercial-dev.db')))  # noqa: F405
DEV_DATABASE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': DEV_DATABASE}}
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
# Dedicated local locations; never mount real school backup data in this profile.
BACKUP_ROOT = DEV_DATABASE.parent / 'development-backups'
RELEASE_ROOT = DEV_DATABASE.parent / 'development-releases'

LANGUAGE_COOKIE_SECURE = False
