"""A device losing authorization during streaming must not commit/evict backups."""
import base64
from datetime import timedelta
import hashlib
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import School, Device, Backup


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BackupAuthorizationAuditTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        setting = override_settings(BACKUP_ROOT=Path(self.directory.name), BACKUP_MASTER_KEY=base64.b64encode(b'a'*32).decode())
        setting.enable(); self.addCleanup(setting.disable)
        user = get_user_model().objects.create_user('synthetic-backup-audit@example.invalid')
        self.school = School.objects.create(user=user, name='Synthetic audit', status='active', student_limit=900,
            starts_at=timezone.now()-timedelta(days=1), expires_at=timezone.now()+timedelta(days=365))
        self.token = 'synthetic-test-only-token'
        self.device = Device.objects.create(school=self.school, installation_id=uuid.uuid4(), label='Synthetic',
            token_digest=hashlib.sha256(self.token.encode()).hexdigest(), password_stamp=user.get_session_auth_hash(),
            credential_expires_at=timezone.now()+timedelta(days=365))
    def test_revocation_or_token_rotation_during_upload_never_commits(self):
        from . import backups
        real_fsync = backups.os.fsync
        for mutation in ('revoked', 'token_digest'):
            with self.subTest(mutation=mutation):
                Device.objects.filter(pk=self.device.pk).update(revoked=False, token_digest=hashlib.sha256(self.token.encode()).hexdigest())
                triggered = []
                def change_authorization(fd):
                    if not triggered:
                        triggered.append(True)
                        Device.objects.filter(pk=self.device.pk).update(**{mutation: True if mutation=='revoked' else hashlib.sha256(b'new-token').hexdigest()})
                    real_fsync(fd)
                raw = b'SQLite format 3\x00' + b'synthetic-test-content' * 10
                with patch('service.backups.os.fsync', side_effect=change_authorization):
                    response = self.client.post('/api/v1/backups/upload/', data=raw, content_type='application/octet-stream', secure=True,
                        HTTP_AUTHORIZATION='Bearer '+self.token, HTTP_X_BACKUP_SHA256=hashlib.sha256(raw).hexdigest())
                self.assertEqual(response.status_code, 401)
                self.assertFalse(Backup.objects.exists())
                self.assertFalse(list(Path(self.directory.name).rglob('*.enc')))

    def test_failed_fresh_authorization_does_not_evict_last_recovery_backup(self):
        from . import backups
        self.school.backup_retention = 1
        self.school.save(update_fields=['backup_retention'])
        raw = b'SQLite format 3\x00' + b'synthetic-original' * 10
        def upload(value):
            return self.client.post('/api/v1/backups/upload/', data=value, content_type='application/octet-stream', secure=True,
                HTTP_AUTHORIZATION='Bearer '+self.token, HTTP_X_BACKUP_SHA256=hashlib.sha256(value).hexdigest())
        self.assertEqual(upload(raw).status_code, 201)
        original = Backup.objects.get()
        real_fsync = backups.os.fsync
        def revoke(fd):
            Device.objects.filter(pk=self.device.pk).update(revoked=True)
            real_fsync(fd)
        with patch('service.backups.os.fsync', side_effect=revoke):
            response = upload(raw+b'changed')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(list(Backup.objects.values_list('pk', flat=True)), [original.pk])
        self.assertTrue(backups.location(self.school.pk, original.blob_name).is_file())
