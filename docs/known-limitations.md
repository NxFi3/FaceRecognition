# Known limits and unresolved release risks

This record distinguishes tested code from externally blocked acceptance.
Current recommendation: **NO-GO for an unsupervised paying-school rollout**.

- **Windows and real cameras — BLOCKED.** The Linux test runner can open a Qt
  source window and load local models, but cannot prove a Windows EXE, native
  credential vault, Qt/dlib/OSNet parity, camera drivers, reconnects or weak-PC
  latency. The school pilot sheet gives exact required checks. The observed
  ~30 FPS/camera Ryzen history is user-reported, not remeasured here.
- **Attendance crash behavior — residual field risk.** The live smart-camera
  entry now commits the daily attendance row, movement event and optional late
  event in one SQLite transaction; exit and optional early event likewise commit
  together. Retried transitions consult the latest durable movement, including
  after a commit-before-RAM-update interruption. SQLite WAL with `synchronous=NORMAL`
  keeps a transaction atomic, but an abrupt power loss can lose the *entire*
  latest transaction. No desktop can reconstruct an uncommitted physical crossing
  that the camera never sees again. Staff must reconcile outages against footage
  or other authorized evidence. A database write error stops the affected camera
  worker with a visible error instead of acknowledging unwritten attendance;
  restart it only after storage is healthy and reconcile the outage. Historical
  rows from older releases are not
  silently rewritten or deleted.
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
- **Windows static verification is not Windows execution.** The build now checks
  original model hashes, the staged public URL/key, Qt's Windows platform plugin,
  native dlib/ONNX extensions, the reviewed OSNet cache manifest where applicable,
  and common private-data file names. A clean Windows launch without Python,
  actual Credential Manager, offline license and two cameras still need manual
  acceptance; scanning package contents cannot prove the absence of every secret.
- **Security verification limits.** Targeted OWASP-style tests and dependency
  scans are evidence for tested paths only. They are not a penetration test,
  proof of zero vulnerabilities or Windows-native DLL audit. Synthetic payment
  records in local test harnesses are not settled bank payments.
