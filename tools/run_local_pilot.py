"""Start a real, signed-license FaceRecognition pilot with a loopback-only HTTPS server.

Run from the repository root:
    python -m tools.run_local_pilot

All account/face/backup data lives outside the checkout in a private directory.
This is a local integration setup, NOT a production deployment or substitute for
Windows/camera acceptance. No Docker, Internet, production server, or TLS bypass.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import secrets
import ssl
import stat
import subprocess
import sys
import threading
from wsgiref.simple_server import make_server

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = Path.home() / ".local" / "share" / "nxfi-facerecognition-pilot"
PORT = 18443


def private_write(path, content):
    """Create secrets without briefly exposing them with default permissions."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)


def workspace(path):
    target = path.expanduser().absolute()
    if target.is_symlink():
        raise RuntimeError("Pilot root must not be a symbolic link")
    target = target.resolve()
    if target == ROOT or ROOT in target.parents or target == Path.home().resolve():
        raise RuntimeError("Pilot directory must be outside the repository and home root")
    marker = target / ".nxfi-local-pilot"
    if target.exists() and any(target.iterdir()) and not marker.is_file():
        raise RuntimeError("Refusing to use a nonempty, unmarked directory")
    if target.exists() and target.is_symlink():
        raise RuntimeError("Pilot root must not be a symbolic link")
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.chmod(0o700)
    if not marker.exists():
        private_write(marker, b"Isolated synthetic NxFi pilot; NOT production.\n")
    for name in ("data", "service", "logs", "exports"):
        (target / name).mkdir(mode=0o700, exist_ok=True)
    linked = target / "models"
    expected_models = (ROOT / "models").resolve()
    if linked.is_symlink():
        # The marked pilot workspace is disposable synthetic runtime state. If
        # the source checkout moved, its old model link may be dangling or may
        # still resolve to another checkout. Replace only the link itself;
        # never delete or mutate the old target directory or its model files.
        if linked.resolve() != expected_models:
            linked.unlink()
            linked.symlink_to(expected_models, target_is_directory=True)
    elif not linked.exists():
        linked.symlink_to(expected_models, target_is_directory=True)
    else:
        raise RuntimeError("Pilot model directory already exists; refusing to replace it")
    return target


def secrets_file(target):
    path = target / "pilot-secrets.json"
    if not path.exists():
        signer = ed25519.Ed25519PrivateKey.generate()
        private = signer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        value = {
            "django_secret": secrets.token_urlsafe(64),
            "license_private": base64.b64encode(private).decode(),
            "backup_master": base64.b64encode(secrets.token_bytes(32)).decode(),
        }
        private_write(path, json.dumps(value).encode())
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError("Pilot signing-secret file is not private (expected mode 0600)")
    value = json.loads(path.read_text())
    signer = ed25519.Ed25519PrivateKey.from_private_bytes(base64.b64decode(value["license_private"], validate=True))
    public = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return value, base64.b64encode(public).decode()


def create_tls(target):
    cert_dir = target / "certs"
    cert_dir.mkdir(mode=0o700, exist_ok=True)
    ca_path = cert_dir / "ca.pem"
    cert_path = cert_dir / "server.pem"
    key_path = cert_dir / "server-key.pem"
    if all(path.exists() for path in (ca_path, cert_path, key_path)):
        return ca_path, cert_path, key_path
    if any(path.exists() for path in (ca_path, cert_path, key_path)):
        raise RuntimeError("Incomplete pilot certificate set; refusing to silently regenerate trust")
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NxFi isolated localhost pilot CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=360))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256()))
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    server = (x509.CertificateBuilder().subject_name(name).issuer_name(ca_name)
        .public_key(server_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=360))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))
        ]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256()))
    private_write(ca_path, ca.public_bytes(serialization.Encoding.PEM))
    private_write(cert_path, server.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    private_write(key_path, server_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return ca_path, cert_path, key_path


def configure(target, values, public, ca_path, port):
    origin = "https://localhost:" + str(port)
    client_path = target / "subscription-client.json"
    expected = {"service_url": origin, "public_key": public}
    if not client_path.exists():
        private_write(client_path, (json.dumps(expected, indent=2) + "\n").encode())
    elif json.loads(client_path.read_text()) != expected:
        raise RuntimeError("Existing pilot public client configuration does not match the local signing key/port")
    # This process alone receives server signing/backup keys.
    for key in ("NXFI_LOCAL_DEVELOPMENT", "SERVICE_TESTING", "SERVICE_TEST_DATABASE_URL"):
        os.environ.pop(key, None)
    os.environ.update({
        "NXFI_LOCAL_PILOT": "1", "NXFI_PILOT_ROOT": str(target),
        "DJANGO_SETTINGS_MODULE": "service.pilot_settings",
        "PUBLIC_URL": origin, "DJANGO_SECRET_KEY": values["django_secret"],
        "LICENSE_SIGNING_KEY": values["license_private"],
        "BACKUP_MASTER_KEY": values["backup_master"],
    })
    return origin


def bootstrap_accounts(target):
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from django.utils import timezone as django_timezone
    from django.db import transaction
    from service.models import AuditEvent, School
    from service.owner_models import CompanyOwner
    from service.commerce import sync_face_subscription

    call_command("migrate", verbosity=0, interactive=False)
    # Serve the actual bundled CSS/JS from the isolated directory even in
    # production-like DEBUG=False mode. Never write compiled assets into git.
    call_command("collectstatic", verbosity=0, interactive=False)
    credentials_file = target / "pilot-test-accounts.txt"
    with transaction.atomic():
        if not CompanyOwner.objects.exists():
            if get_user_model().objects.filter(username__in=[
                    "pilot-owner@localhost.invalid", "pilot-school@localhost.invalid"]).exists():
                raise RuntimeError("Pilot account collision; will not change existing users")
            owner_password = secrets.token_urlsafe(22) + "A1!"
            school_password = secrets.token_urlsafe(22) + "B2!"
            owner = get_user_model().objects.create_user(
                username="pilot-owner@localhost.invalid", email="pilot-owner@localhost.invalid",
                password=owner_password, is_active=True)
            CompanyOwner.objects.create(pk=1, user=owner)
            student = get_user_model().objects.create_user(
                username="pilot-school@localhost.invalid", email="pilot-school@localhost.invalid",
                password=school_password, is_active=True)
            now = django_timezone.now()
            school = School.objects.create(
                user=student, name="NxFi Synthetic Pilot School", status=School.Status.ACTIVE,
                starts_at=now, expires_at=now + timedelta(days=30),
                student_limit=900, device_limit=2, email_verified_at=now)
            sync_face_subscription(school)
            AuditEvent.objects.create(actor=owner, school=school, action="pilot.synthetic_school_created",
                                      object_id=str(school.pk))
            private_write(credentials_file, (
                "LOCAL SYNTHETIC TEST ACCOUNTS ONLY — never use for a real school.\n"
                "Owner portal: pilot-owner@localhost.invalid / " + owner_password + "\n"
                "Desktop school: pilot-school@localhost.invalid / " + school_password + "\n"
            ).encode())
    print("\nLocal accounts: " + str(credentials_file))
    print("Only this private pilot directory contains its test account passwords.")


def serve(origin, cert_path, key_path, port):
    from django.core.wsgi import get_wsgi_application
    django_app = get_wsgi_application()
    def tls_app(environ, start_response):
        # WSGI's handler otherwise assumes HTTP even when its socket is TLS.
        # Actual TLS termination remains mandatory; this is not a proxy header.
        environ["wsgi.url_scheme"] = "https"
        return django_app(environ, start_response)
    server = make_server("127.0.0.1", port, tls_app)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.base_environ["wsgi.url_scheme"] = "https"
    thread = threading.Thread(target=server.serve_forever, name="nxfi-pilot-https", daemon=True)
    thread.start()
    return server, thread


def launch_desktop(target, ca_path):
    env = os.environ.copy()
    for key in ("DJANGO_SECRET_KEY", "LICENSE_SIGNING_KEY", "BACKUP_MASTER_KEY",
                "DATABASE_URL", "REDIS_URL", "DJANGO_SETTINGS_MODULE", "SMTP_PASSWORD"):
        env.pop(key, None)
    env["REQUESTS_CA_BUNDLE"] = str(ca_path)
    env["NO_PROXY"] = "localhost,127.0.0.1"
    env["no_proxy"] = "localhost,127.0.0.1"
    # Do not trust a random working directory or the default school database.
    return subprocess.call([sys.executable, "-m", "src.app"], cwd=str(ROOT), env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--server-only", action="store_true", help="Operate owner portal without starting the desktop")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Pilot port must be between 1024 and 65535")
    target = workspace(args.data_dir)
    values, public = secrets_file(target)
    ca, cert, key = create_tls(target)
    origin = configure(target, values, public, ca, args.port)
    bootstrap_accounts(target)
    server, thread = serve(origin, cert, key, args.port)
    try:
        import requests
        # Verify the real localhost certificate and signed endpoint without disabling TLS.
        session = requests.Session()
        session.trust_env = False
        response = session.get(origin + "/health/", verify=str(ca), timeout=10)
        response.raise_for_status()
        if response.json() != {"ok": True}:
            raise RuntimeError("Pilot HTTPS health check failed")
        print("\nNXFI PILOT — REAL APP / SYNTHETIC SCHOOL ONLY")
        print("Owner website: " + origin + "/owner/")
        print("Login page:    " + origin + "/login/")
        print("Test passwords: " + str(target / "pilot-test-accounts.txt"))
        print("Firefox may need you to trust the pilot certificate for localhost.")
        print("Closing the desktop stops the local pilot service.")
        if args.server_only:
            print("Press Ctrl+C to stop this localhost-only server.")
            thread.join()
        else:
            return launch_desktop(target, ca)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
