# Known limits and unresolved release risks

This record distinguishes tested code from externally blocked acceptance.
Current recommendation: **NO-GO for an unsupervised paying-school rollout**.

- **Windows and real cameras — BLOCKED.** The Linux test runner can open a Qt
  source window and load local models, but cannot prove a Windows EXE, native
  credential vault, Qt/dlib/OSNet parity, camera drivers, reconnects or weak-PC
  latency. The school pilot sheet gives exact required checks. The observed
  ~30 FPS/camera Ryzen history is user-reported, not remeasured here.
- **Attendance crash window — known FAIL for full atomicity.** The existing daily
  attendance row and movement-event row use two SQLite transactions. This pass
  serializes live transitions and permits retry after a caught write error, but a
  process death between commits can still leave inconsistent evidence requiring
  staff reconciliation. Do not claim exactly-once crash atomicity.
- **Biometric quality — BLOCKED.** No authorized field footage or target i5 is
  available. False-match, missed-event and identity-switch rates cannot be
  inferred from synthetic tracker tests; matching thresholds/identity rules were
  intentionally not changed. Staff must review uncertain cases and use existing
  manual correction/review controls.
- **Consent under storage failure — known operational risk.** A failed backup
  preference write disables further automatic uploads in that process and warns
  the user. An older saved opt-in may survive reboot if the disk cannot save the
  opt-out. The school must free storage/retry the opt-out and verify persistence;
  in-flight uploads are not retroactively cancelled.
- **Locale data — P2.** Reviewed static web/Qt/service copy is localized. Names,
  plan descriptions, release notes and other owner-entered legacy records retain
  their entered language; no automatic translation is asserted. Pixel diagnostics
  drawn with OpenCV's ASCII-only font stay ASCII to preserve camera performance.
- **Offline tradeoff.** A valid signed one-year license continues offline until
  signed expiry, so remote revocation takes effect at the next successful sync.
  A privileged local executable/vault snapshot and stolen server signing key
  require separate operational controls. Fully offline desktop DRM is not
  tamper-proof.
- **Backup and server recovery.** Client backups need the school-controlled key;
  the server cannot recover it. Local restored/rollback files and CSV exports need
  Windows ACLs and disk encryption. A disposable logical PostgreSQL restore and
  server restart do not prove external offsite transfer or VPS cold restore.
  The 128 MiB per-file cloud backup cap remains; measure real 900-student photo
  volume before promising fit.
- **Operations — BLOCKED.** No owned DNS/VPS, public ACME certificate, external
  SMTP delivery, remote alerts, code-signed Windows release, offsite retention,
  legal seller/consent/data-residency sign-off, or trained recovery operator was
  available. Owner password step-up is not MFA. Manual payment works only after
  independent owner verification; an Iranian gateway/refunds/fiscal invoices
  remain unintegrated.
- **Security verification limits.** Targeted OWASP-style tests and dependency
  scans are evidence for tested paths only. They are not a penetration test,
  proof of zero vulnerabilities or Windows-native DLL audit. Synthetic payment
  records in local test harnesses are not settled bank payments.
