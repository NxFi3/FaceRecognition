# Release deployment and packaging review

Reviewed baseline `709590d` on `hardening/release-acceptance`. This review read the
README, both recent audits, the Persian deployment/subscription guides, settings,
Docker/Compose/Caddy, initialization/backup helpers, release preparation, model
location hook and CI source before changing them. No production resource was
contacted and no key was rotated.

## Prioritized inventory and implemented fixes

| Priority | Source / evidence | Disposition |
|---|---|---|
| P1 | `deploy/backup-server.sh`: `set -e` cleanup attempted restart before deleting private staging. Synthetic baseline with failing `pg_dump` and restart returned8 and left `database.dump`; `.env` would likewise remain if failure happened after copying it. | Fixed: cleanup always removes staging/partial output before restart; restart failure is explicit/nonzero. Four deterministic host-shell tests cover failure/state/signals/no-overwrite. |
| P1 | Same helper set restart_needed unconditionally; a backup launched during deliberate maintenance would start a previously stopped web service. Signal traps cleaned up but did not terminate execution. | Fixed: inspect initial running state, stop/restart only when originally running; signal handlers exit129/130/143 and EXIT performs cleanup once. SIGTERM test confirms no later tar snapshots execute. |
| P2 | `tools/prepare_windows_assets.py` accepted any four `.dat` files by count; it did not require the names used by the original model loader. | Fixed: exact original filename and nonempty-file checks. A synthetic directory with four incorrect filenames passes the old six-file count but is rejected by the regression. Actual native model loading remains a separate smoke gate. |
| P2 | Windows release workflow downloaded unpinned latest YuNet and re-exported YOLO on every build instead of retaining the chosen commit's models. | Fixed: require tracked committed original ONNX files, check size and record SHA-256; model changes/export stay in the separate reviewed export workflow. No weights, thresholds or runtime providers changed. |
| P2 | Fresh Windows builder staged client settings before the dependency diagnostic. | Moved staging after the dependency check so a clean builder receives the intended installation error. Generated public configuration remains bundled, never a customer setup task. |
| P1 acceptance | Native Windows build/launch, actual Credential Manager and pilot OSNet/cache/DLL/Qt parity cannot be executed in this Linux environment. | **BLOCKED**, not inferred from Python tests or source inspection. Exact checks below. |
| P1 acceptance | Public DNS/ACME/SMTP delivery, off-server backup storage and operator cold restore need the operator's real infrastructure. | **BLOCKED** for production acceptance. Local disposable Docker/TLS proof is a different scope. |
| P2 | Dependency requirements intentionally have ranges/unpinned native packages, not a complete reproducible per-Windows release lock. | Record the actual successful Windows environment and asset manifest; do not invent a lock from partial Linux metadata. No dependency removed. |

The original dlib package assets are actually installed in this environment:
shape_predictor_68_face_landmarks.dat (99,693,937 bytes),
shape_predictor_5_face_landmarks.dat (9,150,489),
dlib_face_recognition_resnet_model_v1.dat (22,466,066),
mmod_human_face_detector.dat (729,940). These sizes are observations, not a
provenance guarantee. Repository ONNX files exist. Root's separate actual original
model smoke after installing the existing onnxruntime dependency succeeded;
that says nothing about Windows loading, cameras or OSNet identity quality.

## Existing deployment properties verified by inspection

- Production uses validated HTTPS public origin, secure HttpOnly session cookies,
  CSRF protection, DEBUGfalse, explicit PostgreSQL and Redis, pinned Ed25519
  public verification at the desktop. Production rejects the test database override.
- Private DB/Redis ports are not published. PostgreSQL init creates the restricted
  application role; existing data volumes are not reinitialized. Compose health
  probes DB/Redis/web; the web image uses UID10001, read-only root, dropped caps,
  tmpfs staging and read-only release artifacts. Automatic restart does not replace
  an operator's alerting, disk/certificate/mail monitoring or a migration rollback.
- Development requires explicit opt-in and refuses production database URLs.
  Staging should use production settings with **separate** domains, secrets,
  Compose project/volumes and synthetic users, never SERVICE_TESTING in public.
- Existing `.env` and signing/backup keys must survive upgrade; deploy.configure
  refuses overwriting. Back up first. Apply only reviewed migrations; do not
  downgrade a live schema merely because a disposable migration test passed.
- Server backup contains DB, blobs, releases, `.env`, commit ID and encrypted
  final archive. Local plaintext staging exists only in a private temporary
  directory and is now cleaned even on restart failure/interruption. SIGKILL or
  machine power loss cannot execute shell cleanup; operators must inspect private
  leftover directories after a crash. Do not log their contents.
- Windows distribution includes public origin/key and locale resources. Never
  include deploy/.env, the private signing key, school data or recovery keys.

## Executed targeted checks

```sh
SERVICE_TESTING=1 python -m service.manage test service.tests_release_deployment service.tests_production_configuration service.tests_deployment -v1
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p test_release_deployment_assets.py -v
bash -n deploy/backup-server.sh deploy/install-ubuntu.sh
python -m compileall -q deploy tools/package_client.py tools/prepare_windows_assets.py
git diff --check
```

Host shell tests exercise the real helper with disposable command doubles for
Docker/age/git. They prove interruption/cleanup/order/state logic, **not age
cryptographic correctness, a real cold server restore or real Docker service
restart**. The four tests deliberately skip in the minimal service image, where
host deployment scripts are absent. Model validation is in the desktop suite
because tools and original model packages are not shipped inside that image.
Full-stack/migration/browser results belong in the main release report.

## Required Windows operator procedure — acceptance remains BLOCKED here

Use a disposable authorized pilot installation and synthetic records. Preserve
any existing installed data/configuration separately before upgrade. Run from
PowerShell in a clean checkout of the reviewed release commit:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
# If the validated pilot uses OSNet, install original extras and supply its cache:
python -m pip install -r requirements-reid.txt
$env:FACE_REID_CACHE = 'C:\SecurePilotAssets\torch'
python -m pip check
python -m unittest discover -s tests -v
python -m src.app --smoke-test
python -m tools.prepare_windows_assets --torch-cache $env:FACE_REID_CACHE
cmd /c buildwindows.bat
& .\dist\FaceRecognition\FaceRecognition.exe --smoke-test
Get-FileHash -Algorithm SHA256 .\dist\FaceRecognition\FaceRecognition.exe
python -m pip freeze | Set-Content -Encoding utf8 .\build\windows-dependencies.txt
Get-FileHash -Algorithm SHA256 .\models\*.onnx
```

If dlib needs a compiler, install the approved build toolchain or the same reviewed
binary distribution used by the Windows CI, then record its exact source/version.
Do not silently substitute model/backend dependencies until parity is validated.
Only if the actual known-good pilot did not use OSNet may you explicitly choose
`$env:FACE_ALLOW_NO_REID='1'` instead of FACE_REID_CACHE and use the matching prepare
flag. Do not apply that exception to an OSNet deployment.

Before these commands, the **operator** provides the existing deployment-generated
public config in deploy/generated/subscription-client.json. Do not regenerate
production keys. If recovering only the public config, stage an already verified
public config using `python -m tools.package_client --source C:\SecurePilotAssets\subscription-client.json --output deploy/generated/subscription-client.json`;
never substitute a different signing key. The customer receives the full output
folder and performs no JSON editing/copying.

On a clean second Windows account/computer with no Python installed: copy the
whole output folder; launch from a different working directory; verify Qt plugins,
fonts, public config and original model files; sign in with the authorized test
NxFi account, then local admin; verify actual Windows Credential Manager storage.
Close normally, disconnect internet and restart repeatedly within the signed term.
Restore connectivity, renew, suspend/revoke then synchronize; expiration must keep
local data and a recovery/export route. Confirm no private key or school test data
was packaged. Retain screenshots/logs/version manifest and actual results.

Run two real cameras, unplug/replug one, stop/start/reboot/sleep, enrollment,
reports and encrypted backup/restore on a **copy** of the school DB. Compare the
known-good baseline without changing biometric thresholds. Record CPU/RSS/FPS and
frame latency on the target PC with tools.profile_runtime; do not replace actual
measurements with Linux/strong-machine results. Complete the root pilot checklist
before a GO decision.

Targeted results actually obtained in this environment: **14/14 host deployment
and existing production/development configuration tests passed (1.671s)**;
**1/1 model-filename packaging regression passed (0.081s)**. POSIX shell syntax,
Python compilation for touched helpers and Git whitespace checks passed. No
actual Windows execution, public ACME or real off-server cold restore is claimed.
