# Release issue inventory (initial inspection)

Base: `feature/nxfi-unified-account-i18n@709590d`, working branch
`hardening/release-acceptance`. Historical reports are context, not fresh proof.
This inventory precedes implementation and is updated with verified results in
release-readiness-report.md. Priority: P0 immediate data/security loss, P1 release
blocker, P2 correctness/operational risk, P3 maintenance.
The final-atomicity follow-up starts from `hardening/release-acceptance@46e58b4`.

| ID | Priority | Evidence at inspection | Required action |
|---|---|---|---|
| L10N-01 | P1 | en default in service/settings.py; raw Persian operational controls across service/templates and src/app.py | Translate real display surfaces and test both directions, preserve persisted codes |
| RELEASE-01 | P1 BLOCKED | Previous reports expressly did not build/launch Windows or exercise physical cameras; this executor is Linux | Native build/vault/camera acceptance on Windows and school PCs, no inferred PASS |
| ASSET-01 | P1 pending verification | dlib asset lookup uses face_recognition_models; repository only contains two ONNX files | Execute unchanged model-load smoke and report missing dependencies/assets accurately |
| OPS-01 | P1 pending verification | Public DNS/SMTP/ACME, external backup cold restore previously not executed | Disposable local stack tests here; real infrastructure remains manual acceptance |
| RECOVERY-01 | P2 pending verification | Prior test counts do not prove actual upgrade/reverse migration and cold restart recovery | Actual migration executor/PG tests and restart/recovery drills |
| QT-01 | P2 | Qt translator loaded only in app.main while login can switch language later | Reconfigure native Qt translations with language; preserve typed credentials |
| AUTH-01 | P2 investigating | User read precedes transaction in verify(); School-only locks do not serialize company-only flows | Reproduce stale account/state operation, fix only proven failure |
| DOC-01 | P3 | README still calls platform Persian-first and tells customers to copy public JSON despite staged build resources | Align documentation with actual customer/operator boundaries |

No new P0 exploit is asserted at this point. Security, licensing and attendance
checks remain enabled. No inference thresholds, models or identity rules are
within the modification scope of this release pass.

## Verified findings after implementation

| ID | Priority | Confirmed code evidence and disposition |
|---|---|---|
| BACKUP-01 | P1 fixed | `deploy/backup-server.sh` could leave cleartext private staging after a failed web restart or signal, and could start a web container intentionally stopped before backup. Cleanup and prior-state restoration now have synthetic failure regressions. |
| RECOVERY-02 | P1 fixed | `src/cloud/ui.py` exposed local CSV/recovery actions before local administrator authentication. Actions now require the same local gate and are regression-tested. |
| LICENSE-01 | P1 fixed | A formerly designated owner retained an already-loaded owner object during a financial/admin transaction. `service/commerce.py` and owner mutation boundaries now recheck the current owner inside a lock. PostgreSQL interleaving regression covers the stale authority. |
| ACCESS-01 | P1 fixed | A verified NxFi company-only user without a FaceRecognition subscription received a 404 on the published installer endpoint. Download authorization now permits that account while keeping unpublished or other-school artifacts inaccessible. |
| EVENT-01 | P1 fixed | `src/engine/smart_events.py` could create duplicate entry transitions when two camera workers touched a person together, and advanced live state before a failed SQLite write. Shared-state serialization and retry-safe state have synthetic regressions. The subsequent branch makes related SQLite commits atomic. |
| EVENT-02 | P1 fixed in `hardening/final-atomicity-windows` | `src/database.py` previously committed attendance before `PERSON_ENTERED` in a second transaction. `record_movement_transition` now commits related entry/exit events together, serializes the durable latest-movement check and suppresses committed retries. Fault injection covers event insert, companion insert and commit failure; a separate process kill between inserts confirms rollback. |
| WIN-01 | P2 fixed in `hardening/final-atomicity-windows` | `buildwindows.bat` and Windows CI checked only EXE/two ONNX assets after packaging. They now run a static validator for original face models, public URL/key, Qt/dlib/ONNX native files, OSNet manifest and common private-data filenames. A real Windows build/launch remains BLOCKED here. |
| REPORT-01 | P1 fixed | Grade/class export fields were reversed, closed-day percentage numerator included unrelated dates, and an export write error could truncate an existing CSV. Fixed with report and file-error regressions. |
| CAMERA-01 | P2 fixed | Capture failure/reopen paths could retain stale frames or an unsuccessful camera handle. Deterministic lifecycle regressions cover release, timestamp reset and restart behavior. |
| CLOUD-01 | P2 fixed with residual risk | Local recovery key publication could precede durable write; now staged and atomically published. A failed consent-preference save now disables new uploads in that process and reports the failure; persisted prior opt-in still needs operator retry after storage recovers. |
| L10N-01 | P1 fixed for static controls | Static website, owner panel, Qt operational controls, desktop-cloud dialogs, service forms/errors and Persian catalog are translated and tested; owner-entered legacy descriptions and OpenCV pixel HUD text retain source/ASCII language. |
| PORTAL-01 | P2 fixed | The active school portal still displayed first-purchase instructions. Entitlement-aware guidance now has English/Persian active/expired regressions and a real browser screenshot. |
| RELEASE-01 / OPS-01 | P1 BLOCKED | Actual Windows EXE, native vault, school cameras, field metrics, external DNS/SMTP, offsite cold recovery, and legal/operator acceptance require infrastructure unavailable in this executor. They are not inferred from Docker/Linux tests. |

The severity describes release impact; these are code-confirmed issues, not a
claim that all OWASP/CWE classes have been exhaustively tested.
