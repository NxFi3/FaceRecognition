#!/bin/sh
# Cold, consistent encrypted backup of DB, encrypted blobs AND server keys.
# Usage: ./deploy/backup-server.sh age1RECOVERY_PUBLIC_KEY /secure/offsite/server.age
set -eu
umask 077
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 AGE_PUBLIC_RECIPIENT OUTPUT.age" >&2
    exit 1
fi
command -v age >/dev/null
recipient="$1"
output="$2"
case "$output" in /*) ;; *) output="$(pwd)/$output";; esac
case "$recipient" in age1*) ;; *) echo "An age public recipient is required" >&2; exit 1;; esac
if [ -e "$output" ]; then echo "Refusing to overwrite an existing backup" >&2; exit 1; fi
repository=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository/deploy"
backup_temp=$(mktemp -d)
restart_needed=0
cleanup() {
    if [ "$restart_needed" -eq 1 ]; then docker compose start web >/dev/null; fi
    rm -rf -- "$backup_temp"
}
trap cleanup EXIT HUP INT TERM
# The service is unavailable briefly; desktop sessions retain at most 15m leases.
age -r "$recipient" -o "$backup_temp/.recipient-check.age" /dev/null
restart_needed=1
docker compose stop web >/dev/null
docker compose exec -T db pg_dump -U face_service -d face_service -Fc > "$backup_temp/database.dump"
docker compose run --rm --no-deps -T web tar -C /data/backups -cf - . > "$backup_temp/blobs.tar"
cp .env "$backup_temp/server.env"
git -C "$repository" rev-parse HEAD > "$backup_temp/code-version.txt"
# Encryption writes to a temporary output and is renamed only after success.
# Keep the age PRIVATE recovery identity off this VPS.
tar -C "$backup_temp" -cf "$backup_temp/recovery.tar" database.dump blobs.tar server.env code-version.txt
age -r "$recipient" -o "$backup_temp/recovery.age" "$backup_temp/recovery.tar"
output_partial=$(mktemp "${output}.partial.XXXXXX")
cp "$backup_temp/recovery.age" "$output_partial"
chmod 600 "$output_partial"
if ! ln "$output_partial" "$output"; then rm -f "$output_partial"; exit 1; fi
rm -f "$output_partial"
echo "Encrypted service recovery bundle created; copy it off the VPS."
