# FaceRecognition

A desktop smart-attendance application combining face recognition, person detection, local tracking, optional appearance-based re-identification (Re-ID), and attendance-event management.

The project is designed to be tested on CPU-first hardware, with optional CUDA acceleration where supported. Performance and accuracy depend on the cameras, environment, model assets, and configuration; benchmark results from one setup are not universal guarantees.

## NxFi commercial platform

The same Django service now provides a Persian RTL product website, synthetic
interactive demo, school portal, invoices/support/releases and explicit owner
management. Purchases use independently verified manual payments; no live Iranian
payment gateway or cloud inference service is advertised as integrated.

- [Local development, owner bootstrap and deployment (Persian)](docs/commercial-deployment-fa.md)
- [Repository audit](docs/commercial-audit.md)
- [Commercial security and validation](docs/commercial-security.md)
- [Verified synthetic desktop/mobile website screenshots](docs/screenshots/README.md)
- [Offline licensing and school recovery (Persian)](docs/subscription-setup-fa.md)

Company name NxFi Technologies is provisional. The project makes no claim of
legal registration, certification, paying customers, or measured target-PC accuracy.
The commercial feature branch is `feature/nxfi-commercial-platform`; its baseline
`hardening/offline-first-licensing` remains unchanged. Published prices and release
binaries must be explicitly configured by the owner; there are no fake payment
successes or placeholder installers.

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
- Install the base dependencies:

      python -m pip install -r requirements.txt

- OSNet Re-ID is optional. Install its extra dependencies only when using that feature:

      python -m pip install -r requirements-reid.txt

The OSNet backend is allowed to fall back gracefully when its optional dependencies are unavailable.

The optional live-camera Re-ID diagnostic is not the production tracker. Run it from the repository root only when a camera is connected:

    python -m src.reid_camera_test

## Run

The desktop now requires a configured subscription service and an active school
account. Initial activation is online; an activated signed v2 license runs offline
until subscription expiry. Network sync is asynchronous. School-owned client-encrypted
complete backups require a separately kept recovery key. Start with the [Persian deployment and operations guide](docs/subscription-setup-fa.md).
It covers VPS setup, website/admin accounts, subscription activation, Windows
packaging, encrypted backups and disaster recovery. Payment activation is manual.
Copy the generated public `subscription-client.json` beside the application;
never ship the private server `.env` or signing/backup keys.

From the repository root:

    python src/app.py

The application stores its local database under data/attendance.db and runtime logs under logs/; these files are intentionally ignored by Git.

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
