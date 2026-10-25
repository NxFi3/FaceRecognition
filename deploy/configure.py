"""Generate private deployment settings and a public desktop configuration.

python -m deploy.configure --domain schools.example.com --email owner@example.com
Never regenerate keys for an existing installation: old backups require them.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--email", required=True, help="TLS certificate contact; not an automatically created admin")
    args = parser.parse_args()
    if not re.fullmatch(r"(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}", args.domain):
        parser.error("Supply a DNS hostname without scheme/path")
    if not re.fullmatch(r"[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", args.email):
        parser.error("Invalid contact email")
    root = Path(__file__).resolve().parent
    env_path = root / ".env"
    if env_path.exists():
        parser.error("deploy/.env already exists; refusing to replace signing/backup keys")
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    values = {"DOMAIN": args.domain.lower(), "ACME_EMAIL": args.email, "PUBLIC_URL": "https://" + args.domain.lower(),
              "DJANGO_SECRET_KEY": secrets.token_urlsafe(64), "POSTGRES_PASSWORD": secrets.token_urlsafe(32), "POSTGRES_ADMIN_PASSWORD": secrets.token_urlsafe(32),
              "LICENSE_SIGNING_KEY": base64.b64encode(private).decode(), "BACKUP_MASTER_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
              "SMTP_HOST": "", "SMTP_PORT": "587", "SMTP_USER": "", "SMTP_PASSWORD": "", "SMTP_FROM": args.email,
              "RELEASE_ROOT": "/data/releases", "MANUAL_PAYMENT_INSTRUCTIONS": "", "CONTACT_RESPONSE_DAYS": "3"}
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    (root / "releases").mkdir(mode=0o755, exist_ok=True)
    generated = root / "generated"
    generated.mkdir(mode=0o700, exist_ok=True)
    (generated / "subscription-client.json").write_text(json.dumps({"service_url": values["PUBLIC_URL"], "public_key": base64.b64encode(public).decode()}, indent=2) + "\n")
    print("Created deploy/.env (private) and deploy/generated/subscription-client.json (public). Configure SMTP, then start Compose. Keep an encrypted offline copy of .env.")


if __name__ == "__main__":
    main()
