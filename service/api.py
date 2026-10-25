"""Device API: opaque revocable credentials and nonce-bound Ed25519 leases."""
import base64
from datetime import timedelta
from functools import wraps
import hashlib
import json
import secrets
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings
from django.contrib.auth import authenticate
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from .models import AuditEvent, Device, School
from .security import throttled


def error(code, status=403):
    return JsonResponse({"error": code}, status=status)


def body(request):
    if request.content_type != "application/json" or int(request.META.get("CONTENT_LENGTH") or 0) > 8192:
        raise ValueError("invalid request")
    value = json.loads(request.body)
    if not isinstance(value, dict):
        raise ValueError("invalid request")
    return value


def signed_lease(school, installation_id, nonce, revoked=False, version=2):
    if not isinstance(nonce, str) or not 32 <= len(nonce) <= 128 or not nonce.isascii():
        raise ValueError("invalid nonce")
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unsupported license version")
    now = timezone.now()
    expires = school.expires_at or now
    if version == 1:
        expires = min(expires, now + timedelta(seconds=settings.LEASE_SECONDS))
    payload = {"version": version, "license_id": str(uuid.uuid4()),
               "status": "active" if school.entitled and not revoked else "revoked",
               "subscription_starts_at": (school.starts_at or now).timestamp(), "device_limit": school.device_limit, "audience": "face-recognition-desktop", "school_id": str(school.pk),
               "school_name": school.name, "device_id": str(installation_id), "nonce": nonce,
               "issued_at": now.timestamp(), "lease_expires_at": expires.timestamp(),
               "subscription_expires_at": (school.expires_at or now).timestamp(), "student_limit": school.student_limit,
               "backups_enabled": school.backups_enabled}
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(settings.SIGNING_PRIVATE_KEY, validate=True))
    return {"payload": base64.b64encode(serialized).decode(), "signature": base64.b64encode(key.sign(serialized)).decode()}


def _credential_valid(device, *, synchronization=False):
    return bool(device is not None
        and constant_time_compare(device.password_stamp, device.school.user.get_session_auth_hash())
        and (synchronization or (not device.revoked
             and device.credential_expires_at > timezone.now() and device.school.user.is_active)))


def locked_authenticated_device(request, *, synchronization=False):
    """Refresh bearer authority under School -> Device row locks.

    Call inside transaction.atomic immediately before a state mutation or license
    signature. The initial decorator only authenticates a request snapshot; it is
    insufficient after waiting, streaming an upload, or concurrent revocation.
    Synchronization may return revoked state solely to issue a signed denial.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('Fresh device authorization requires an atomic transaction')
    school = School.objects.select_for_update().select_related('user').filter(pk=request.school.pk).first()
    if school is None:
        return None
    device = Device.objects.select_for_update().filter(pk=request.device.pk, school=school).first()
    if device is None or device.token_digest is None or not constant_time_compare(
            device.token_digest, getattr(request, '_device_auth_digest', '')):
        return None
    device.school = school  # Reuse the freshly locked school/user, never an old relation cache.
    if not _credential_valid(device, synchronization=synchronization):
        return None
    request.device, request.school = device, school
    return device


def device_auth(entitlement=True, synchronization=False):
    def decorate(function):
        @wraps(function)
        def wrapped(request, *args, **kwargs):
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer ") or len(header) > 160:
                return error("authentication_required", 401)
            digest = hashlib.sha256(header[7:].encode()).hexdigest()
            device = Device.objects.select_related("school__user").filter(token_digest=digest).first()
            if not _credential_valid(device, synchronization=synchronization):
                return error("invalid_device_credential", 401)
            if entitlement and not device.school.entitled:
                return error("subscription_inactive")
            request._device_auth_digest = digest
            request.device = device
            request.school = device.school
            return function(request, *args, **kwargs)
        return wrapped
    return decorate


@csrf_exempt
@require_POST
def login(request):
    try:
        data = body(request)
        email = str(data.get("email", "")).strip().casefold()
        password = data.get("password", "")
        installation_id = uuid.UUID(str(data.get("device_id", "")))
        nonce = data["nonce"]
        if type(data.get("license_version", 1)) is not int or data.get("license_version", 1) not in (1, 2):
            raise ValueError("Unsupported license version")
        if len(email) > 150 or not isinstance(password, str) or len(password) > 1024:
            raise ValueError()
        if not isinstance(nonce, str) or not 32 <= len(nonce) <= 128 or not nonce.isascii():
            raise ValueError()
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return error("invalid_request", 400)
    if throttled(request, "device-login", email, limit=15):
        return error("rate_limited", 429)
    user = authenticate(request, username=email, password=password)
    if user is None:
        return error("invalid_credentials", 401)
    authenticated_stamp = user.get_session_auth_hash()
    with transaction.atomic():
        school = School.objects.select_for_update().select_related('user').filter(user=user).first()
        if school is not None and not constant_time_compare(authenticated_stamp, school.user.get_session_auth_hash()):
            return error('invalid_credentials', 401)
        if school is None or not school.entitled:
            return error("subscription_inactive")
        device = Device.objects.filter(school=school, installation_id=installation_id).first()
        if device and device.revoked:
            return error("device_revoked")
        others = Device.objects.filter(school=school, revoked=False, token_digest__isnull=False).exclude(installation_id=installation_id).count()
        if others >= school.device_limit:
            return error("device_limit_reached")
        token = secrets.token_urlsafe(32)
        if device is None:
            device = Device(school=school, installation_id=installation_id)
        device.label = str(data.get("label", "Desktop"))[:80]
        device.token_digest = hashlib.sha256(token.encode()).hexdigest()
        device.password_stamp = school.user.get_session_auth_hash()
        device.credential_expires_at = max(school.expires_at, timezone.now() + timedelta(days=settings.DEVICE_CREDENTIAL_DAYS))
        device.last_seen = timezone.now()
        device.save()
        AuditEvent.objects.create(actor=user, school=school, action="device.login", object_id=str(device.pk))
        lease = signed_lease(school, installation_id, nonce, version=data.get("license_version", 1))
    return JsonResponse({"credential": token, "lease": lease})


@csrf_exempt
@require_POST
@device_auth(entitlement=False, synchronization=True)
def lease(request):
    if throttled(request, "lease", str(request.device.pk), limit=120):
        return error("rate_limited", 429)
    try:
        data = body(request)
        version = data.get('license_version', 1)
        nonce = data['nonce']
        # Validate outside locks before touching license state.
        if (type(version) is not int or version not in (1, 2)
                or not isinstance(nonce, str) or not 32 <= len(nonce) <= 128 or not nonce.isascii()):
            raise ValueError('Invalid license request')
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return error('invalid_request', 400)
    with transaction.atomic():
        device = locked_authenticated_device(request, synchronization=True)
        if device is None:
            return error('invalid_device_credential', 401)
        if version == 1 and (device.revoked or not request.school.entitled):
            return error('subscription_inactive')
        result = signed_lease(request.school, device.installation_id, nonce,
                              revoked=device.revoked, version=version)
        Device.objects.filter(pk=device.pk).update(last_seen=timezone.now(),
            credential_expires_at=max(request.school.expires_at or timezone.now(),
                                      timezone.now() + timedelta(days=settings.DEVICE_CREDENTIAL_DAYS)))
    return JsonResponse({'lease': result})


@csrf_exempt
@require_POST
@device_auth(entitlement=False)
def logout(request):
    with transaction.atomic():
        device = locked_authenticated_device(request)
        if device is None:
            return error('invalid_device_credential', 401)
        Device.objects.filter(pk=device.pk).update(token_digest=None)
        AuditEvent.objects.create(actor=request.school.user, school=request.school,
                                  action='device.logout', object_id=str(device.pk))
    return JsonResponse({'ok': True})
