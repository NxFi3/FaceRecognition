import hashlib
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse


class SecurityHeaders:
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        # Docker web is private; Caddy overwrites X-Forwarded-For. Direct gunicorn
        # access must not be published by an alternative deployment.
        if request.method == "POST" and request.path in {"/login/", "/admin/login/", "/register/", "/resend-verification/", "/password-reset/"}:
            identity = request.POST.get("username", request.POST.get("email", ""))[:150]
            if throttled(request, "web-auth:" + request.path, identity, limit=15):
                return too_many()
        response = self.get_response(request)
        response["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response["Referrer-Policy"] = "same-origin"
        # The website demo is synthetic; browser access to student sensors is unnecessary.
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not request.path.startswith(settings.STATIC_URL):
            response["Cache-Control"] = "no-store"
        return response


def throttled(request, scope, identity="", limit=20, seconds=600):
    address = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "unknown")).split(",")[0].strip()
    # Fixed-window counters are shared by all workers via Redis. Fail closed if
    # Redis is unavailable rather than silently removing brute-force protection.
    for kind, value in (("ip", address), ("account", identity.casefold())):
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
    response = HttpResponse("تعداد درخواست زیاد است؛ چند دقیقه بعد تلاش کنید.", status=429)
    response["Retry-After"] = "600"
    return response
