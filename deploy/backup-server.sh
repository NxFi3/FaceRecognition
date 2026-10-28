#!/bin/sh
# Cold, consistent encrypted backup of DB, encrypted blobs, release files AND server keys.
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
output_partial=
cleanup() {
    backup_status=$?
    trap - EXIT HUP INT TERM
    # Delete plaintext database/key staging even when restarting Docker fails.
    # Cleanup must not itself abort early under set -e.
    if [ -n "$output_partial" ]; then
        rm -f -- "$output_partial" || backup_status=1
    fi
    rm -rf -- "$backup_temp" || backup_status=1
    if [ "$restart_needed" -eq 1 ]; then
        if ! docker compose start web >/dev/null; then
            echo "Backup cleanup could not restart web; inspect Compose before retrying." >&2
            backup_status=1
        fi
    fi
    exit "$backup_status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# The service is unavailable briefly; activated v2 desktops keep their signed
# offline subscription validity, independent of this service outage.
age -r "$recipient" -o "$backup_temp/.recipient-check.age" /dev/null
# Probe the output directory before taking a running service offline. The final
# hard link still refuses an existing destination atomically.
output_partial=$(mktemp "${output}.partial.XXXXXX")
web_running=$(docker compose ps -q --status running web)
if [ -n "$web_running" ]; then
    restart_needed=1
    docker compose stop web >/dev/null
fi
docker compose exec -T db pg_dump -U face_service -d face_service -Fc > "$backup_temp/database.dump"
docker compose run --rm --no-deps -T web tar -C /data/backups -cf - . > "$backup_temp/blobs.tar"
docker compose run --rm --no-deps -T web tar -C /data/releases -cf - . > "$backup_temp/releases.tar"
cp .env "$backup_temp/server.env"
git -C "$repository" rev-parse HEAD > "$backup_temp/code-version.txt"
# Encryption writes to a temporary output and is renamed only after success.
# Keep the age PRIVATE recovery identity off this VPS.
tar -C "$backup_temp" -cf "$backup_temp/recovery.tar" database.dump blobs.tar releases.tar server.env code-version.txt
age -r "$recipient" -o "$backup_temp/recovery.age" "$backup_temp/recovery.tar"
cp "$backup_temp/recovery.age" "$output_partial"
chmod 600 "$output_partial"
if ! ln "$output_partial" "$output"; then rm -f "$output_partial"; exit 1; fi
rm -f "$output_partial"
echo "Encrypted service recovery bundle created; copy it off the VPS."
