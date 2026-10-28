# Offline-first licensing: implementation and pilot validation

Dedicated branch: `hardening/offline-first-licensing`, based directly on
`fix/enrollment-dialog-responsive` at `5f6bd20`. No performance-branch code or PR #5
was used. This is a pilot-hardening implementation, not a claim of a completed
Windows release, external security certification, or live commercial deployment.

## Inspected architecture and findings

The existing path remains capture thread/latest-frame slot → smart camera worker
→ person detector → tracker/optional OSNet → YuNet/cached face observations →
shared bounded recognition executor → global identity/event engine → per-call
SQLite connections (WAL, foreign keys, busy timeout) → Qt latest-preview mailbox
and local reports. Recognition thresholds, voting, model weights, crop logic,
detection/recognition cadence, cross-camera handoff and attendance decisions are
unchanged. The existing opt-in runtime profiler remains available.

| Finding | Evidence | Action |
| --- | --- | --- |
| Mandatory login each startup, 900-second in-memory leases | Direct client/UI/server execution path | Replaced for v2 clients with persisted signed subscription-duration entitlement |
| Network credential expiration could make a long-offline installation count as a free slot | Admission query inspected; regression test | Count non-revoked bound credentials independently of their network expiry |
| Website revocation deleted the only token digest usable to return signed status | Direct `revoke_device` path; server regression | Preserve digest for status synchronization only; revoked devices remain forbidden from data APIs |
| Reducing capacity below historical record count blocked startup/restore | Client admission/restore code | Data stays accessible; only new enrollment/reactivation consumes an active seat |
| Backup upload was plaintext SQLite inside TLS; server could decrypt | Existing streaming implementation | New client-side AES-GCM complete bundles; retain legacy format explicitly |
| Existing backup omitted external event photos | SQLite-only snapshot vs `early_exit_review` inspected | Include photos and DB-held settings, add journaled complete restore |
| Multi-file restore can fail between DB and photo replacement | Failure injection and actual subprocess `os._exit(77)` test | Durable rollback journal, preserved previous generation, startup recovery |
| PII in early-exit evidence log | Source inspection | Remove name/code/path from that log |
| `setuptools<81` had advisory PYSEC-2026-3447 | Actual full desktop-requirements `pip-audit` | Require corrected >=83; replace deprecated path lookup with identical original asset paths |
| Local Windows builder excluded Torch/Re-ID, and expected root assets despite PyInstaller's default nested layout | Packaging inspection; no actual Windows build | Explicit Re-ID capability gate/cache inclusion, root contents layout, ORT/DLL collection, custom dlib asset hook |
| Native driver/model call might never return during shutdown | Strong structural evidence, not reproduced on real hardware | Keep existing ownership/wait semantics; do not kill native threads or redesign pipeline |
| GUI report queries/evidence writes and SQLite 8-second busy timeout could cause stalls under disk contention | Plausible, unmeasured | Document for real-footage testing; no speculative scheduling changes |
| Live DB/photo snapshot is not one filesystem-wide transaction | Structural limitation | Provide stopped-app full recovery CLI; live backup can omit a photo still being written |

Do not optimize biometric matching, caches/confirmation, camera format handling,
Re-ID association or stage cadence in this pass. Future work, ordered by benefit
and risk: actual Windows/offline asset verification (high benefit/low runtime risk),
real disk/photo growth and restore drills (high/low), measured GUI/DB contention
(high/medium), native-driver isolation if hangs are reproduced (high/high). No FPS
improvement is claimed or attempted.

## License protocol and trust

V2 Ed25519 payload: audience/version, unique license UUID, school UUID/name,
installation UUID, start/end/issue UTC timestamps, student and device limits,
backup permission, nonce and active/revoked status. Each online response is
verified with the pinned public key, current nonce and installation/school binding.
Its request age is bounded. The private signer stays on the server. Legacy v1
clients negotiate their original short online lease; v2 clients never promote a
v1 lease into a long offline license. Upgrade the server before activating a v2
client. Existing accounts/server blobs are migrated in place with no rekeying.

A successful activation saves the signed envelope plus trusted time/generation
in an approved OS credential vault. Compressed checkpoint storage avoids wasting
Windows credential-blob space. The JSON mirror is atomically replaced and is not
secret. A two-phase protected/mirror checkpoint recognizes an interrupted write
and keeps the newer protected license; restoring an older mirror after a completed
commit requires verification, rather than reverting the entitlement. A protected installation anchor survives missing metadata. Protected time,
local observed wall time, and suspend-aware elapsed time are combined; backward
jumps beyond five minutes or inconsistent/tampered state require online verification.
Periodic checkpoints run on a background job; clean shutdown also checkpoints.
Ordinary whole-file rollback cannot move the protected time floor backwards.
A fully privileged whole-machine/vault snapshot remains outside this protection.

An already activated valid client does not show online login at startup. Its
in-memory frame gate does no disk/network work. The controller starts asynchronous
sync after five seconds, then hourly on success; failure backoff doubles from
120 seconds to a 3600-second ceiling. Only one job of each kind is outstanding. Automatic backup preparation waits
for a successful background sync, avoiding repeated expensive snapshots while offline.
HTTP errors/timeouts never erase a valid offline license. Signed denial is durable;
expiry blocks operational admission but preserves data. Signed online renewal can
restore admission repeatedly, including after subscription/network-token expiry.
Password-stamp changes require account login again; they are not signed revocation.

**A long valid offline license and immediate remote revocation are mutually
exclusive.** Revocation arrives at the next successful authenticated sync; an
entirely offline old installation can continue until its signed end. Installation
UUIDs and the vault are not TPM binding, executable integrity or perfect DRM.

## Data ownership, capacity and permissions

Existing databases receive `students.capacity_archived INTEGER NOT NULL DEFAULT 0`;
all old records remain active. Explicit roster classification is reversible.
`BEGIN IMMEDIATE` serializes new enrollment and archived→active transitions.
Capacity reductions do not block existing data/restore. Archived embeddings and
attendance are retained, and **archiving does not change recognition eligibility**
in this pilot. This contractual roster policy is not a biometric exclusion switch
or a tamper-proof bound on inference identities. UI states this explicitly.

On expired/suspicious admission, local-admin authentication precedes an offline
CSV/full encrypted export/recovery dialog. The independent `tools.school_recovery`
also works without service configuration; it requires existing bound school data
and the app-instance lock. CSV sanitizes spreadsheet formula prefixes. Full
operational dashboards are not exposed as an expired read-only desktop session.

Essential roles are vendor staff (grant/renew/revoke) versus school account owner
(own devices/backups only), plus the existing local admin password. Existing
single-owner accounts are preserved. No new multi-staff school roles were added:
per-teacher permissions and per-operator local audit are not implemented and must
be evaluated before a pilot needing them. Staff must never grant staff/superuser
to customer accounts. Tenant-filtered queries, bearer-only API auth, browser CSRF
and PostgreSQL row locks remain enforced. Backup deletion is now owner-scoped,
POST+CSRF, audited and physically unlinks after commit.

## Recovery format and security

New client backups: private SQLite snapshot → ZIP/Deflate level 1 including DB
settings/embeddings/events and external `event_*.jpg` photos → streaming AES-256-GCM.
Header `FRCLIENT2` authenticates school UUID and nonce. The 32-byte school key is
never uploaded; the school explicitly exports a `.frkey` file and keeps another
protected offline copy. The file is NOT password encrypted; both possession of it
and of a backup grants decryption. Protect it with separate encrypted removable
storage/physical access policy. Lost keys cannot be recovered by the vendor.
The existing OS vault keeps the local working copy. Different keys cannot silently
replace an existing key; automatic key rotation is not implemented.

Server migration 0002 labels old backups `legacy-sqlite`, new ones `client-v2`.
The server keeps its previous per-tenant AES-GCM outer encryption. Decrypting that
outer layer returns client ciphertext for new backups, not student plaintext.
Legacy backups still download/restore as before and remain server-decryptable.
There is no automatic conversion/deletion of old backups. Retention defaults to
seven versions/one GiB; operator backup-server snapshots have their own offsite
retention and must be included in deletion agreements. Orphan-pruning is explicit.

Downloads validate size/hash; the desktop authenticates GCM before extraction,
rejects cross-school keys/bundles, unsafe Windows paths, duplicate entries,
symlinks and oversized ZIP expansion (local safety ceiling 2 GiB). Schema,
quick_check, foreign keys and DB tenant binding are validated before installation.
A preserved previous generation and atomic/fsynced journal allow rollback after
process death. Cloud restore is startup-only; the local CLI holds the same instance
lock. The restored local-admin password is checked before operational access.

Snapshots/decrypted staging/CSV/rollback data remain confidential local plaintext.
Private temp directories/files are used; POSIX modes and explicit fsync are applied.
Windows relies on the parent directory's NTFS ACL; use a dedicated restricted
Windows account/folder and disk encryption. Application-level full local database
encryption, secure erasure on SSD, seamless key rotation and TPM attestation are
not implemented. Neither secrets nor backup content are logged by the new paths.

## Storage check (actually obtained, not a school prediction)

`python -m tools.measure_backup_fixture` was run once, with synthetic data:

- 900 students, 4500 dummy 128-float64 embedding blobs: 4,608,000 bytes of blobs.
- 162,000 attendance rows (180 days); logical SQLite: 21,241,856 bytes.
- 100 incompressible dummy photo files of 256 KiB each: 26,214,400 bytes.
- Encrypted complete bundle: **33,396,164 bytes** (random fixture contents can
  slightly change the final compressed size on another run).

Cloud limit stays **134,217,728 bytes (128 MiB)**. The check does NOT establish that
900 real students or a year of real photos fit. Photo accumulation can dominate.
Use `python -m tools.backup_inventory --database data/attendance.db` to inventory
actual storage privately. A too-large cloud backup gives an explicit error; local
complete recovery remains available. Larger quotas/limits require actual disk,
proxy, temp space and concurrency planning rather than a blind constant increase.

## Validation commands

```sh
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -q
SERVICE_TESTING=1 python -m service.manage test service -v 1
SERVICE_TESTING=1 SERVICE_TEST_DATABASE_URL=postgresql://TEST_USER:TEST_PASSWORD@127.0.0.1:5432/TEST_DB python -m service.manage test service -v 1
SERVICE_TESTING=1 python -m service.manage makemigrations --check --dry-run
SERVICE_TESTING=1 python -m service.manage check --deploy --fail-level WARNING
python -m pip_audit -r requirements-service.txt -r requirements-cloud.txt --progress-spinner off
python -m pip_audit -r requirements.txt --progress-spinner off
python -m compileall -q src service tools tests hooks deploy
git diff --check
```

Use only disposable PostgreSQL test databases with a test user allowed to create
DBs. Never use production credentials for Django tests. SQLite skips the two
production-database concurrency cases. Final results are recorded below. The deployment check intentionally silences Django's
two previously documented whole-domain HSTS/preload policy recommendations.

The full dependency audit initially found PYSEC-2026-3447 in pinned setuptools
80.10.2; upgraded to 84.2.1 and required >=83. The model locator tests verify the
same original .dat paths without importing pkg_resources. Model weights are not
modified. Package-advisory checks are not an independent penetration test or a
promise of zero vulnerabilities.

## Windows and pilot checklist

No Windows EXE was built or signed in this Linux environment. Build scripts/CI
were inspected and changed; actual DLL/Qt/keyring/model inclusion must still be
verified on Windows. CI refuses a silent OSNet omission: use the full local
builder and existing validated Torch cache when the pilot uses Re-ID. Asset hashes
and dependency versions are emitted in a release manifest; freeze that environment
for repeat builds. No server secrets are bundled. No real TLS/SMTP/VPS/payment
infrastructure was purchased or publicly deployed in this pass.

Before the first school:

- Build on actual Windows x64, check all DLL/Qt/dlib/ONNX assets and manifest;
  sign the executable and verify antivirus behavior on the CPU-only machine.
- Activate once using the school's Windows account; restart repeatedly with
  internet physically disconnected, including reboot and sleep/resume. Verify
  enrollment, reports, both cameras and event parity against the known-good pilot.
- Verify OSNet is actually loaded with the same weights if baseline uses it;
  a successful ONNX smoke test alone does not prove Re-ID inclusion or parity.
- Use short TEST subscriptions (not production data) to check offline expiry,
  export access, online renewal across multiple periods, and signed revocation.
- Test rollback/tampered state and online recovery, including unavailable/unlocked
  Windows Credential Manager; never delete real checkpoint data during tests.
- Verify owner/other-school isolation, capacity reduction/archive/reactivation,
  and the local-admin password after restore.
- Export the school key, obtain consent, upload, download, and restore on a separate
  activated installation; compare attendance, settings, embeddings and event photos.
- Rehearse interrupted restore/disk-full recovery in a disposable copy, measure
  real backup growth, and agree retention/deletion and custody of recovery keys.
- Verify actual DNS/HTTPS/SMTP/password reset and a cold server restore with
  off-VPS copies. Keep signer/master keys; never regenerate them during upgrades.
- Run the existing `python -m tools.profile_runtime --seconds 120 --output baseline.jsonl`
  on real footage/hardware to check parity; no FPS number is inferred here.


## Recorded results — 2026-10-23

- `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -q`:
  **111 tests passed** (0.808 s in the recorded final functional run). Includes
  existing vision/event/cache/handoff regressions, new licensing/Qt/recovery tests,
  and an actual subprocess crash/rollback test. This runtime is not a hardware
  benchmark or a Windows result.
- Disposable PostgreSQL 17: `SERVICE_TESTING=1 SERVICE_TEST_DATABASE_URL=... python -m service.manage test service -v 1`:
  **26 tests passed**, including both real concurrency cases (1.136 s recorded).
- Non-root, read-only Docker image built successfully; `docker run --rm --read-only
  --tmpfs /tmp -e SERVICE_TESTING=1 face-subscription:hardening python -m service.manage test service -v 1`:
  **26 tests discovered, 24 passed, 2 PostgreSQL-only cases skipped** (0.975 s).
- `SERVICE_TESTING=1 python -m tools.smoke_offline_license`: **PASS** using the
  real Django API/signatures and desktop client, disposable in-memory server DB,
  real SQLite snapshots and real client encryption/full restoration. Covers
  offline restart and network failure, three renewals, expiry recovery and durable
  signed revocation. Added to service CI with an explicitly isolated in-memory DB.
- `python -m pip_audit -r requirements-service.txt -r requirements.txt --progress-spinner off`:
  **No known vulnerabilities found** after the setuptools fix. Earlier separate
  service/cloud and desktop audits also passed after remediation.
- `python -m pip check`: **No broken requirements found**.
- `SERVICE_TESTING=1 python -m service.manage makemigrations --check --dry-run`:
  **No changes detected**; `check --deploy --fail-level WARNING`:
  **no issues, 2 documented host-policy checks silenced**.
- Compilation, deployment shell syntax checks and `git diff --check`: passed.
- Actual Windows packaging/signing, live camera throughput/parity, public TLS/SMTP,
  target-PC performance, and external penetration testing: **not performed**.

Only synthetic/local test data was used. The test PostgreSQL container is disposable;
no production data was migrated or destroyed. The previous branch's Compose/cold
server-recovery results are historical and are not presented as a repeat test of
this new change. Rehearse the documented server recovery procedure before a pilot.
