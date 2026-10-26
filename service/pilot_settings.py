"""Isolated loopback HTTPS pilot; never suitable for public deployment."""
import os
from pathlib import Path
from urllib.parse import urlparse

if os.environ.get("NXFI_LOCAL_PILOT") != "1":
    raise RuntimeError("Explicit NXFI_LOCAL_PILOT=1 is required")
if os.environ.get("SERVICE_TESTING") == "1" or os.environ.get("NXFI_LOCAL_DEVELOPMENT") == "1":
    raise RuntimeError("Pilot must not be mixed with test/dev server settings")
root_text = os.environ.get("NXFI_PILOT_ROOT", "")
root = Path(root_text).resolve() if root_text else None
repo = Path(__file__).resolve().parent.parent
if (root is None or not Path(root_text).is_absolute() or root == repo
        or repo in root.parents or root == Path.home().resolve()
        or not (root / ".nxfi-local-pilot").is_file()):
    raise RuntimeError("Pilot settings require a separate marked directory")
origin = os.environ.get("PUBLIC_URL", "")
address = urlparse(origin)
if address.scheme != "https" or address.hostname != "localhost" or address.path or address.query or address.fragment:
    raise RuntimeError("Pilot service must use a localhost HTTPS origin")

# Placeholder URLs satisfy production settings validation; NEVER connect to them.
# The database and cache are fully replaced below, before Django setup.
os.environ["DATABASE_URL"] = "postgresql://pilot:pilot@127.0.0.1:1/unused"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"
from .settings import *  # noqa: F403,F401,E402

DEBUG = False
OWNER_ONLY_WEB = True
ALLOWED_HOSTS = ["localhost"]
CSRF_TRUSTED_ORIGINS = [origin]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": root / "service" / "pilot.sqlite3", "OPTIONS": {"timeout": 15}}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
BACKUP_ROOT = root / "service" / "backups"
RELEASE_ROOT = root / "service" / "releases"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_HOST = ""
SECURE_PROXY_SSL_HEADER = None
SECURE_HSTS_SECONDS = 0
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
