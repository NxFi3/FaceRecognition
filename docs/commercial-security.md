# Commercial implementation, security review and actual validation

Date: 2026-10-25. Branch: `feature/nxfi-commercial-platform`.
Baseline: `hardening/offline-first-licensing` at `bb75a65` (unchanged).
The original/default branches are not merged or modified. NxFi Technologies is
provisional; nothing here asserts legal registration, certification, paying
customers, a live gateway, or an external security assessment.

## Implemented product boundary

Django remains the only web backend/frontend renderer. The Persian RTL website
has home, features, workflow, synthetic interactive demo, published-plan pricing,
comparison, published-release downloads, requirements, FAQ, contact/demo requests,
support, privacy, terms, registration/login/reset, plus customer and owner portals.
The local font is licensed/self-hosted; there are no marketing analytics or sensor
permissions. No real student data is queried by public pages. Ten real browser
screenshots and the machine-readable check report are in [screenshots](screenshots/README.md).

Business tables are separated from local student SQLite/templates. New migrations
0003/0004 add owner authority, plans, immutable orders, verified payments, contacts,
tickets/replies, release metadata, durable mail outbox and rejection reasons.
Existing accounts, school UUIDs, device tokens, backup formats and signer/master
keys are retained. Run migrations after a reviewed backup and provision the owner
explicitly: old staff/superuser privilege does not become company ownership.

School workflows include profile, subscription/capacity/installations, order and
printable invoice history, manual checkout, renewals, cancellation/upgrade/privacy
requests through support, tenant-scoped ticket replies, owned backup download/delete,
software download, business-data JSON export and password changes. Local student
export/recovery remains independent of paid operational access as on the baseline.

The owner console manages real plans/prices, customers and subscription windows,
explicit device revocation, order verification/rejection, trial approval, release
publication/withdrawal, support/contact requests, audits, gross verified receipts
and operational probes. Technical Django admin is separately owner/step-up gated;
it grants no user/group/owner-provisioning authority through ordinary staff roles.

## Purchase-to-license semantics

- Prices/capacity/devices/duration come from server Plan records. First order
  creation snapshots these terms, school name and billing email. Submitted browser
  price fields are ignored. One pending order per school/plan and a per-school UUID
  idempotency key prevent duplicate checkout and cross-plan replay.
- Quotes expire after seven days. Existing invoices are not repriced when the
  owner edits a plan. Initial plan seeds are inactive/unpublished and do not invent
  prices. Integer IRR is used; 10 IRR = 1 toman.
- Customer receipt references are claims, not verified payments. Browser GET,
  redirects and customer input cannot activate a subscription. Only the explicit
  owner with recent password verification can attest independent bank settlement.
- Payment amount must exactly match the immutable order. A unique normalized
  provider/reference prevents one receipt fulfilling two schools. Row locks follow
  School -> Order in checkout, claim, cancel, approve, reject and fulfillment.
- Verified payment, entitlement dates/limits, audit and notification outbox are one
  transaction. Duplicate verification does not append time twice. Failed verification
  or outbox transaction failure rolls back fulfillment. Rejection preserves the
  entitlement and immutable customer-visible reason. Paid/fulfilled orders cannot
  be retroactively rejected. Trial approval is explicit, once per school, and
  creates no revenue row.
- Renewal appends a full period to remaining valid time; there is no automatic
  daily proration or refund engine. Mid-period capacity reductions require support.
  Excess bound devices must be explicitly revoked before a smaller device plan.
- The existing Ed25519 API issues the correct signed license after entitlement
  updates. It never runs recognition in the cloud. A valid offline license still
  survives network failure until signed expiry. Immediate remote revocation while
  disconnected remains impossible; no new online-startup requirement is introduced.

## Verified findings and fixes

| Finding / evidence | Implemented control and regression |
| --- | --- |
| Default Django staff/superuser authority was broader than exclusive owner requirement | Explicit singleton CompanyOwner, CLI provisioning/replacement, no flag inference; admin and commercial auth regressions |
| Sensitive operations lacked step-up | Short password/auth-hash/owner-revision-bound step-up; expiry, rotation, CSRF, redirect and throttle tests |
| Owner could accidentally be assigned as a school and lose access | School editor rejects reserved owner user; creation/reassignment tests |
| Device-limit reductions could retain excess authorized devices | Locked school validation in commerce and technical/custom owner editors; explicit revoke required |
| Logout cleared admission but close checkpoint resurrected the old signed license | Desktop session epochs, durable logout tombstone, immediate local signout, late-response checks; actual logout/shutdown/restart and race tests |
| GUI and legacy CSV/XLSX bypassed formula escaping | Shared sanitizer; actual exported files tested; record/event decisions unchanged |
| Wrong relative DB import silently dropped names in attendance signals | Correct package/top-level import with unchanged attendance write arguments; signal regression |
| Recovery key size was checked after reading all bytes; compressed checkpoint accepted trailing data | Bounded read and strict decompression; corruption/large-input regressions |
| New commerce could otherwise trust customer claims, duplicate receipt/time or cross-tenant IDs | Server pricing, immutable snapshots, explicit owner, unique references, atomic idempotency and real PostgreSQL races |
| Retention could scrub a fresh reply during concurrent reopening | Parent-row locks across purge and replies; reproduced old interleaving and real PG regression |
| Reopened previously scrubbed tickets could retain new replies forever | Idempotent reprocessing on subsequent closed-ticket retention cycles; regression |
| Old filelock 3.32.7 reported CVE-2026-112940, CVE-2026-112913 and CVE-2026-112907 | Require >=4.2.1,<5; tested 4.2.4 (Python>=3.11); full dependency audit and desktop lock compatibility tests rerun |

Relevant ASVS control areas applied: authentication/session rotation and step-up;
server-side least-privilege authorization; CSRF and redirect validation; input/output
validation and spreadsheet escaping; tenant-scoped object access; cryptographic
license/backup verification; financial business-logic integrity; bounded request
rates; protected release file paths; secret configuration; privacy-aware logging,
retention/export and security regression testing. This is a control mapping, not
ASVS certification or a penetration-test claim.

## Artifact, privacy and operational controls

Releases are operator-installed immutable files on a read-only volume; the web
interface registers metadata, not arbitrary uploads/URLs. Both publication and
download validate basename, containment, symlink policy, size and SHA-256 on a
single file handle. Missing/corrupt releases return a safe unavailable state;
published binaries are never fabricated. Download and export are authenticated
and rate limited. Protect files against privileged in-place modification and sign
Windows binaries before external distribution.

Cloud backup remains opt-in and client-encrypted with the school's recovery key.
No automatic student image upload or cloud recognition was added. Legacy backups
retain their explicit compatibility/privacy distinction. Local templates and
plaintext recovery/CSV files still require dedicated Windows-account ACLs and
approved disk encryption; per-field matching-template encryption was deliberately
not inserted into the working recognition pipeline.

Contact requires explicit reply consent. No attachments or biometric upload endpoint
was added to support. Resolved contact messages, old closed-ticket content and old
sent emails have an operator-controlled dry-run retention command. Pending/open
messages and financial/audit records are preserved. Requests for account deletion,
refunds or legal retention exceptions are manually reviewed, not silently applied.
Cold backups/downloaded copies require separate retention/deletion agreements.
Outbox SMTP runs outside payment locks with bounded retries; mail may be delivered
more than once after a crash, while financial fulfillment remains idempotent.

Production HTTPS/private PostgreSQL/Redis, non-root/read-only web image, secure
cookies/CSP and generated secrets remain. Explicit loopback development settings
reject production DB configuration; no production signer is auto-created for a demo.
Environment templates contain no real secrets. No infrastructure was purchased or
publicly deployed. Updated cold-server backup includes releases; that new cold
backup/restore variation still needs an operator rehearsal.

## Actually run verification

| Check | Result |
| --- | --- |
| `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -q` | 129 passed, 0.847 s; baseline and 18 new desktop regressions |
| `SERVICE_TESTING=1 SERVICE_TEST_DATABASE_URL=<disposable PG17 URL> python -m service.manage test service --noinput -v 1` | 111 passed, 5.073 s; includes nine PostgreSQL-only concurrency cases |
| `docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -f deploy/Dockerfile -t nxfi-commercial:test .` | Built successfully, image `sha256:c78244b6831287cd79f2b0b8a3de40da83c65a9df069275cf1dbc6f2e4a1f74d` |
| `docker run --rm --read-only --tmpfs /tmp -e SERVICE_TESTING=1 nxfi-commercial:test python -m service.manage test service -v 1` | 111 discovered, 102 passed, nine PostgreSQL-only skipped, 5.820 s; non-root image |
| `SERVICE_TESTING=1 python -m tools.smoke_offline_license` | PASS: real desktop/server signatures, offline restart/failure, encrypted full restore, three renewals, expiry recovery, durable revocation |
| Actual Playwright/Chromium 154 loopback browser run | 30 public/auth viewport checks at 1440/390 px; interactive synthetic demo, menu/keyboard, authenticated school/owner screens, no horizontal overflow/JS errors, critical no-JS pages; ten PNG screenshots |
| `python -m pip_audit -r requirements-service.txt -r requirements.txt --progress-spinner off` | No known vulnerabilities after filelock remediation |
| `python -m bandit -r service src tools deploy -f json -o <local report>` | 18,232 lines; zero high/medium findings, 57 LOW warnings; not a clean-all-findings claim |
| `python -m pip check` | No broken requirements |
| Django migrations/deployment checks | No migration drift; no deployment issues with two documented host-scoped HSTS/preload policy checks silenced |
| Compilation, Node JS syntax, shell syntax, Git whitespace | Passed |
| Compose generated-environment config | Passed with disposable environment; not a public deployment |

Bandit LOW categories: test/smoke `assert`s, synthetic fixture passwords/empty
configuration and auth key names, controlled subprocess invocations in tests/local
profiling, and best-effort exception handling in existing camera/vault paths.
These were classified, not blindly suppressed. Inherited swallowed exceptions and
native-driver observability remain risks. Advisory scans cover the resolved Python
requirements, not every OS/native DLL or optional OSNet environment. None of the
functional test durations are hardware throughput/accuracy benchmarks.

## Remaining production blockers / Sama Cup next steps

1. Obtain actual domain/VPS/SMTP and configure persistent signing/master keys;
   provision the owner and actual prices/payment instructions. Keep originals
   during upgrades. Legal seller identity, terms, student-processing consent,
   data residency, fiscal invoices and refunds require qualified review.
2. Build/sign on real Windows x64; verify Qt/DLL/dlib/ONNX and existing OSNet cache
   without changing weights. Test actual Credential Manager, reboot/sleep/offline
   activation, expiry/repeated renewal/revocation, school-key recovery and backups.
3. Recheck two-camera/event/recognition parity and report/resource behavior on real
   authorized footage. Shared pending cache finalization, eight-second SQLite busy
   waits and native-call shutdown hangs remain inspected risks, not reproduced or
   optimized in this task. No FPS or accuracy gain is claimed.
4. Rehearse complete server restore including releases and a school restore on a
   separate machine. Configure off-VPS copies, outbox/retention scheduling, alerts
   and independent monitoring; email acceptance is not delivery proof.
5. Before real money, use a dedicated owner account and consider MFA at a trusted
   access gateway. Password step-up is not MFA. No Iranian gateway was integrated,
   no automatic refunds/tax issuance exist, and gross verified receipts are not
   net recognized revenue. A live gateway requires merchant contract, server-side
   verification and sandbox/settlement tests.
6. For Sama Cup use the public synthetic demo and labelled screenshots, then show
   isolated school checkout, pending claim (zero revenue), explicit owner approval,
   invoice and correct signed entitlement. Never mix demo fixtures and production
   accounts/payments/student data. The browser screenshot harness requires its
   operator-provided isolated fixture; it does not seed production demo users.
7. Future cloud inference is only an unused local-default provider interface and
   documented design. No paid cloud-inference product or hidden image transport
   is enabled. Full-machine/vault rollback, local administrator compromise and
   perfect offline DRM cannot be guaranteed.

See [Persian operations](commercial-deployment-fa.md), [audit](commercial-audit.md),
[commerce](commerce-implementation.md), [owner security](commercial-owner-security.md),
[desktop review](desktop-commercial-security.md) and [future provider contract](future-inference-provider.md).
