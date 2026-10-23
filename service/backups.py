"""Tenant-bound streaming encryption; no client filenames used on disk."""
import hashlib
from pathlib import Path
import os
import secrets
import tempfile
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.http import FileResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
import base64
from .api import device_auth, error
from .models import AuditEvent, Backup, School
from .security import throttled

MAGIC = b"FRBACKUP1"
SQLITE_HEADER = b"SQLite format 3\x00"


def school_key(school_id):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=str(school_id).encode(), info=b"face-backup-v1").derive(base64.b64decode(settings.BACKUP_MASTER_KEY, validate=True))


def location(school_id, blob_name):
    # Defense in depth for maintenance scripts / tampered metadata.
    if len(blob_name) != 36 or not blob_name.endswith(".enc"):
        raise ValueError("Invalid blob name")
    uuid.UUID(hex=blob_name[:-4])
    return settings.BACKUP_ROOT / str(uuid.UUID(str(school_id))) / blob_name


def describe(backup):
    return {"id": str(backup.pk), "size": backup.size, "sha256": backup.sha256, "created_at": backup.created_at.isoformat(), "format": backup.format}


@csrf_exempt
@require_POST
@device_auth()
def upload(request):
    if not request.school.backups_enabled:
        return error("backups_disabled")
    if throttled(request, "backup-upload", str(request.school.pk), limit=12, seconds=3600):
        return error("rate_limited", 429)
    try:
        length = int(request.headers.get("Content-Length", "0"))
    except ValueError:
        return error("invalid_length", 400)
    if request.content_type != "application/octet-stream" or not 16 <= length <= settings.MAX_BACKUP_BYTES:
        return error("backup_too_large_or_invalid", 413)
    expected = request.headers.get("X-Backup-SHA256", "")
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        return error("invalid_digest", 400)
    backup_format = request.headers.get('X-Backup-Format', 'legacy-sqlite')
    if backup_format not in ('legacy-sqlite', 'client-v2'):
        return error('invalid_backup_format', 400)
    directory = settings.BACKUP_ROOT / str(request.school.pk)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    final = None
    committed = False
    try:
        digest = hashlib.sha256()
        count = 0
        nonce = secrets.token_bytes(12)
        encryptor = Cipher(algorithms.AES(school_key(request.school.pk)), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(str(request.school.pk).encode())
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".upload-", delete=False) as output:
            temporary = Path(output.name)
            output.write(MAGIC + nonce)
            while True:
                chunk = request.read(65536)
                if not chunk:
                    break
                if count == 0 and not chunk.startswith(SQLITE_HEADER if backup_format == "legacy-sqlite" else b"FRCLIENT2" + request.school.pk.bytes):
                    return error("invalid_sqlite_backup", 400)
                count += len(chunk)
                if count > settings.MAX_BACKUP_BYTES or count > length:
                    return error("backup_too_large_or_invalid", 413)
                digest.update(chunk)
                output.write(encryptor.update(chunk))
            output.write(encryptor.finalize() + encryptor.tag)
            output.flush()
            os.fsync(output.fileno())
        if count != length or digest.hexdigest() != expected:
            return error("backup_digest_mismatch", 400)
        with transaction.atomic():
            school = School.objects.select_for_update().select_related("user").get(pk=request.school.pk)
            if not school.entitled or not school.backups_enabled:
                return error("subscription_inactive")
            old = list(Backup.objects.filter(school=school).order_by("-created_at"))
            keep = old[:max(0, school.backup_retention - 1)]
            remove = old[len(keep):]
            if sum(b.size for b in keep) + count > school.backup_quota_bytes:
                return error("backup_quota_exceeded", 409)
            blob_name = uuid.uuid4().hex + ".enc"
            final = location(school.pk, blob_name)
            os.replace(temporary, final)
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            backup = Backup.objects.create(school=school, blob_name=blob_name, size=count, sha256=expected, format=backup_format)
            for item in remove:
                path = location(school.pk, item.blob_name)
                item.delete()
                transaction.on_commit(lambda p=path: p.unlink(missing_ok=True), robust=True)
            AuditEvent.objects.create(actor=school.user, school=school, action="backup.created", object_id=str(backup.pk))
        committed = True
        return JsonResponse(describe(backup), status=201)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if final is not None and not committed:
            final.unlink(missing_ok=True)


def download_response(backup):
    # Authenticate the GCM tag and digest before returning any plaintext.
    output = tempfile.TemporaryFile()
    try:
        with location(backup.school_id, backup.blob_name).open("rb") as source:
            header = source.read(len(MAGIC) + 12)
            if len(header) != len(MAGIC) + 12 or not header.startswith(MAGIC):
                raise ValueError("Invalid encrypted backup")
            source.seek(-16, os.SEEK_END)
            end = source.tell()
            tag = source.read(16)
            source.seek(len(header))
            decryptor = Cipher(algorithms.AES(school_key(backup.school_id)), modes.GCM(header[len(MAGIC):], tag)).decryptor()
            decryptor.authenticate_additional_data(str(backup.school_id).encode())
            digest = hashlib.sha256()
            while source.tell() < end:
                chunk = decryptor.update(source.read(min(65536, end - source.tell())))
                digest.update(chunk)
                output.write(chunk)
            tail = decryptor.finalize()
            output.write(tail)
            digest.update(tail)
            if output.tell() != backup.size or digest.hexdigest() != backup.sha256:
                raise ValueError("Backup integrity failure")
        output.seek(0)
        response = FileResponse(output, as_attachment=True, filename=f"backup-{backup.created_at:%Y%m%d-%H%M%S}." + ("frbackup" if backup.format == "client-v2" else "sqlite3"), content_type="application/octet-stream")
        response["X-Backup-SHA256"] = backup.sha256
        response["X-Backup-Format"] = backup.format
        response["Content-Length"] = str(backup.size)
        return response
    except BaseException:
        output.close()
        raise


@require_GET
@device_auth(entitlement=False)
def listing(request):
    return JsonResponse({"backups": [describe(b) for b in Backup.objects.filter(school=request.school)[:100]]})


@require_GET
@device_auth(entitlement=False)
def download(request, backup_id):
    backup = Backup.objects.filter(pk=backup_id, school=request.school).first()
    if backup is None:
        return error("not_found", 404)
    if throttled(request, "backup-download", str(request.school.pk), limit=20, seconds=3600):
        return error("rate_limited", 429)
    try:
        response = download_response(backup)
    except (OSError, ValueError, InvalidTag):
        return error("backup_unavailable", 503)
    AuditEvent.objects.create(actor=request.school.user, school=request.school, action="backup.downloaded", object_id=str(backup.pk))
    return response
