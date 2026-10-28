FROM postgres:17-bookworm
# Normalize permissions regardless of the host checkout's umask/ownership.
COPY --chmod=755 deploy/init-postgres.sh /docker-entrypoint-initdb.d/10-app-role.sh
