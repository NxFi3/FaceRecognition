"""Real server/client contract check using ONLY disposable in-memory server data."""
import base64
from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import urlparse
from unittest.mock import patch


def main():
    if os.environ.get('SERVICE_TESTING') != '1' or os.environ.get('SERVICE_TEST_DATABASE_URL'):
        raise SystemExit('Run with SERVICE_TESTING=1 and WITHOUT SERVICE_TEST_DATABASE_URL; never use production settings')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'service.settings')
    import django
    django.setup()
    from django.conf import settings
    if settings.DATABASES['default']['NAME'] != ':memory:':
        raise SystemExit('Refusing anything other than an in-memory server database')
    from django.core.management import call_command
    from django.test import Client, override_settings
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import requests
    from src.cloud.client import CloudClient, ClientConfig, AccessDenied
    from src import database
    from service.models import School, Device
    call_command('migrate', verbosity=0)
    signer = Ed25519PrivateKey.generate()
    private = signer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    class Vault:
        def __init__(self):
            self.values = {}
        def get(self, key):
            return self.values.get(key)
        def set(self, key, value):
            self.values[key] = value
            return True
        def delete(self, key):
            self.values.pop(key, None)
    class Response:
        def __init__(self, response):
            self.status_code = response.status_code
            self.headers = response.headers
            self.raw = b''.join(response.streaming_content) if response.streaming else response.content
        def iter_content(self, size):
            for start in range(0, len(self.raw), size):
                yield self.raw[start:start+size]
        def close(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    http = Client()
    def transport(method, url, **kwargs):
        headers = dict(kwargs['headers'])
        content_type = headers.pop('Content-Type', 'application/json')
        payload = json.dumps(kwargs['json']).encode() if 'json' in kwargs else kwargs.get('data', b'')
        if hasattr(payload, 'read'):
            payload = payload.read()
        return Response(http.generic(method, urlparse(url).path, data=payload, content_type=content_type, secure=True, headers=headers))
    with tempfile.TemporaryDirectory(prefix='offline-contract-') as directory:
        root = Path(directory)
        with override_settings(SIGNING_PRIVATE_KEY=base64.b64encode(private).decode(), BACKUP_MASTER_KEY=base64.b64encode(os.urandom(32)).decode(), BACKUP_ROOT=root / 'server-blobs', PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher']):
            user = get_user_model().objects.create_user('contract@example.com', password='Test-only-password!987')
            school = School.objects.create(user=user, name='Synthetic contract school', status='active', student_limit=900, starts_at=timezone.now()-timedelta(days=1), expires_at=timezone.now()+timedelta(days=365))
            data = root / 'school'
            data.mkdir()
            db = data / 'attendance.db'
            with patch.object(database, 'DB_PATH', db):
                database.init_db()
                database.add_student('synthetic-1', 'Synthetic')
            photos = data / 'early_exit_review'
            photos.mkdir()
            (photos / 'event_1.jpg').write_bytes(b'synthetic evidence')
            vault = Vault()
            config = ClientConfig('https://testserver', public)
            client = CloudClient(config, data / 'cloud-state.json', store=vault, transport=transport)
            client.login(user.username, 'Test-only-password!987')
            client.bind_database(db)
            client.configure_backup_key(root / 'school.frkey')
            uploaded = client.backup(db)
            assert uploaded['format'] == 'client-v2'
            def disconnected(*args, **kwargs):
                raise requests.ConnectionError('offline')
            offline = CloudClient(config, data / 'cloud-state.json', store=vault, transport=disconnected)
            assert offline.allowed()
            try:
                offline.renew()
            except Exception:
                pass
            assert offline.allowed()
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute("INSERT INTO students(student_code,name) VALUES('later','Later')")
            (photos / 'event_1.jpg').write_bytes(b'later image')
            client.restore(uploaded['id'], db)
            with closing(sqlite3.connect(db)) as connection:
                assert connection.execute('SELECT COUNT(*) FROM students').fetchone()[0] == 1
            assert (photos / 'event_1.jpg').read_bytes() == b'synthetic evidence'
            for year in (2, 3, 4):
                school.expires_at = timezone.now()+timedelta(days=365*year)
                school.save()
                client.renew()
                assert client.allowed()
            school.expires_at = timezone.now()-timedelta(seconds=1)
            school.save()
            try:
                client.renew()
                raise AssertionError('Expired subscription admitted')
            except AccessDenied:
                pass
            school.expires_at = timezone.now()+timedelta(days=365)
            school.save()
            client.renew()
            assert client.allowed()
            Device.objects.update(revoked=True)
            try:
                client.renew()
                raise AssertionError('Revoked installation admitted')
            except AccessDenied:
                pass
            assert not CloudClient(config, data / 'cloud-state.json', store=vault).allowed()
    print('PASS: real signed activation, offline restart/failure, encrypted upload/download/full restore, three renewals, expiry recovery, durable revocation')


if __name__ == '__main__':
    main()
