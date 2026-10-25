# Independent deployment configuration review

Baseline: `3d67e03`, existing `feature/nxfi-commercial-platform` branch.
This review covers actual production/dev settings, Docker/Compose, proxy, secrets
creation, static assets, SMTP and cold-backup source. No real service credentials,
student records or public infrastructure were used by this review.

## Confirmed findings and smallest fixes

1. **Medium — requested PostgreSQL certificate verification was silently lost
   (CWE-295).** `service/settings.py` manually parsed `DATABASE_URL` but discarded
   its entire query. A URL with `sslmode=verify-full&sslrootcert=...` produced a
   Django database configuration without `OPTIONS`, leaving the driver default
   rather than the operator's requested verification policy. The parser now
   forwards the supported TLS options (`sslmode`, `sslrootcert`, `sslcert`,
   `sslkey`) and bounded `connect_timeout`. Unknown, duplicate or malformed
   options fail closed instead of being silently ignored. This is configuration
   forwarding verification, not a claim that a remote PostgreSQL TLS handshake
   or operator certificate authority was tested here.
2. **Low security / medium operational — test DB configuration could override
   production (CWE-15/CWE-20).** With `SERVICE_TESTING=0`, a populated
   `SERVICE_TEST_DATABASE_URL` still took precedence over `DATABASE_URL`. An
   isolated subprocess reproduced selection of the synthetic test database name.
   Production now rejects that contradictory configuration. PostgreSQL test runs
   continue to use it only with explicit `SERVICE_TESTING=1`.
3. **Low — malformed deployment endpoints were accepted until runtime (CWE-20).**
   Empty user-info or invalid/zero HTTPS ports, missing PostgreSQL endpoint or
   database, and empty/malformed Redis locations were accepted by settings.
   Startup now gives a generic error without echoing supplied credentials.
   Existing Redis connection/TLS query options are still forwarded unchanged.

No finding above is presented as a remotely exploitable environment-variable
injection: deployment configuration is operator-controlled. The TLS finding is a
real missing security control when an operator explicitly requires verification.

## Tests actually run

`service.tests_production_configuration` uses synthetic subprocess environments;
it never connects to PostgreSQL or Redis. It checks preservation of certificate
verification settings and secure-cookie/HTTPS defaults, production/test isolation,
malformed origins/database/cache endpoints, secret-free failure messages, normal
Compose-style configuration, and Redis query-option compatibility.

```sh
SERVICE_TESTING=1 python -m service.manage test service.tests_production_configuration service.tests_deployment --noinput -v 1
```

The combined production-configuration and existing loopback-development guard
suite passed **10 tests**. Python compilation and Git whitespace checks passed.
The parent integration process records the final whole-suite and actual Docker /
PostgreSQL / Redis / HTTPS / SMTP workflow results separately.

## Reviewed controls and remaining limitations

- Only Caddy ports are public in the shipped Compose topology; PostgreSQL/Redis
  are private. Trusted proxy headers depend on keeping Gunicorn private.
- Web runs as UID 10001 with read-only root, bounded tmpfs, dropped capabilities
  and no-new-privileges. Release artifacts are operator-managed/read-only.
- Generated signing/master/DB/Django secrets are random, exclusive-created with
  mode 0600, and not embedded in desktop/public configuration. Do not regenerate
  them while upgrading an existing deployment.
- Cold backup includes DB, ciphertext blobs, releases, `.env` and revision,
  encrypted to an operator-held age recipient. Actual restore rehearsals and
  off-VPS retention remain required; code inspection alone is not recovery proof.
- TLS verification flags are honored when configured. The private Compose DB
  connection does not newly require PostgreSQL TLS; remote DB deployments must
  select and validate an appropriate TLS policy and mount the corresponding CA.
- SMTP transport configuration and backend acceptance do not prove real-world
  delivery. Credentials, sender/domain configuration and deliverability require
  actual infrastructure. No configured SMTP exception details or credentials
  are added to application logs by these changes.
- Production image/version pinning, host hardening, monitoring, certificates,
  offsite-copy jobs and external security assessment are operational obligations;
  this review does not certify them or claim zero remaining vulnerabilities.
