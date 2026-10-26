# Production deployment and upgrade checklist

Owner/operator runbook. Status here is a checklist, not a deployment record.
The repository was tested with disposable infrastructure; no public service,
production keys, customer DB or live payment was touched.

## Before deploying

- [ ] Choose an owned domain, VPS, PostgreSQL/Redis sizing, email provider and
  off-VPS encrypted backup destination. Establish legal seller identity, contract,
  student-data consent, retention and incident contacts with qualified advice.
- [ ] Use a separate staging host and domain with separate keys, DB, volumes and
  SMTP mailbox. `service.dev_settings` is only a loopback source profile;
  staging uses production `service.settings` with isolated resources. Do not bind
  two Compose projects to the same host 80/443 ports.
- [ ] Record the exact reviewed Git SHA. Preserve the current production `.env`,
  Ed25519 signing private key, public client configuration and school recovery
  keys. Never regenerate keys on an existing installation.
- [ ] Take and verify an offsite encrypted backup of PostgreSQL, backup blobs,
  `.env` and release artifacts. Practice cold recovery on a *new isolated host*
  before changing the live host. Keep school-controlled `.frkey` separately;
  losing all copies makes client-encrypted backups unrecoverable.
- [ ] Review `docs/release-deployment-review.md` and
  `docs/commercial-deployment-fa.md`; confirm operator payment instructions,
  published real prices, SMTP STARTTLS credentials, trusted HTTPS endpoint,
  disk quotas, backup retention and incident alerting.

## Fresh installation

1. Clone this SHA on the server. For the **first deployment only**, generate
   secrets and public client settings with `python -m deploy.configure --domain
   schools.example.org --email operator@example.org` using your *actual owned*
   domain/address. On an upgrade, retain the existing `.env` and public key.
2. Install Docker using the documented helper. Confirm bind mounts/volumes and
   restricted permissions on `deploy/.env`; ensure private keys are absent from
   the desktop artifact. Do not put student images or templates on the website.
3. Build/start with `docker compose --env-file deploy/.env -f
   deploy/compose.yml up -d --build`. Its web entrypoint applies migrations.
   Observe health of PostgreSQL, Redis, Gunicorn and Caddy; verify the actual
   public HTTPS certificate and redirects. Run `python -m service.manage check
   --deploy --fail-level WARNING` in the web container.
4. Provision exactly one explicit owner using the existing `provision_owner`
   command. Owner role is a separate DB record; Django staff/superuser alone does
   not convey it. Use unique strong credentials and review MFA/network controls.
5. Publish only verified FaceRecognition plans, manual-payment instructions,
   release metadata and a hash-checked **real** Windows distribution. Do not
   advertise cloud inference or an Iranian gateway; neither is integrated.
6. Register a synthetic school through the actual public website; deliver and
   use its email verification link. Place an unpaid and a rejected order and
   confirm no desktop license. Independently attest a synthetic manual payment,
   confirm one entitlement and an Ed25519 license for the configured device.
7. Turn on backup only with school consent and a stored school recovery key.
   Upload a synthetic encrypted backup and restore a copy. Check that another
   school cannot view/download its records or backup.

## Upgrade from existing installation

1. Schedule maintenance and collect current SHA, migration list, volume list,
   student capacity, device count and backup inventory. Capture the previous
   Windows installer and client public key. Do not let a fresh checkout
   overwrite `.env` or replace the signing key.
2. Perform the offsite backup and independent recovery check above. Roll forward
   the reviewed image in staging first with a copy of *authorized synthetic or
   sanitized* data. Migration `0007` localizes model display metadata only and
   uses no executable data/schema SQL; `0006` adds the independent company
   profile and preserves existing School/Device/Order IDs. Test the actual
   previous-to-new migration and rollback on disposable data before production.
3. Run the same Compose `up -d --build` on the approved host. Confirm that old
   school accounts, subscriptions, invoices, device tokens and signed offline
   leases retain their authority. Failed server sync must not invalidate a
   still-valid offline license. Keep an immediately usable previous image and
   recovery instructions; rollback of code may require DB backup restoration
   after later migrations.
4. Practice restart, failure alarms, server maintenance, SMTP queue dispatch,
   signing-key compromise response and cold restore. `deploy/backup-server.sh`
   now preserves a previously stopped web service and clears private staging
   even if restart fails. Copy its final encrypted artifact *off* the VPS.

## Windows operator build and manual acceptance — BLOCKED here

On the owner's **Windows** build PC in PowerShell, from this exact checkout:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install pyinstaller
# Supply the existing reviewed public-only config in deploy/generated/.
# If the known-good deployment uses OSNet, keep its validated cache:
$env:FACE_REID_CACHE = 'C:\SecurePilotAssets\TORCH_HOME'
python -m tools.prepare_windows_assets --torch-cache $env:FACE_REID_CACHE
cmd /c buildwindows.bat
& .\dist\FaceRecognition\FaceRecognition.exe --smoke-test
Get-FileHash -Algorithm SHA256 .\dist\FaceRecognition\FaceRecognition.exe
python -m pip freeze | Set-Content -Encoding utf8 .\build\windows-dependencies.txt
```

Follow the [asset/build details](release-deployment-review.md) for optional
Re-ID dependencies and the narrow `FACE_ALLOW_NO_REID` exception: use it only
if the actually validated pilot baseline had no OSNet. Never substitute a model
or lower a threshold to make the build pass. The operator packages the public
URL/key automatically. Customers copy the *entire* output folder and do no JSON
setup. Confirm with a second Windows account/computer and different working
folder, without Python installed; launch, authenticate, exit and restart offline.
Record exact installed models/DLLs/Qt plugins and smoke output. A Linux build,
source GUI screenshot or CI syntax check does not satisfy this gate.

After signing/reviewing the actual installer, follow
[school-pilot-acceptance-test.md](school-pilot-acceptance-test.md) on real school
hardware. Release requires a written GO decision in
[release-readiness-report.md](release-readiness-report.md).
