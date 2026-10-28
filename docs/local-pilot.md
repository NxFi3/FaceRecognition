# FaceRecognition localhost integration pilot (Ubuntu source checkout)

This is **not a school deployment**: test accounts, local SQLite server and
desktop databases, a persistent local Ed25519 test signer, and a loopback HTTPS
certificate are isolated under `~/.local/share/nxfi-facerecognition-pilot/`.
The server binds only to `127.0.0.1`. The desktop uses its actual PySide6 UI,
real license verification and camera code, not `tools.desktop_dev`.

## Launch

In the same Conda/venv environment in which the 181 desktop tests and model
smoke test passed, install the existing Django service dependencies:

```bash
cd ~/Desktop/FaceRecognition
python -m pip install -r requirements-service.txt
python -m tools.run_local_pilot
```

On the first run, account credentials are created in a private 0600 file
`~/.local/share/nxfi-facerecognition-pilot/pilot-test-accounts.txt`.
Read that file locally. Use its **owner** credentials to sign in at
`https://localhost:18443/login/` (then follow the owner portal); use the
**school** credentials in the desktop. Change/revoke these if the directory is
ever exposed. The demo subscription begins with 900 places, two devices and
30 days. The pilot web site hides the corporate storefront and restricts
interactive browser sign-in to the explicit owner. School desktop API login
remains fully functional. The same owner-only website mode can be selected on
an eventual production server by setting `NXFI_OWNER_ONLY_WEB=1` in its
secure deployment environment; do this only after validating that no
existing customers require the old web portal. The owner can create additional synthetic schools and edit license
capacity/expiry via the web portal.

Certificate details: the runner creates an isolated root CA and localhost
server certificate in the private pilot directory. It sets `REQUESTS_CA_BUNDLE`
only in the desktop subprocess, leaving global TLS trust unchanged.
Firefox may warn until the locally generated pilot CA is explicitly trusted
in that browser. Do not disable TLS verification globally or trust this CA
for unrelated domains. The certificate expires after 360 days; renewal is
a deliberate operator action rather than an automatic trust replacement.

The app must have an unlocked supported OS keyring (Secret Service/KWallet)
for protected offline lease storage. Missing vault support must not be
replaced with plaintext credentials.

**To stop**: close the desktop; the local HTTPS server stops too.
To use only the owner website: `python -m tools.run_local_pilot --server-only`
and press Ctrl+C to stop. Backup blobs are local to the test service.
Never copy real school face data into the pilot directory.

**Not verified by this setup**: 2-camera accuracy/FPS on target Windows
school hardware, official Windows EXE packaging, public certificate/SMTP,
whole-server offsite restore and production consent/compliance.
