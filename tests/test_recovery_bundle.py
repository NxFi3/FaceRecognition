from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from cryptography.exceptions import InvalidTag
from src import database
from src.cloud import recovery
from src.cloud.entitlement import bind_gate
from src.cloud.local_export import export_attendance


class RecoveryBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root / 'attendance.db'
        dbpatch = patch.object(database, 'DB_PATH', self.db)
        dbpatch.start()
        self.addCleanup(dbpatch.stop)
        bind_gate(None)
        database.init_db()
        self.school = str(uuid.uuid4())
        database.set_setting('cloud_school_id', self.school)
        self.key = b'k' * 32
        self.student = database.add_student('123', '=Spreadsheet formula')
        database.mark_attendance(self.student)
        self.photos = self.root / 'early_exit_review'
        self.photos.mkdir()
        (self.photos / 'event_1.jpg').write_bytes(b'private-image-content')
        self.bundle = self.root / 'school.frbackup'
        recovery.make_bundle(self.db, self.bundle, self.school, self.key)
    def unpack(self, key=None, school=None):
        directory = self.root / ('extract-' + uuid.uuid4().hex)
        directory.mkdir()
        recovery.unpack_bundle(self.bundle, directory, school or self.school, key or self.key)
        return directory
    def test_client_encryption_and_complete_restore_preserve_settings_and_photos(self):
        ciphertext = self.bundle.read_bytes()
        self.assertNotIn(b'private-image-content', ciphertext)
        self.assertNotIn(b'SQLite format', ciphertext)
        directory = self.unpack()
        database.add_student('later', 'Later record')
        (self.photos / 'event_1.jpg').write_bytes(b'new image')
        recovery.install_bundle(directory, self.db)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM students').fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT value FROM app_settings WHERE key='cloud_school_id'").fetchone()[0], self.school)
        self.assertEqual((self.photos / 'event_1.jpg').read_bytes(), b'private-image-content')
        self.assertEqual(len(list(self.root.glob('before-restore-*'))), 1)
    def test_wrong_key_school_corruption_leave_existing_database_intact(self):
        for kwargs in ({'key': b'x' * 32}, {'school': str(uuid.uuid4())}):
            with self.assertRaises((ValueError, InvalidTag)):
                self.unpack(**kwargs)
        value = bytearray(self.bundle.read_bytes())
        value[-20] ^= 1
        self.bundle.write_bytes(value)
        with self.assertRaises(InvalidTag):
            self.unpack()
        self.assertTrue(database.get_student(self.student))
    def test_recovery_keys_are_school_bound_and_never_overwritten(self):
        path = self.root / 'school.frkey'
        recovery.export_key(path, self.school, self.key)
        self.assertEqual(recovery.import_key(path, self.school), self.key)
        with self.assertRaises(FileExistsError):
            recovery.export_key(path, self.school, b'x' * 32)
        with self.assertRaises(ValueError):
            recovery.import_key(path, str(uuid.uuid4()))
    def test_failed_restore_rolls_back_both_database_and_photos(self):
        directory = self.unpack()
        database.add_student('later', 'Later record')
        (self.photos / 'event_1.jpg').write_bytes(b'current image')
        real_replace = recovery.os.replace
        def fail_database(source, target):
            if Path(source) == directory / 'attendance.db':
                raise OSError('disk full')
            return real_replace(source, target)
        with patch.object(recovery.os, 'replace', side_effect=fail_database):
            with self.assertRaises(OSError):
                recovery.install_bundle(directory, self.db)
        self.assertEqual(len(database.list_students()), 2)
        self.assertEqual((self.photos / 'event_1.jpg').read_bytes(), b'current image')
        self.assertFalse((self.root / '.restore-journal.json').exists())
    def test_crash_journal_recovered_before_database_reopen(self):
        directory = self.unpack()
        import subprocess
        import sys
        database.add_student('later', 'Later record')
        program = """
import os, sys
from src.cloud import recovery
original = recovery.atomic_json
def crash(path, value):
    if value.get('committed'):
        os._exit(77)
    original(path, value)
recovery.atomic_json = crash
recovery.install_bundle(sys.argv[1], sys.argv[2])
"""
        result = subprocess.run([sys.executable, '-c', program, str(directory), str(self.db)], timeout=15)
        self.assertEqual(result.returncode, 77)
        self.assertTrue((self.root / '.restore-journal.json').exists())
        self.assertEqual(len(database.list_students()), 1)
        recovery.recover_interrupted_restore(self.db)
        self.assertEqual(len(database.list_students()), 2)
    def test_csv_export_is_offline_and_prevents_spreadsheet_formula_injection(self):
        path = self.root / 'attendance.csv'
        export_attendance(self.db, path)
        self.assertIn("'=Spreadsheet formula", path.read_text(encoding='utf-8-sig'))
    def test_photos_symlink_refused_without_upload(self):
        path = self.photos / 'linked.jpg'
        try:
            path.symlink_to(self.bundle)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        with self.assertRaises(ValueError):
            recovery.make_bundle(self.db, self.root / 'unsafe.frbackup', self.school, self.key)

    def test_client_bundle_rejects_unsafe_windows_zip_paths(self):
        import io
        import os
        import zipfile
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        for unsafe in ('../escape.db', 'early_exit_review/C:/escape.jpg', 'early_exit_review/NUL.jpg', 'early_exit_review/../../escape.jpg'):
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('manifest.json', json.dumps({'version': 2, 'school_id': self.school}))
                z.writestr('attendance.db', self.db.read_bytes())
                z.writestr(unsafe, b'unsafe')
            nonce = os.urandom(12)
            header = recovery.MAGIC + uuid.UUID(self.school).bytes + nonce
            cipher = Cipher(algorithms.AES(self.key), modes.GCM(nonce)).encryptor()
            cipher.authenticate_additional_data(header)
            self.bundle.write_bytes(header + cipher.update(archive.getvalue()) + cipher.finalize() + cipher.tag)
            with self.subTest(path=unsafe), self.assertRaises(ValueError):
                self.unpack()

    def test_backup_output_is_not_published_on_write_failure_or_overwritten(self):
        original = self.bundle.read_bytes()
        with self.assertRaises(FileExistsError):
            recovery.make_bundle(self.db, self.bundle, self.school, self.key)
        self.assertEqual(self.bundle.read_bytes(), original)
        destination = self.root / 'new.frbackup'
        with patch.object(recovery.os, 'fsync', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                recovery.make_bundle(self.db, destination, self.school, self.key)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob('.backup-*')))
