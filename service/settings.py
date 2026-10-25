"""Production defaults. SERVICE_TESTING=1 is only for isolated service tests."""
import base64
import os
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qsl

BASE_DIR = Path(__file__).resolve().parent
TESTING = os.environ.get("SERVICE_TESTING") == "1"
DEBUG = False
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "test-only-secret-" * 5 if TESTING else "")
if len(SECRET_KEY) < 50:
    raise RuntimeError("Generate deployment secrets with deploy.configure before starting the service")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://testserver" if TESTING else "").rstrip("/")
try:
    parsed = urlparse(PUBLIC_URL)
    origin_port = parsed.port  # Validate malformed/out-of-range ports before serving.
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
            or (origin_port is not None and not 1 <= origin_port <= 65535)
            or any(character.isspace() or ord(character) < 32 for character in PUBLIC_URL)):
        raise ValueError()
except ValueError:
    raise RuntimeError("PUBLIC_URL must be an HTTPS origin with valid port and without credentials, path, query or fragment") from None
ALLOWED_HOSTS = [parsed.hostname]
CSRF_TRUSTED_ORIGINS = [PUBLIC_URL]
ROOT_URLCONF = "service.urls"
WSGI_APPLICATION = "service.wsgi.application"
INSTALLED_APPS = ["service.apps.OwnerAdminConfig", "django.contrib.auth", "django.contrib.contenttypes",
                  "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "service"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
              "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.locale.LocaleMiddleware", "django.middleware.common.CommonMiddleware",
              "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
              "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
              "service.security.SecurityHeaders"]
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"],
              "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request",
               "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages", "service.context_processors.company_navigation"]}}]
test_database_url = os.environ.get("SERVICE_TEST_DATABASE_URL", "")
if test_database_url and not TESTING:
    raise RuntimeError("SERVICE_TEST_DATABASE_URL is forbidden outside isolated SERVICE_TESTING mode")


def postgres_configuration(value):
    """Preserve explicit TLS intent; never silently discard connection options."""
    try:
        db = urlparse(value)
        port = db.port if db.port is not None else 5432
        if (db.scheme not in {"postgres", "postgresql"} or not db.hostname or not db.username
                or not db.password or not db.path.startswith('/') or not db.path[1:]
                or '/' in db.path[1:] or db.fragment or not 1 <= port <= 65535):
            raise ValueError()
        pairs = parse_qsl(db.query, strict_parsing=True, keep_blank_values=True)
        options = dict(pairs)
        allowed = {'sslmode', 'sslrootcert', 'sslcert', 'sslkey', 'connect_timeout'}
        if len(options) != len(pairs) or set(options) - allowed or any(not v for v in options.values()):
            raise ValueError()
        if options.get('sslmode', 'prefer') not in {'disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'}:
            raise ValueError()
        if 'connect_timeout' in options:
            timeout = int(options['connect_timeout'])
            if not 1 <= timeout <= 120:
                raise ValueError()
            options['connect_timeout'] = timeout
        return {"ENGINE": "django.db.backends.postgresql", "NAME": unquote(db.path[1:]),
                "USER": unquote(db.username), "PASSWORD": unquote(db.password), "HOST": db.hostname,
                "PORT": port, "CONN_MAX_AGE": 60, "CONN_HEALTH_CHECKS": True, "OPTIONS": options}
    except (ValueError, TypeError):
        raise RuntimeError("Configure a valid PostgreSQL DATABASE_URL with host, database, credentials and supported TLS/connect options") from None


def redis_location(value):
    try:
        location = urlparse(value)
        port = location.port if location.port is not None else 6379
        if (location.scheme not in {'redis', 'rediss'} or not location.hostname
                or location.fragment or not 1 <= port <= 65535
                or (location.path not in ('', '/') and not location.path[1:].isdigit())):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise RuntimeError("Configure a valid REDIS_URL for the shared cache and rate limiter") from None


if TESTING and not test_database_url:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
else:
    DATABASES = {"default": postgres_configuration(test_database_url or os.environ.get("DATABASE_URL", ""))}
    if TESTING:
        CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    else:
        CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache",
                              "LOCATION": redis_location(os.environ.get("REDIS_URL", ""))}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"}]
LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("fa", "فارسی")]
USE_I18N = True
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_HTTPONLY = True
LANGUAGE_COOKIE_SAMESITE = "Lax"
LANGUAGE_COOKIE_SECURE = not TESTING
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
