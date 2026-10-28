"""Versioned, streaming client-encrypted recovery bundles (school owns the key).

Includes a consistent SQLite snapshot (all settings and embeddings) and event
photos. Never includes credentials, license checkpoints or server/private keys.
Restore requires the application instance lock and stopped database users.
"""
import base64
from contextlib import closing
import json
import os
import re
from pathlib import Path, PurePosixPath

try:
    from ..localization import text as tr
except ImportError:
    from localization import text as tr
import shutil
import sqlite3
import tempfile
import uuid
import zipfile

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b'FRCLIENT2'
MAX_RECOVERY_BYTES = 2 * 1024 ** 3  # local decompressed safety ceiling, not cloud allowance


def sync_directory(path):
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def sync_tree(path):
    path = Path(path)
    for child in path.rglob('*'):
        if child.is_file():
            with child.open('rb') as stream:
                os.fsync(stream.fileno())
    for child in sorted((p for p in path.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        sync_directory(child)
    sync_directory(path)


def atomic_json(path, value):
    path = Path(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.private-')
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def export_key(path, school, key):
    # Never overwrite an existing recovery key accidentally.
    if len(key) != 32:
        raise ValueError(tr('Recovery key must be 32 bytes'))
    value = json.dumps({'version': 2, 'school_id': str(uuid.UUID(school)),
                        'key': base64.b64encode(key).decode()})
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.key-')
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        # Publish only the complete key, and never replace an existing key even
        # when a competing export creates it after our initial existence check.
        if os.name == 'nt':
            os.rename(temporary, path)
        else:
            os.link(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def import_key(path, school):
    with Path(path).open('rb') as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError(tr('Invalid recovery key'))
    value = json.loads(raw)
    key = base64.b64decode(value['key'], validate=True)
    if value['version'] != 2 or value['school_id'] != school or len(key) != 32:
        raise ValueError(tr('Recovery key belongs to another school or is invalid'))
    return key


def make_bundle(database, output, school, key):
    from .client import check_database
    database, output = Path(database), Path(output)
    if output.exists():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(prefix='.recovery-', dir=database.parent) as temporary:
        temporary = Path(temporary)
        snapshot = temporary / 'attendance.db'
        with closing(sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True)) as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target, pages=128, sleep=.01)
            check_database(target, {'school_id': school}, require_binding=True)
        archive = temporary / 'data.zip'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            z.write(snapshot, 'attendance.db')
            z.writestr('manifest.json', json.dumps({'version': 2, 'school_id': school}))
            total = sum(info.file_size for info in z.infolist())
            if total > MAX_RECOVERY_BYTES:
                raise ValueError(tr('Uncompressed database exceeds recovery safety limit'))
            photos = database.parent / 'early_exit_review'
            if photos.exists():
                if photos.is_symlink():
                    raise ValueError(tr('Photo directory cannot be a symlink'))
                for path in sorted(photos.rglob('*')):
                    if path.is_symlink():
                        raise ValueError(tr('Photo symlinks are not permitted'))
                    if path.is_file():
                        if not re.fullmatch(r'event_[0-9]+\.jpg', path.name) or path.parent != photos:
                            raise ValueError(tr('Unexpected event photo filename; preserve separately before backup'))
                        name = 'early_exit_review/' + path.relative_to(photos).as_posix()
                        z.write(path, name)
                        total += z.getinfo(name).file_size
                        if total > MAX_RECOVERY_BYTES or len(z.infolist()) > 100000:
                            raise ValueError(tr('Uncompressed recovery bundle exceeds safety limit'))
        if archive.stat().st_size > MAX_RECOVERY_BYTES:
            raise ValueError(tr('Recovery bundle exceeds local safety limit'))
        nonce = os.urandom(12)
        header = MAGIC + uuid.UUID(school).bytes + nonce
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(header)
        # Publish only a complete authenticated file, without overwriting a prior backup.
        fd, staging = tempfile.mkstemp(dir=output.parent, prefix='.backup-')
        try:
            with archive.open('rb') as source, os.fdopen(fd, 'wb') as destination:
                destination.write(header)
                for chunk in iter(lambda: source.read(65536), b''):
                    destination.write(encryptor.update(chunk))
                destination.write(encryptor.finalize() + encryptor.tag)
                destination.flush()
                os.fsync(destination.fileno())
            if os.name == 'nt':
                os.rename(staging, output)  # Windows rename never overwrites a destination.
            else:
                os.link(staging, output)  # Atomic exclusive publication on POSIX.
            sync_directory(output.parent)
        finally:
            Path(staging).unlink(missing_ok=True)
    return output.stat().st_size


def unpack_bundle(bundle, directory, school, key):
    from .client import check_database
    directory = Path(directory)
    archive = directory / 'archive.zip'
    with Path(bundle).open('rb') as source, archive.open('xb') as destination:
        os.chmod(archive, 0o600)
        header = source.read(len(MAGIC) + 16 + 12)
        if len(header) != len(MAGIC) + 28 or header[:len(MAGIC)] != MAGIC or header[len(MAGIC):len(MAGIC)+16] != uuid.UUID(school).bytes:
            raise ValueError(tr('Invalid bundle or school mismatch'))
        source.seek(-16, os.SEEK_END)
        end = source.tell()
        tag = source.read(16)
        if end < len(header) or end > MAX_RECOVERY_BYTES:
            raise ValueError(tr('Invalid bundle size'))
        source.seek(len(header))
        decryptor = Cipher(algorithms.AES(key), modes.GCM(header[-12:], tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        while source.tell() < end:
            destination.write(decryptor.update(source.read(min(65536, end - source.tell()))))
        destination.write(decryptor.finalize())  # No extraction before authentication.
    with zipfile.ZipFile(archive) as z:
        names, normalized, size = set(), set(), 0
        for info in z.infolist():
            name = PurePosixPath(info.filename)
            size += info.file_size
            if (info.filename.casefold() in normalized or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', part) or part.endswith(('.', ' ')) or part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(1, 10)), *('LPT'+str(i) for i in range(1, 10))} for part in name.parts) or '\\' in info.filename or name.is_absolute() or '..' in name.parts
                    or not (info.filename in ('attendance.db', 'manifest.json') or
                            (name.parts and name.parts[0] == 'early_exit_review' and len(name.parts) > 1))
                    or info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000
                    or size > MAX_RECOVERY_BYTES or len(names) > 100000):
                raise ValueError(tr('Unsafe or oversized recovery archive'))
            names.add(info.filename)
            normalized.add(info.filename.casefold())
        if not {'attendance.db', 'manifest.json'} <= names or z.getinfo('manifest.json').file_size > 4096:
            raise ValueError(tr('Missing manifest/database'))
        manifest = json.loads(z.read('manifest.json'))
        if manifest != {'version': 2, 'school_id': school}:
            raise ValueError(tr('Recovery manifest mismatch'))
        for name in names:
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with z.open(name) as src, target.open('xb') as dst:
                os.chmod(target, 0o600)
                shutil.copyfileobj(src, dst, 65536)
    with closing(sqlite3.connect(f'{(directory / "attendance.db").resolve().as_uri()}?mode=ro', uri=True)) as connection:
        check_database(connection, {'school_id': school}, require_binding=True)


def recover_interrupted_restore(database):
    """Replay a local rollback journal before opening SQLite/cameras. Idempotent."""
    database = Path(database)
    journal = database.parent / '.restore-journal.json'
    if not journal.exists():
        return
    value = json.loads(journal.read_text())
    name = value['rollback']
    if not name.startswith('before-restore-') or Path(name).name != name:
        raise ValueError(tr('Invalid restore journal; operator recovery required'))
    old = database.parent / name
    if not value.get('committed'):
        if (old / 'attendance.db').exists():
            # Copy through an atomic staging file so repeated crash recovery remains safe.
            fd, temporary = tempfile.mkstemp(dir=database.parent, prefix='.rollback-')
            os.close(fd)
            try:
                shutil.copyfile(old / 'attendance.db', temporary)
                with open(temporary, 'rb') as stream:
                    os.fsync(stream.fileno())
                for suffix in ('-wal', '-shm'):
                    Path(str(database) + suffix).unlink(missing_ok=True)
                os.replace(temporary, database)
            finally:
                Path(temporary).unlink(missing_ok=True)
        photos = database.parent / 'early_exit_review'
        if (old / 'photos-moved').exists():
            if photos.exists():
                shutil.rmtree(photos)
            if (old / 'early_exit_review').exists():
                shutil.copytree(old / 'early_exit_review', photos)
    sync_tree(database.parent / name)
    if (database.parent / 'early_exit_review').exists():
        sync_tree(database.parent / 'early_exit_review')
    sync_directory(database.parent)
    journal.unlink()
    sync_directory(database.parent)


def install_bundle(directory, database):
    """Crash-recoverable DB + photo replacement; retain the old generation locally."""
    database, directory = Path(database), Path(directory)
    recover_interrupted_restore(database)
    old = database.parent / ('before-restore-' + uuid.uuid4().hex)
    old.mkdir(mode=0o700)
    photos = database.parent / 'early_exit_review'
    journal = database.parent / '.restore-journal.json'
    value = {'rollback': old.name, 'committed': False}
    if database.exists():
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(old / 'attendance.db')) as target:
            source.backup(target)
            if source.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                raise ValueError(tr('Database busy; stop all application processes'))
        with (old / 'attendance.db').open('rb') as stream:
            os.fsync(stream.fileno())
    # Copy photos before mutation: a disk-full failure leaves the current installation intact.
    if photos.exists():
        shutil.copytree(photos, old / 'early_exit_review')
    (old / 'photos-moved').touch()
    sync_tree(old)
    sync_directory(database.parent)
    atomic_json(journal, value)
    try:
        if photos.exists():
            shutil.rmtree(photos)
        if (directory / 'early_exit_review').exists():
            os.replace(directory / 'early_exit_review', photos)
        for suffix in ('-wal', '-shm'):
            Path(str(database) + suffix).unlink(missing_ok=True)
        os.replace(directory / 'attendance.db', database)
        with database.open('rb') as stream:
            os.fsync(stream.fileno())
        if photos.exists():
            sync_tree(photos)
        sync_directory(database.parent)
        value['committed'] = True
        atomic_json(journal, value)
    except BaseException:
        recover_interrupted_restore(database)
        raise
    journal.unlink()
