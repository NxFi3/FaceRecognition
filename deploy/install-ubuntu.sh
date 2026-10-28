#!/bin/sh
# Provision dependencies on a fresh Ubuntu 24.04 VPS. Does not open a firewall,
# buy infrastructure, create default accounts, or replace existing service keys.
set -eu
if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo on your VPS." >&2; exit 1; fi
if [ "$#" -ne 2 ]; then echo "Usage: $0 DOMAIN CERTIFICATE_CONTACT_EMAIL" >&2; exit 1; fi
. /etc/os-release
if [ "$ID" != "ubuntu" ] || [ "$VERSION_ID" != "24.04" ]; then
    echo "This helper supports Ubuntu 24.04 only; use the manual Docker instructions elsewhere." >&2
    exit 1
fi
repository=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository"
if [ -e deploy/.env ]; then echo "deploy/.env exists; use the deployment guide for upgrades. Keys were not changed." >&2; exit 1; fi
apt-get update
apt-get install -y ca-certificates python3-venv age git
if ! command -v docker >/dev/null; then apt-get install -y docker.io docker-compose-v2; fi
docker compose version >/dev/null
systemctl enable --now docker
python3 -m venv deploy/.venv
deploy/.venv/bin/python -m pip install 'cryptography>=50.0.0,<51'
deploy/.venv/bin/python -m deploy.configure --domain "$1" --email "$2"
echo "Preparation complete. Configure SMTP in deploy/.env, then follow docs/subscription-setup-fa.md to start and create the administrator."
