from django.utils.translation import gettext_lazy as _
import hashlib
import logging
import unicodedata
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse


logger = logging.getLogger(__name__)


class SecurityHeaders:
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        # Docker web is private; Caddy overwrites X-Forwarded-For. Direct gunicorn
        # access must not be published by an alternative deployment.
        response = None
        if request.method == "POST" and request.path in {"/login/", "/admin/login/", "/register/", "/resend-verification/", "/password-reset/"}:
            identity = request.POST.get("username", request.POST.get("email", ""))
            try:
                limited = throttled(request, "web-auth:" + request.path, identity, limit=15)
            except Exception:
                # Fail closed without running authentication, but make a temporary
                # protection outage distinguishable from an internal application bug.
                logger.error('Authentication rate-limit backend unavailable')
                response = HttpResponse(_('The security check is temporarily unavailable. Please try again.'), status=503)
            else:
                if limited:
                    response = too_many()
        if response is None:
            response = self.get_response(request)
        response["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response["Referrer-Policy"] = "same-origin"
        # The website demo is synthetic; browser access to student sensors is unnecessary.
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not request.path.startswith(settings.STATIC_URL):
            response["Cache-Control"] = "no-store"
        return response


def throttled(request, scope, identity="", limit=20, seconds=600):
    # Match Django UsernameField/EmailField and our login normalization BEFORE
    # hashing or truncation. Raw whitespace/Unicode aliases must not create a
    # fresh per-account budget across different source IPs.
    identity = unicodedata.normalize('NFKC', identity).strip().casefold()[:254]
    address = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "unknown")).split(",")[0].strip()
    # Fixed-window counters are shared by all workers via Redis. Fail closed if
    # Redis is unavailable rather than silently removing brute-force protection.
    for kind, value in (("ip", address), ("account", identity)):
        if not value:
            continue
        key = "limit:" + hashlib.sha256(f"{scope}:{kind}:{value}".encode()).hexdigest()
        if cache.add(key, 1, timeout=seconds):
            count = 1
        else:
            try:
                count = cache.incr(key)
            except ValueError:
                count = 1 if cache.add(key, 1, timeout=seconds) else cache.incr(key)
        if count > limit:
            return True
    return False


def too_many():
    response = HttpResponse(_('Too many requests. Please try again in a few minutes.'), status=429)
    response["Retry-After"] = "600"
    return response
