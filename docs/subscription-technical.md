# Subscription service implementation and validation

This change extends `fix/enrollment-dialog-responsive` after the audited baseline
`a8c2189`. No work from the rejected performance branch/PR is incorporated.
It is a deployable subscription/account and database-backup implementation, not
an assertion of completed external security certification or live deployment.

## Components and authority

- `service/`: Django 5.2 LTS site/auth/admin/API, PostgreSQL persistent accounts,
  subscription windows/status/quotas, installation credentials and audit records.
  Public registration requires email verification and starts suspended with zero
  capacity. Only vendor staff can grant/extend service. Payment activation is
  manual; no payment gateway/webhook is implemented yet.
- Browser sessions: built-in Django password hashing/validation/reset/session
  machinery, secure HTTP-only same-site cookies, CSRF on all browser mutations,
  host validation, HTTPS redirect, host-scoped HSTS and CSP. API authentication
  never accepts a browser cookie in place of a device credential.
- Device API: random 256-bit credentials, SHA-256 token digests at rest, password
  auth stamp invalidation, explicit installation revocation and a 30-day rolling
  credential lifetime. PostgreSQL school-row locks serialize device admission
  and backup quota decisions across Gunicorn workers. Redis fixed-window limits
  apply per IP and account; unavailable Redis fails closed. Proxy headers are
  trustworthy only because Gunicorn is private and Caddy overwrites them.
- Leases: Ed25519-signed payload bound to application audience, schema version,
  school UUID, installation UUID and a fresh caller nonce. Every startup requires
  a fresh online response. Renew every five minutes; lease validity <=15 minutes
  and <=subscription expiry. The desktop subtracts the full request duration
  from the usable lifetime through a monotonic deadline (Linux CLOCK_BOOTTIME includes suspend), so desktop clock rollback
  and replaying an old response cannot extend an active session. Server clock/NTP
  is authoritative. Revocation has a bounded delay, not instant offline effect.
- `src/cloud/`: HTTPS account client, OS-vault-only credential persistence,
  atomic nonsecret state writes, account/backup dialogs and asynchronous control
  plane. No plaintext credential fallback. GUI and camera admission use an
  in-memory gate; no network or disk I/O in the frame gate. State fsync uses a
  separate lock so it cannot stall camera admission. Late network replies cannot
  reopen a closed session. Existing model/detector/recognition cadence and
  biometric decisions are unchanged.
- Capacity: total stored student records; admission uses the existing SQLite
  connection with `BEGIN IMMEDIATE`, so concurrent registrations cannot both
  claim the last seat. Startup/restore validate existing count and tenant binding;
  renewal also rejects a reduction below the existing count. This is not an
  active/archive licensing scheme and does not change recognition eligibility.
- Backups: SQLite backup API, integrity/schema/foreign-key/tenant/count checks,
  streamed HTTPS upload with a SHA-256 digest, server-side streaming AES-256-GCM
  with per-school HKDF key derivation and tenant AAD. No client filenames used as
  server paths. Ciphertext and directory are fsynced before metadata commit.
  Retention deletes old blobs after commit. Interrupted-upload/orphan cleanup
  is explicit and age-bounded. GCM authentication and digest verification finish
  before download emits plaintext. Each tenant can only enumerate/download its
  own records. Expired subscriptions can export existing backups from the site
  while the user account remains active. Disabling backup alone does not revoke
  an otherwise valid application subscription.
- Restore: startup only, instance lock, verified download and SQLite validation,
  rollback snapshot, checkpoint and atomic replacement. The application retains
  camera QThread references and waits for native work to return before releasing
  its instance/restore lock. Native code that never returns remains a hardware/
  driver shutdown limitation; forced thread destruction is not used. The local
  admin credential in a restored DB is checked again before opening the app.
- Deployment: Caddy automatic TLS, PostgreSQL and Redis on a private network,
  web image as non-root with read-only root FS, bounded tmpfs, dropped capabilities
  and no-new-privileges. PostgreSQL app role owns its DB but is not a cluster
  superuser/role creator/database creator. Secrets generator refuses overwrite.
  Gunicorn access log omits request URLs/query strings to avoid routine token-link
  logging. Cold server recovery bundles include database, blobs, keys and code
  revision, encrypted for a separate operator-held age recovery identity.

No biometric matching thresholds, crops, model files, voting rules, association
logic or camera format policy changed. A packaged smoke-test defect was fixed:
the smoke command now uses the existing PersonDetector backend selection instead
of unconditionally importing Ultralytics in an ONNX-only package.

## Validation

Validation was performed on a development environment, not a customer's VPS or
camera deployment. The commands are:

```sh
python -m unittest discover -s tests -v
SERVICE_TESTING=1 python -m service.manage test service -v 2
SERVICE_TESTING=1 SERVICE_TEST_DATABASE_URL=postgresql://TEST_USER:TEST_PASSWORD@127.0.0.1:5432/TEST_DB python -m service.manage test service --noinput -v 2
SERVICE_TESTING=1 python -m service.manage check --deploy --fail-level WARNING
python -m pip install pip-audit
python -m pip_audit -r requirements-service.txt -r requirements-cloud.txt --progress-spinner off
python -m compileall -q src service deploy tests
```

Use a disposable PostgreSQL test user with CREATE DATABASE privilege for the
Django test runner; production application credentials intentionally lack it.
Do not run service tests against a production database. PostgreSQL-specific
concurrency cases are skipped on SQLite. CI runs them with PostgreSQL 17 and
also exercises the non-root/read-only Docker image.

Desktop tests cover credential storage behavior, signed-lease admission,
replay/nonce/device/tenant/expiry rejection, clock rollback, network loss versus
explicit rejection, seat concurrency, shutdown/late reply, backup/restore and
rollback integrity, cross-school restore rejection, background delivery to Qt's
UI thread, expired UI/camera admission, consent, missing-config failure, config
key generation/overwrite refusal and preserving the packaged ONNX smoke path.
Existing tracking, face-cache, handoff, event and profiling tests run unchanged.

Server tests cover signature/limits, inactive/expired/future/banned windows,
credential rotation/logout/password changes, device limits/revocation, rate
limits, CSRF/session/API separation, verification replay, ownership/IDOR,
encrypted backup round trips, corrupted tags, size/quota/retention, export after
expiry, nonstaff admin exclusion and simultaneous PostgreSQL admissions/uploads.

The first dependency audit identified advisories against cryptography 46.0.7.
The declared requirement was raised to the corrected 50.x series and the audit
was rerun with 50.0.2, reporting **no known vulnerabilities** for the resolved
service/cloud requirements at that time. This is a package-advisory result, not a
proof of application security or a scan of every inherited vision dependency /
operating-system package. Rerun it before releases and after dependency updates.

Final recorded counts and stack/recovery smoke results are in the commit's
validation notes below. No camera FPS, target-hardware latency or hosting
capacity claims are inferred from functional test runtime.

## Operational boundaries

Real domain DNS/ACME issuance, SMTP delivery, Windows signing/packaging, actual
camera parity and a supervised external deployment still need real credentials,
infrastructure and devices. No VPS/domain/payment account was bought or deployed
on the user's behalf. VPS sizing in the Persian guide is a starting estimate.
Automatic off-VPS copying needs an actual destination account. The service-wide
backup/recovery guide must be rehearsed on a fresh destination, never a live
production database.

Installation UUIDs plus an OS credential vault are not TPM-backed hardware DRM.
A machine owner who patches the executable or exports credentials is outside the
security boundary of ordinary desktop licensing. There is no absolute anti-crack
or zero-vulnerability claim. Server/SMTP compromise, signing/backup key loss and
privileged local file access need operational controls. Backups are encrypted
at rest by the server, not end-to-end against the server operator. Keys must be
retained and backed up; key rotation/re-encryption is not automatic.

Consent covers the DB (including stored face data), not arbitrary outside files.
Local DB/rollback snapshots retain the existing application's at-rest protection;
restrict filesystem permissions and storage access on school PCs. Encrypted
cloud backups do not encrypt those local files. Default backup size is 128 MiB;
monitor actual growth and plan retention/storage before onboarding more schools.
The current architecture still has the audit's native driver/model shutdown
risks, shared cache-finalization workload and inference costs; this change is
not an FPS optimization pass.


## Recorded results (2026-10-23)

- Desktop/core suite: **83 tests passed**, including the pre-existing vision/event
  regressions and the new account/backup/Qt/config/smoke checks.
- Service suite against a disposable **PostgreSQL 17** instance: **20 tests
  passed**, including both real concurrent admission/quota cases.
- `makemigrations --check --dry-run`: no changes. Django deployment checks pass
  with the two documented host-policy advisories intentionally silenced. Python
  compilation, shell syntax checks and Git whitespace checks pass.
- Resolved `requirements-service.txt` / `requirements-cloud.txt` package audit:
  **no known vulnerabilities found** after moving to cryptography 50.0.2.
- Built the service Docker image and launched the Compose DB/Redis/web stack.
  All three reported healthy; app DB role was confirmed `rolsuper=false`,
  `rolcreatedb=false`, `rolcreaterole=false`. The web process ran as UID 10001,
  with read-only root FS and a writable bounded tmpfs/backup volume.
- A synthetic full-stack session passed: CSRF-bearing login page, account login,
  pinned-key/nonce lease verification, real SQLite snapshot upload, encrypted
  storage/listing/download, school DB restore plus rollback copy, renewal and
  logout. API traffic used loopback HTTP with explicit trusted-proxy headers;
  **no real-domain TLS or SMTP delivery was claimed**.
- The actual cold server-backup script produced an age-encrypted bundle with
  PostgreSQL dump, ciphertext blobs, original keys and code revision. The bundle
  was decrypted and restored into **fresh isolated PostgreSQL/Redis/blob
  volumes**. Login succeeded and an original school backup decrypted/restored
  successfully with the retained keys. Only synthetic fixture data was used.
- Caddy validated the shipped configuration. Public ACME issuance/renewal still
  needs the user's DNS/domain. Ubuntu host provisioning and Windows EXE signing
  still require the actual host/build environment; they were not simulated as
  successful deployments.

The stack exercise caught and fixed non-root source-read permissions,
Gunicorn's control-socket location on a read-only filesystem, the Compose tmpfs
list quoting, and PostgreSQL init-script permissions under restrictive umask.
The Postgres initializer is baked into a tiny image with normalized permissions.
The Windows dependency installation order and ONNX smoke path were also corrected
so the cloud conversion does not rely on an unintended dlib source build or
Ultralytics import in an ONNX-only release. Actual Windows packaging remains a
release validation step.
