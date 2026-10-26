# School pilot acceptance test — actual Windows and school hardware

Record school permission, operator, PC/Windows build, CPU/RAM, camera models,
firmware, installer SHA-256, original model hashes, NxFi service URL/public key
fingerprint, software SHA, date, locale and explicit consent. Use synthetic or
properly authorized observations only. Keep screenshots/logs private and exclude
student face templates from public issue trackers. **All real-device outcomes are
BLOCKED until the operator runs and signs this sheet.**

| Step | Action and acceptance evidence | Result / evidence |
|---|---|---|
| 1 | Install complete signed release on clean Windows; no Python/JSON setup; verify DLLs, Qt plugins, YuNet/dlib/YOLO and OSNet if baseline uses it | BLOCKED |
| 2 | Register company-only NxFi account, verify real email, login/logout/reset, create FaceRecognition school workspace | BLOCKED |
| 3 | Confirm unpaid/rejected/price-mismatched claims never activate; owner reauth and independently verified manual receipt activate exactly once | BLOCKED |
| 4 | First desktop login, local administrator setup/login, correct school/device/capacity, no stale account from previous Windows user | BLOCKED |
| 5 | Disconnect WAN before startup twice within signed term. Camera, attendance, local reports and export must work; no GUI freeze; reconnect and renew | BLOCKED |
| 6 | Revoke one installation, suspend/renew/expire school, check signed denial on successful sync and continued offline use only until valid signed expiry | BLOCKED |
| 7 | Run **two actual 720p cameras**; stop/start/unplug/replug/sleep/reboot; confirm latest frame, reconnect and clean shutdown, no frozen capture or growing queues | BLOCKED |
| 8 | Exercise authorized enrollment and original model outputs on original footage; record labeled false matches, misses, duplicate records and identity-switch cases separately | BLOCKED |
| 9 | Verify unknown and low-evidence person never silently confirms attendance; staff review and manual correction preserve the audit trail | BLOCKED |
| 10 | Cross an entry/exit line twice from both cameras; check attendance count, early-exit review, day-off logic and report grade/class values against paper records | BLOCKED |
| 11 | Fill the agreed student capacity, test next student and archived records, renewal/capacity reduction and historical record preservation | BLOCKED |
| 12 | With explicit consent, export school recovery key to separate secure media; upload encrypted DB+event-photo backup; tamper a copy and test wrong key; restore to a *copy* | BLOCKED |
| 13 | Test shutdown/disk-full/permission denied safely on disposable copy. Reconcile attendance and movement events after a simulated crash between existing separate SQLite writes | BLOCKED |
| 14 | Verify school A cannot access B's invoices/devices/backups; owner-only endpoints reject ordinary users; CSRF and password recovery work | BLOCKED |
| 15 | Switch English/Persian at login and while cameras run; a running operational UI should stay coherent until restart; check RTL/LTR, reports, emails and exported data | BLOCKED |
| 16 | Run `python -m tools.profile_runtime --seconds 120 --output pilot.jsonl` with permitted cameras. Record actual processed/capture FPS, frame age p50/p95, CPU/RAM, queue and stage timing on the weak PC | BLOCKED |
| 17 | Verify real domain TLS renewal, external email deliverability, offsite encrypted server backup and cold restore rehearsal | BLOCKED |

For each row replace BLOCKED with PASS/FAIL and attach private evidence,
reproduction steps, and a staff-signed incident decision. Compare to the
known-good Ryzen baseline and to operational requirements set by the school;
this repository supplies no fabricated i5/mobile FPS threshold or measured
false-match rate. Review false matches/missed events/identity switches with
appropriate human authority. A failed row is a NO-GO until repaired and rerun.

The automated synthetic matrix is in
[release-readiness-report.md](release-readiness-report.md). It is separate from
this field acceptance sheet. Biometric matching and cross-camera identity rules
were deliberately not changed in this hardening pass.
