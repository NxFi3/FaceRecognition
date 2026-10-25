"""Production defaults. SERVICE_TESTING=1 is only for isolated service tests."""
import base64
import os
from pathlib import Path
from urllib.parse import urlparse, unquote

BASE_DIR = Path(__file__).resolve().parent
TESTING = os.environ.get("SERVICE_TESTING") == "1"
DEBUG = False
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "test-only-secret-" * 5 if TESTING else "")
if len(SECRET_KEY) < 50:
    raise RuntimeError("Generate deployment secrets with deploy.configure before starting the service")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://testserver" if TESTING else "").rstrip("/")
parsed = urlparse(PUBLIC_URL)
if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
        or parsed.path or parsed.query or parsed.fragment):
    raise RuntimeError("PUBLIC_URL must be an HTTPS origin")
ALLOWED_HOSTS = [parsed.hostname]
CSRF_TRUSTED_ORIGINS = [PUBLIC_URL]
ROOT_URLCONF = "service.urls"
WSGI_APPLICATION = "service.wsgi.application"
INSTALLED_APPS = ["service.apps.OwnerAdminConfig", "django.contrib.auth", "django.contrib.contenttypes",
                  "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "service"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
              "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
              "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
              "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
              "service.security.SecurityHeaders"]
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"],
              "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request",
               "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
if TESTING and not os.environ.get("SERVICE_TEST_DATABASE_URL"):
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
else:
    db = urlparse(os.environ.get("SERVICE_TEST_DATABASE_URL") or os.environ["DATABASE_URL"])
    if db.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError("Production service requires PostgreSQL")
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": db.path.lstrip("/"),
                "USER": unquote(db.username or ""), "PASSWORD": unquote(db.password or ""), "HOST": db.hostname,
                "PORT": db.port or 5432, "CONN_MAX_AGE": 60, "CONN_HEALTH_CHECKS": True}}
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")}}
    if TESTING:
        CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"}]
LANGUAGE_CODE = "fa"
TIME_ZONE = "UTC"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = []
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
# Intentionally host-scoped: do not force TLS/preload on the operator's unrelated
# subdomains. These two policy advisories are documented in the deployment guide.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
X_FRAME_OPTIONS = "DENY"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/account/"
LOGOUT_REDIRECT_URL = "/login/"
PASSWORD_RESET_TIMEOUT = 3600
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend" if TESTING else "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("SMTP_HOST", "")
EMAIL_PORT = int(os.environ.get("SMTP_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get("SMTP_FROM", "noreply@example.invalid")
BACKUP_ROOT = Path(os.environ.get("BACKUP_ROOT", "/data/backups"))
MAX_BACKUP_BYTES = 128 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 100
LEASE_SECONDS = 900
DEVICE_CREDENTIAL_DAYS = 30
SIGNING_PRIVATE_KEY = os.environ.get("LICENSE_SIGNING_KEY", "")
BACKUP_MASTER_KEY = os.environ.get("BACKUP_MASTER_KEY", "")
if not TESTING:
    for name, value in (("LICENSE_SIGNING_KEY", SIGNING_PRIVATE_KEY), ("BACKUP_MASTER_KEY", BACKUP_MASTER_KEY)):
        try:
            if len(base64.b64decode(value, validate=True)) != 32:
                raise ValueError()
        except Exception as exc:
            raise RuntimeError(f"{name} must contain a generated 32-byte base64 key") from exc

# Short owner step-up; authority comes only from the singleton DB record.
OWNER_REAUTH_SECONDS = 300

# Commercial artifacts are operator-installed and mounted read-only, never user uploads.
RELEASE_ROOT = Path(os.environ.get('RELEASE_ROOT', '/data/releases'))
MANUAL_PAYMENT_INSTRUCTIONS = os.environ.get('MANUAL_PAYMENT_INSTRUCTIONS', '')
CONTACT_RESPONSE_DAYS = int(os.environ.get('CONTACT_RESPONSE_DAYS', '3'))
