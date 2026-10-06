import hashlib
import hmac
import secrets

try:
    from database import get_setting, set_setting
except ModuleNotFoundError:
    from .database import get_setting, set_setting


PASSWORD_HASH_KEY = "admin_password_hash"
PASSWORD_SALT_KEY = "admin_password_salt"
PASSWORD_ITERATIONS = 400_000
PASSWORD_SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 8


def _derive_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )


def password_is_configured():
    return bool(
        get_setting(PASSWORD_HASH_KEY)
        and get_setting(PASSWORD_SALT_KEY)
    )


def set_admin_password(password):
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Administrator password must be at least "
            f"{MIN_PASSWORD_LENGTH} characters."
        )

    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    digest = _derive_password(password, salt)

    set_setting(PASSWORD_SALT_KEY, salt.hex())
    set_setting(PASSWORD_HASH_KEY, digest.hex())


def verify_admin_password(password):
    if not password_is_configured():
        return False

    try:
        salt = bytes.fromhex(get_setting(PASSWORD_SALT_KEY))
        expected = bytes.fromhex(get_setting(PASSWORD_HASH_KEY))
    except (TypeError, ValueError):
        return False

    actual = _derive_password(password, salt)
    return hmac.compare_digest(actual, expected)


def change_admin_password(current_password, new_password):
    if not verify_admin_password(current_password):
        return False

    set_admin_password(new_password)
    return True
