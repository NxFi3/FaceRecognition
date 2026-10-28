# NxFi

An English-default, Persian-optional software and AI platform. FaceRecognition is
its first and currently only commercial product: a desktop attendance application
with person detection, local tracking, optional Re-ID, and attendance management.

The project is designed to be tested on CPU-first hardware, with optional CUDA acceleration where supported. Performance and accuracy depend on the cameras, environment, model assets, and configuration; benchmark results from one setup are not universal guarantees.

## Corporate website and first product

The same Django service provides a company homepage and Products catalogue,
a dedicated complete FaceRecognition journey, and a synthetic
interactive demo, school portal, invoices/support/releases and explicit owner
management. Purchases use independently verified manual payments; no live Iranian
payment gateway or cloud inference service is advertised as integrated.

- [Company/product architecture and migration](docs/corporate-product-architecture.md)
- [Current corporate desktop/mobile screenshots](docs/screenshots/corporate-README.md)
- [Local development, owner bootstrap and deployment (Persian)](docs/commercial-deployment-fa.md)
- [Plain-language local pilot and commercial workflow guide (Persian)](docs/guide-commercial-and-local-pilot-fa.md)
- [Repository audit](docs/commercial-audit.md)
- [Independent security audit and actual production-mode workflow](docs/production-readiness-audit.md)
- [Current production-mode screenshots](docs/screenshots/audit-final/README.md)
- [Commercial implementation and earlier validation](docs/commercial-security.md)
- [Verified synthetic desktop/mobile website screenshots](docs/screenshots/README.md)
- [Offline licensing and school recovery (Persian)](docs/subscription-setup-fa.md)

The NxFi brand is provisional. The project makes no claim of
legal registration, certification, paying customers, or measured target-PC accuracy.
Use the exact commit and its successful validation results to identify a release candidate; branch names alone do not establish release readiness. Published prices and release binaries must be explicitly configured by the owner; there are no fake payment successes or placeholder installers.

## Project layout

    FaceRecognition/
    ├── models/                 # Model assets used by inference
    ├── src/
    │   ├── app.py              # PySide6 desktop application
    │   ├── database.py         # SQLite persistence and settings
    │   ├── auth.py             # Administrator authentication
    │   ├── enroll.py           # Enrollment utility
    │   ├── export_attendance.py
    │   ├── today.py
    │   ├── reid_camera_test.py  # Optional live-camera diagnostic
    │   └── engine/             # AI and real-time camera pipeline
    │       ├── camera_capture.py
    │       ├── smart_camera_worker.py
    │       ├── smart_tracking.py
    │       ├── smart_events.py
    │       ├── global_identity.py
    │       └── reid.py
    ├── tests/                  # Fast, hardware-independent unit tests
        ├── requirements.txt
    └── requirements-reid.txt   # Optional OSNet dependencies

Runtime data such as the SQLite database, logs, caches, and downloaded datasets should stay out of Git.

## Requirements

- Python 3.11 is the recommended development runtime.
- Install desktop and client dependencies:

      python -m pip install -r requirements.txt

- Install the Django control-service dependencies when running the website/service:

      python -m pip install -r requirements-service.txt

- OSNet Re-ID is optional only when the selected pilot baseline does not use it.
  Install its extra dependencies only when needed:

      python -m pip install -r requirements-reid.txt

Preserve the validated OSNet weights and CPU dependencies for any pilot that
relies on Re-ID. Do not silently remove that capability when packaging.

## Run

**For a complete local integration run (desktop + Control Service + website),
use the isolated synthetic pilot:**

      python -m tools.run_local_pilot

It uses a separate private data directory, a localhost-only HTTPS endpoint,
temporary signing keys, and synthetic owner/school accounts. Closing the desktop
stops the local service. The owner portal is normally
`https://localhost:18443/owner/`; credentials are written to
`~/.local/share/nxfi-facerecognition-pilot/pilot-test-accounts.txt`.

To run only the local website/service and use the browser:

      python -m tools.run_local_pilot --server-only

For a separate test workspace:

      python -m tools.run_local_pilot --data-dir ~/.local/share/nxfi-facerecognition-pilot-review

Do not use real student data in the pilot, and do not disable TLS certificate
verification to bypass browser warnings. The source desktop requires a trusted
HTTPS `subscription-client.json`; `python src/app.py` alone does not launch the
Control Service, and without a valid public service configuration it cannot
activate an account.

For real deployment, start with the
[Persian commercial deployment guide](docs/commercial-deployment-fa.md) and the
[new plain-language local and commercial workflow guide](docs/guide-commercial-and-local-pilot-fa.md).
`python -m deploy.configure` only generates server configuration; it does not
publish or start the public service. Never package the server's private
`deploy/.env`, signing key, or backup master key.

The desktop normally stores its local database under `data/attendance.db` and
runtime logs under `logs/`; the local pilot deliberately stores its synthetic
service data outside the repository.

## Validation

Run the unit tests:

    python -m unittest discover -s tests -v

Run the application dependency/model smoke test without opening the GUI:

    python src/app.py --smoke-test

For a quick syntax check:

    python -m compileall -q src tests

The smoke test verifies that the YuNet and YOLO model assets and required face-recognition model assets can be loaded. It does not validate camera behavior, attendance accuracy, multi-camera identity transfer, or anti-spoofing.

## Model assets

Model files are kept under models/. The application retains a compatibility fallback for an older YuNet file placed in the repository root and for a YOLO model at the root. Confirm the provenance and redistribution terms of each model before redistributing packaged builds.

## Research and benchmarking

For research-grade results, record the code commit, model versions, camera/video settings, device, inference settings, and exact test split. Compare changes against a fixed baseline and report the metrics actually supported by the dataset annotations. Do not claim school attendance accuracy from a generic indoor tracking dataset alone.

## Production baseline profiling

Record the existing runtime without changing inference scheduling:

```sh
python -m pip install psutil
python -m tools.profile_runtime --seconds 120 --output baseline.jsonl
```

Use the normal application login and camera configuration. Recording ends after
120 seconds of camera activity; the app stays open. Close it normally.
See [metric definitions and validation](docs/runtime-profiling.md) and the
[production runtime audit](docs/runtime-baseline-audit.md). Profiling is disabled
when starting the application normally.

Current company-account/localization increment: [implementation audit, tested scope
and remaining bilingual release blockers](docs/unified-account-i18n-audit.md).
[Verified English/Persian browser and Qt evidence](docs/screenshots/unified-account/README.md).
The full legacy desktop/owner localization is unfinished; do not advertise full
bilingual support from the existence of the language selector alone.

Release acceptance: [evidence and GO/NO-GO matrix](docs/release-readiness-report.md), [deployment checklist](docs/production-deployment-checklist.md), [school pilot protocol](docs/school-pilot-acceptance-test.md), and [known limits](docs/known-limitations.md).
