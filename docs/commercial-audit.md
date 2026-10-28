# Commercial-platform audit

Audit baseline: `hardening/offline-first-licensing` at `bb75a65`.
Implementation branch: `feature/nxfi-commercial-platform`.
Audit performed before implementation, against repository source rather than
production cameras or private school data. The descriptions of gaps below refer
to that baseline; the completion report identifies what this branch resolves.
No code from the rejected performance branch/PR was used.

## Existing capabilities

- `service/models.py`: one school-owner account per `School`, dated entitlement,
  active/suspended/banned status, student/device limits, backup quota/retention,
  `Device`, `Backup`, and `AuditEvent` records.
- `service/views.py`, `api.py`, `security.py`: Django login/password reset/email
  verification; CSRF on browser mutations; bearer-only device API; shared Redis
  request limits; host checking, HTTPS redirect, CSP and secure session cookies.
- `service/api.py`: Ed25519 v2 licenses bind audience/version, school UUID,
  installation UUID, nonce, unique license ID, start/end time and limits. V1
  compatibility is explicit. `src/cloud/client.py` verifies the pinned public key,
  preserves legitimate offline admission on unsigned network failures, and saves
  protected time/generation state through `src/cloud/offline.py`.
- `src/engine/camera_capture.py`: capture thread, condition/sequence and latest
  frame reference; `smart_camera_worker.py` → person detector/tracker/optional
  OSNet/YuNet → shared recognition executor and cache → `global_identity.py` /
  `smart_events.py` → SQLite → Qt preview/report UI in `src/app.py`.
- `src/database.py`: SQLite WAL, foreign keys, busy timeout and transactions;
  capacity roster archive is explicit and preserves matching/history behavior.
- `src/cloud/recovery.py`: client AES-GCM encrypted full SQLite/photo recovery
  bundle, tenant binding, safe ZIP handling, journaled replacement and rollback.
  Legacy SQLite backup compatibility is kept. Backup upload is opt-in.
- `tools/profile_runtime.py`: opt-in stage/queue/FPS/resource telemetry. No target
  CPU or accuracy claims are justified solely by source inspection or unit tests.
- `deploy/`: TLS proxy, private PostgreSQL/Redis, non-root/read-only web image,
  bounded tmpfs, least-privilege database role, secret generator and age-encrypted
  cold server recovery. The Windows builder includes explicit Re-ID asset review.

## Evidence-based baseline findings

| Area | Evidence | Classification / implication |
| --- | --- | --- |
| No commercial site | `service/urls.py` redirects `/` directly to account; `base.html` and `portal.css` offer a small form-oriented shell | Confirmed functionality gap: no homepage, product pages, pricing, SEO/sitemap, demo or buyer journey |
| No purchase ledger | Models only contain School/Device/Backup/AuditEvent; entitlement is manually mutable in `service/admin.py` | Confirmed functionality gap: no orders, price snapshots, verified payments, invoice ledger or fulfillment idempotency |
| Staff imply administration | Existing `admin.site` uses ordinary Django staff permissions; no explicit owner record or fresh authentication check | Confirmed requirement/security gap for exclusive company-owner access |
| Minimal customer portal | `views.account` only supplies school/devices/backups | Confirmed missing profile editing, orders/invoices, support, software releases and account-security guidance |
| No release artifact service | Docker copies only `service`; no release metadata/download endpoint or read-only release volume | Confirmed release-management gap; deployment must not serve arbitrary operator-controlled paths |
| No commercial notification queue | SMTP registration/reset exist, but there is no business notification outbox | Confirmed reliability gap: owner approval must not depend on an SMTP transaction succeeding |
| Broad dependency/image ranges | Several requirements are lower/upper ranges; Compose uses major/minor image tags | Confirmed reproducibility limitation: release manifests and retained image digests/wheels are needed for exact rebuilds |
| External Windows assets | Windows CI downloads YuNet, exports detector weights and only runs on main/manual | Confirmed release-validation gap: original weights/license provenance and actual Windows package parity must be checked; no Windows result inferred here |
| Alternative env files | `.gitignore` excludes `.env.*` but the baseline Docker ignore omits that pattern | Plausible accidental packaging risk, not evidence of an existing leaked secret; explicit alternate-env exclusion added |
| Logout can resurrect admission at shutdown | `client.logout` clears `_lease`/checkpoint but leaves `_envelope`; `close` calls `checkpoint_time` and can save it again | Confirmed reproduction by desktop audit; session lifecycle regression fix required |
| GUI spreadsheet exports bypass safer helper | `src/app.py` event CSV and report CSV/XLSX write user-controlled strings directly, unlike `cloud/local_export.py` | Confirmed spreadsheet-formula injection path; export-cell sanitization regression required |
| Recovery key read is capped after allocation | `recovery.import_key` calls `read_bytes()` before checking the 4 KiB bound | Confirmed avoidable oversized local-file allocation; bounded read required |
| Worker name lookup import | worker shutdown/finalization path imports nonexistent `.database` inside `engine`, then swallows the error | Confirmed display-name loss; does not justify changing event/identity decisions |
| Shutdown of native inference/driver | `run_application` retains QThreads and waits for return; capture stop uses driver release fallback | Strong structural evidence of a possible native-call hang; real driver reproduction required before architectural changes |
| UI/DB contention | GUI report/evidence paths can perform local disk work; SQLite busy timeout is 8 seconds | Plausible/unmeasured latency bottleneck; do not alter recognition cadence or event semantics speculatively |
| Local plaintext ownership boundary | Active SQLite/CSV/rollback files remain plaintext; recovery key export is intentionally school-controlled | Documented limitation: restricted Windows account/NTFS ACL and disk encryption remain deployment controls |
| Long offline revocation | Valid signed licenses continue while unreachable | Deliberate unavoidable tradeoff: next successful authenticated sync applies revocation; immediate offline kill cannot be promised |

## Test coverage inspected

The baseline has deterministic detection/tracking/event/cache/handoff and profiling
regressions under `tests/`; cloud client tests include offline restarts, clock/file
rollback, signature tampering, renewed periods, failed sync, expired admission,
tenant mismatch and capacity serialization. Recovery tests exercise corruption,
Windows-style unsafe paths, wrong keys/schools, atomic publication, disk failure
and an actual subprocess crash during restore. `service/tests.py` includes API
isolation, CSRF, password/token behavior, encrypted backup retention/quota and
PostgreSQL concurrent admissions/uploads. These are real test definitions; prior
reported counts are historical, not newly executed results for this branch.

Missing baseline coverage: verified-payment races, price tampering, duplicate
fulfillment, owner-only/step-up checks, customer order/invoice/ticket/release IDOR,
public-page navigation/SEO/mobile checks, full browser buyer journey, and formal
release artifact verification. This branch adds appropriate regression coverage;
the final report records commands actually run and unavoidable hardware gaps.

## Scope boundaries and future work

No biometric thresholds, crops, models, identity voting, cross-camera decisions,
recognition cadence or attendance semantics are candidates for speculative changes.
Local inference remains the default. A future cloud provider is an interface and
API design only, not a purchasable product or a silently enabled uploader.

Controls in this repository are not a certification of OWASP ASVS compliance,
legal registration, privacy-law approval, uncrackable DRM or zero vulnerability.
Before onboarding children’s biometric data, obtain school/guardian approvals as
applicable, agree purpose/retention/deletion and recovery custody, review Iranian
hosting/payment/legal obligations with qualified advisers, verify actual Windows
and camera parity, and rehearse recovery on a separate installation.

## Implementation trace and operational checks

The baseline audit is intentionally retained above so fixed findings are not
rewritten as if they never existed. Implementation details and remaining limits
are recorded separately:

- [Commercial operations and deployment (Persian)](commercial-deployment-fa.md):
  explicit owner provisioning, manual bank verification, read-only artifacts,
  local development isolation, outbox processing and server recovery.
- [Owner security](commercial-owner-security.md): explicit ownership record,
  provisioning/transfer and fresh authentication for sensitive actions.
- [Desktop security review](desktop-commercial-security.md): reproduced logout
  reactivation, export-cell injection and bounded file handling fixes, with tests.
- [Future inference provider](future-inference-provider.md): local-only boundary
  and proposed future API; no cloud inference product or uploader is activated.

The DevOps implementation was checked using three local-development guard tests,
two secret-generator regression tests, a production Compose `config --quiet`
against a newly generated disposable environment, Python compilation, shell syntax
and Git whitespace checks. These checks did not launch public infrastructure or
prove HTTPS/SMTP delivery. Docker/image/PostgreSQL/browser/full-suite results are
reported by the final integration run, rather than inferred from these checks.

Local plaintext student storage, guardian/school consent, credential-vault
availability, model redistribution rights, actual Windows signing/build/camera
parity, provider contracts, external penetration review and lawful financial
invoicing remain release responsibilities. Password-based owner step-up is not
phishing-resistant MFA. Dependency advisory scans cannot establish absence of all
vulnerabilities, and neither anti-spoofing/liveness nor perfect offline DRM is
claimed.


## Follow-up privacy review

The message-retention command originally selected closed ticket IDs without
locking their parent rows. A synthetic interleaving actually reproduced fresh
reply deletion after a customer reopened the ticket. The fix serializes retention
and both reply paths on the parent ticket. A PostgreSQL 17 regression verifies
that the older reply is scrubbed and the fresh customer reply survives while the
ticket reopens. A second regression covers a later retention cycle after a
previously anonymized ticket receives new replies; the parent marker must not
exclude fresh reply content forever. No production data was used in reproducing
or testing these cases.

During dependency review, advisories were identified against the old filelock
3.x dependency. The commercial branch raises the requirement to the corrected
4.2.1+ series; the final security report records the resolved dependency audit
rather than treating a version bump as proof of zero vulnerabilities.
