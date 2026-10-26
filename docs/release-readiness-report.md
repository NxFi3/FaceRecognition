# Release readiness evidence — `hardening/final-atomicity-windows`

**Decision: NO-GO for unsupervised paid-school deployment.** The disposable
production-mode service, browser and Linux desktop checks below are useful
release evidence, but they do not substitute for a native Windows build, two
real cameras on school hardware, an owned HTTPS/SMTP deployment, or offsite cold
recovery. Attendance writes now have one atomic transaction, but real-world
missed-crossing recovery after hardware failure still needs the school pilot.

Starting branch: `hardening/release-acceptance@46e58b4` on 2026-10-26;
that branch itself came from `feature/nxfi-unified-account-i18n@709590d`.
Only the new branch was edited. No production data, DNS, bank, customer mailbox,
private production key or live service was touched. The test accounts, payments,
SMTP messages, camera observations and backup images were synthetic. The prior
audit reports were checked against actual source, settings, migrations, CI and
tests; their statements were not treated as fresh execution evidence.

## What was changed

| Priority / root cause | Fix and tested scope |
|---|---|
| P1 `deploy/backup-server.sh` left private plaintext staging after failed restart and could restart an intentionally stopped web server | Cleanup always removes private staging and partial artifacts before restoring the prior service state. Four isolated shell failure/signal tests. |
| P1 `src/cloud/ui.py` allowed local CSV/recovery actions before local administrator authentication | Same administrator gate as normal local operations; negative Qt regressions. Recovery key now uses a durable sibling temporary file and atomic publication. |
| P1 `service/commerce.py` and owner mutation paths could trust a stale owner object | Current explicit owner is rechecked with locked authority/user rows at mutation time. PostgreSQL interleaving regression; this does not make ordinary Django staff an owner. |
| P1 published installer returned 404 to a verified company-only NxFi account | Authorized verified account download before a FaceRecognition subscription, while unpublished and cross-school releases remain denied. |
| P1 `src/engine/smart_events.py` raced two camera workers and advanced live state before a failed SQLite write | Serialized transitions and retry-safe state; duplicate and injected-write regressions. Biometric models/thresholds/identity votes were untouched. |
| P1 report values and export reliability | Correct grade/class mapping and closed-day denominator; write CSV through a sibling temporary file so disk errors preserve prior export. |
| P2 camera handle/frame lifecycle | Failed/reopened capture releases handles and resets stale latest-frame state; deterministic regressions. |
| P1/P2 incomplete English/Persian operational UI | Static website/owner/portal copy, server forms/errors/emails, Qt operational screens and cloud dialogs localized. Stable attendance codes are kept separate from translated labels. Dynamic school-authored descriptions stay as entered. |
| P2 active subscription portal displayed first-purchase instructions | Portal guidance now follows actual entitlement state; active/expired English/Persian regression and real browser screenshots. |
| P2 Windows release asset drift | Require exact original model asset names and committed ONNX files; record hashes rather than re-export/download new weights during each build. A Windows build itself remains BLOCKED here. |
| P1 two-transaction attendance/movement crash window | Live smart-camera entry now writes daily attendance, `PERSON_ENTERED` and optional `LATE_ARRIVAL` in one `BEGIN IMMEDIATE` SQLite transaction. Exit and optional early event are likewise atomic. Durable latest movement suppresses duplicate retries after commit-before-RAM interruption. GUI signals publish only after commit. Synthetic failure, a real child-process kill before commit, retry and concurrent-worker regressions cover this path; no schema change or biometric change. |
| P2 Windows output validation gap | The builder and Windows CI now require exact original model hashes, staged public URL/key, `qwindows.dll`, native dlib/ONNX extensions, reviewed OSNet weights when selected, and reject common accidentally packaged school/private files. These are static package checks, not a native Windows execution claim. |

The [prioritized issue inventory](release-issue-inventory.md) records P0–P3
classification, code evidence and residual risks. No new P0 issue was confirmed.
This report is not an assertion that all CWE/OWASP ASVS classes have been
exhaustively tested.

## Acceptance matrix

`PASS` means the stated *synthetic/disposable* scope passed. `BLOCKED` means
real equipment or service required for that criterion was unavailable. `FAIL`
identifies a known unresolved correctness condition.

| Criterion | Result | Reproducible evidence and limit |
|---|---|---|
| Account registration, verification, login, logout and password recovery | PASS (local service/browser); BLOCKED (external mail) | Full PostgreSQL-backed service suite; disposable HTTPS browser uses local STARTTLS spool. Public SMTP deliverability remains untested. |
| Verified company account without subscription and school workspace creation | PASS | Service regressions for account and installer; browser company/school route checks and separate school signup. |
| School/owner/cross-product authorization, CSRF/session/rate-limit boundaries | PASS (covered paths) | Full service suite and browser denies school owner route and cross-school invoice. Production-mode browser checks Secure/HttpOnly session and Secure CSRF cookies. No penetration-test claim. |
| Manual-payment rejection, wrong price, owner step-up, idempotent approval and invoice | PASS (synthetic attestation) | Browser pays no bank: unpaid/rejected/wrong-amount never activate; actual owner form attests synthetic payment; fulfilled request retried after web/PG restarts without duplicate financial records. Real bank settlement is BLOCKED. |
| Ed25519 license, activation, device limit, offline restart, outage, expiration, renewal, revocation | PASS (Linux + disposable HTTPS) | Full service/desktop suites and actual Qt login against verified loopback TLS; server device limit; proxy-stop outage; renewed license works offline; successful sync persists revocation. Native Windows Credential Manager is BLOCKED. |
| Client-encrypted backup, wrong key/corruption, DB+event-photo restore and tenant scope | PASS (synthetic local) | Desktop regressions and actual HTTPS encrypted upload/download/restore; school key held only client-side. 900-student size, external retention and cold VPS restore remain BLOCKED. |
| Server restart, PostgreSQL logical recovery and duplicate financial retry | PASS (disposable stack) | An administrator-created logical dump restored into a disposable database owned by the restricted application role; commercial counts and verified payment sum matched. Web and PG restarted and fulfilled payment was retried. This does not test loss of whole VPS/remote volumes. |
| Migration upgrade and reverse on existing-style data | PASS (disposable) | PostgreSQL `MigrationExecutor` progression `0005 → 0007 → 0005 → 0007` and full migration checks preserved synthetic accounts/IDs. Production rollback is BLOCKED without an operator backup. |
| Attendance duplicate transitions, unknown/uncertain handling, report correctness | PASS (deterministic regressions); BLOCKED (accuracy) | Desktop suite exercises changed event logic and unchanged uncertainty boundaries. No labeled real footage, false-match/miss/identity-switch rate, or original-camera-quality claim. |
| Attendance and movement atomicity after a process crash between writes | PASS (software-level synthetic) | One SQLite transaction now contains related rows; injected event and commit failures roll it back. A separate process was killed after inserting attendance but before inserting its event; neither row survived. Separate-state concurrent workers and commit-before-RAM retries create no duplicate movement. An uncommitted physical crossing can still be lost after sudden power failure, so field reconciliation remains BLOCKED. |
| Windows installer, original models/DLL/Qt/OSNet and offline launch | BLOCKED | Linux original face and person detector smoke plus synthetic static distribution-validator regressions pass; no real Windows EXE build/launch, clean PC or OSNet runtime parity was available. |
| Two 720p cameras, weak CPU latency, sleep/unplug/reconnect and stable 30 FPS | BLOCKED | No real authorized school cameras or target Windows PC; no performance number invented. `tools.profile_runtime` is available for the pilot. |
| Public DNS/ACME, production SMTP, offsite backup/cold restore and legal/data consent | BLOCKED | Only disposable loopback HTTPS/PostgreSQL/Redis/SMTP were used. [Deployment](production-deployment-checklist.md) and [pilot](school-pilot-acceptance-test.md) gates remain open. |

## Executed verification

These commands ran in the Linux executor from this branch. Counts are verbatim
from their outputs; a skip is not a pass.

```text
PostgreSQL 17, disposable loopback container:
  SERVICE_TESTING=1 SERVICE_TEST_DATABASE_URL=<disposable PostgreSQL URL> python -m service.manage test service -v 1
  Ran 199 tests in 19.543s — OK (0 skipped) on disposable PostgreSQL 17.

Linux Qt offscreen:
  QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
  Ran 181 tests in 1.855s — OK

Built service image, nonroot/read-only container:
  docker run --rm --read-only --tmpfs /tmp -e SERVICE_TESTING=1 nxfi-release:test python -m service.manage test service -v 1
  Ran 199 tests in 25.850s — OK (skipped=15: host/PG-specific cases)

Release recovery fixture guard:
  python -m unittest tests.test_release_drill -v
  Ran 1 test in 0.003s — OK

Offline license and encrypted-recovery contract:
  SERVICE_TESTING=1 python -m tools.smoke_offline_license
  PASS: signed activation, offline restart/failure, encrypted backup/full restore,
  three renewals, expiry recovery and durable revocation.

Original model smoke after installing existing onnxruntime dependency:
  python -m src.app --smoke-test
  FACE_RECOGNITION_SMOKE_TEST_OK; PERSON_DETECTOR_SMOKE_TEST_OK

Dependency/security/static checks:
  python -m pip_audit -r requirements-service.txt -r requirements.txt --progress-spinner off
  No known vulnerabilities found in the resolved Linux dependency set.
  python -m bandit -r service src deploy -f json -o /tmp/nxfi-release-bandit.json
  0 HIGH, 0 MEDIUM, 48 LOW findings (not all low findings triaged).
  python -m service.manage makemigrations --check --dry-run
  No changes detected.
  python -m service.manage check --deploy
  System check identified no issues (2 silenced).
  python -m pip check
  No broken requirements found.
  docker compose --env-file <synthetic disposable env> -f deploy/compose.yml config --quiet
  Exit 0; static Compose configuration only, not a public deployment.
```

The full production-mode browser workflow used `python -m
tools.run_production_audit --image nxfi-release:test --language en --output
docs/screenshots/release-acceptance/en`: **PASS, 14 workflow checks, 34 responsive
page checks, 15 screenshots**, Chromium `154.0.8037.57`. It starts temporary
Caddy, PostgreSQL, Redis, web and SMTP containers, a local CA and synthetic
data. See the [machine-readable English result](screenshots/release-acceptance/en/audit-production-workflow.json),
[company desktop](screenshots/release-acceptance/en/audit-production-company-desktop.png),
[school mobile](screenshots/release-acceptance/en/audit-production-school-mobile.png),
[active portal](screenshots/release-acceptance/en/audit-production-school-active.png)
and [desktop activation dialog](screenshots/release-acceptance/en/audit-production-desktop-login-en.png).
The same workflow with `--language fa` also **PASSed 14 workflow checks, 34
responsive page checks and 15 screenshots**, including the expired/renewed
portal guidance and opposite language switch; see its
[Persian result](screenshots/release-acceptance/fa/audit-production-workflow.json)
and [screenshots](screenshots/release-acceptance/fa/). These are local
synthetic artifacts, not real payments or customer pages. No live bank or public
mail provider was contacted.

On the final-atomicity branch, the disposable English production workflow was
rerun against the rebuilt service image: **PASS, 14 checks, 34 responsive pages,
15 screenshots**. Its [machine-readable result](screenshots/final-atomicity/audit-production-workflow.json)
and [verified school portal image](screenshots/final-atomicity/audit-production-school-active.png)
are separate from attendance transaction tests. The browser test exercises the
purchase/license/backup/renewal stack; the new SQLite atomicity is exercised by
the synthetic concurrent-worker and injected-failure desktop regressions.

## Security and reliability interpretation

The backup staging leak, preauthentication recovery path, stale owner authority,
two-camera attendance race and split SQLite commits were confirmed and
regression-tested. Relevant
OWASP ASVS areas include access control, authentication/session management,
transaction integrity and file handling; matching CWE examples include CWE-284,
CWE-362, CWE-664 and CWE-200. The dependency scan and Bandit are additional
signals, not a full Windows/native dependency review or proof of no exploit.
No biometric identity matching, cross-camera association, thresholds or model
weights were changed. A valid signed offline license intentionally survives an
internet outage and remote revocation is effective at the next successful
sync, so instantaneous offline revocation is impossible by design.

For unresolved items, read [known limitations](known-limitations.md). The
[deployment checklist](production-deployment-checklist.md) covers operator
secrets, migration/restore, Windows build and public service setup. The
[school pilot sheet](school-pilot-acceptance-test.md) has exact human acceptance
steps, including a clean Windows account and two real 720p cameras. Do not
upgrade a customer installation or make a GO decision using only this report.
