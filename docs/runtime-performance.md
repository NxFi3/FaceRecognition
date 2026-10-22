# CPU camera runtime: implementation and validation

This change targets two 720p cameras on CPU-only PCs. It does not change face
matching, biometric thresholds, face-cache voting/clustering, appearance
association scores, or cross-camera identity logic. Those components remain
opaque; their calls are timed and optional work admission is bounded.

## Execution path found

The main window starts `SmartCameraWorker` per enabled camera:

1. `_open_camera` → OpenCV backend → `LatestFrameCapture` → one latest frame.
2. Tiny motion probe → Ultralytics person detector at 480 input size.
3. `SmartPersonTracker` → optional OSNet batch → existing association logic.
4. YuNet on original camera resolution → existing crop preparation → shared
   asynchronous face jobs and crossing-triggered face-cache finalization.
5. Existing identity/event handlers → SQLite attendance/events.
6. Preview-only annotations → owned QImage → direct, lock-protected mailbox →
   GUI timer → QPixmap. Preview frames are coalesced before GUI delivery.

`CameraWorker` is a legacy implementation, not constructed by the main window.
Enrollment and `reid_camera_test.py` have separate capture paths. They are not
covered by the smart-camera performance claims. No ONNX Runtime sessions occur
in the production path: YuNet uses OpenCV, person detection and OSNet use torch.

## Changes and expected impact

| Change | Reason | Metric to validate on deployment |
|---|---|---|
| One-frame mailbox, monotonic read timestamp, sequence gaps, reject frames older than 250 ms before processing | No processing backlog or duplicate consumption; avoids stale work after stalls | capture age p95, dropped/stale counts, total p95 |
| Interruptible reconnect with 0.25–5 s backoff, validation of empty/invalid frames, startup retry | Prevents hot failure loops and persistent disconnected state | failed reads, opens/reconnects, CPU while unplugged |
| Explicit raw Bayer setting per camera; default off | Removes hardware-specific automatic second-camera format assumption | successful startup on ordinary webcams |
| One shared downstream executor with a hard running-plus-queued limit | Bounds retained crops, native concurrency, and pending work | downstream depth, RSS, queue-delay p95 |
| One regular in-flight job per track; optional jobs expire after 500 ms waiting; leave an admission slot for crossings | Avoids redundant observations and outdated expensive work | downstream calls, expired/deferred/rejected counts, CPU |
| Cache futures retain their track/camera context outside the opaque result | Fixes the original completion handler's invalid tuple unpacking, which discarded pending crossings | crossing regression test and real event trace |
| Empty, quiet scene detector backoff with motion wake-up and periodic full checks | Avoids person detection when nothing changes; occupied scenes retain existing cadence | detector call rate and CPU when empty; first-person acquisition delay |
| Tentative tracks expire after three missed detector updates, mature tracks retain 18 | Reclaims one/two-observation noise tracks and crops sooner | stale-track count and RSS in noisy footage |
| Conservative native threads, including Ultralytics' lazy reset | Prevents two cameras multiplying host-sized thread pools | native thread count, CPU, detector p95 |
| Preview rates of 5/10/15 FPS, skip preview when frame processing exceeds 250 ms, consume GUI mailbox once | Reduces annotation, QImage, scaling and repeated GUI work during overload | preview/QImage/render time, GUI responsiveness |
| GUI timer polls camera shutdown; live workers are retained until finished | Prevents GUI waits, duplicate camera starts, and destruction of running QThreads | repeated start/stop and unplugged shutdown behavior |
| Burst attendance refreshes coalesce over 200 ms | Fewer repeated dashboard queries during arrival bursts | DB/report time and GUI responsiveness |
| WAL with FULL durability, atomic attendance conflict handling, day/time event index | Concurrent readers/writers and cameras avoid duplicate-insert races; indexed filtered reports | DB p95, integrity check, report query plan |

The necessary image ownership copies remain: annotations use a private preview
copy, and QImage owns its buffer. Removing these would let capture/inference and
Qt observe mutated or freed memory. Full frames are not copied for downstream
recognition; existing compact crops and cache snapshots stay bounded.

Overload behavior is explicit: optional face observations are deferred/rejected;
crossing-cache work that cannot be admitted falls back to the **existing event
handler with the track's current state**, emits a visible error and increments a
counter. No identity is invented. Cache exceptions and shutdown also flush an
observed pending crossing through that handler. This may produce an unknown
crossing rather than later cache-based attribution under overload. Validate
arrival bursts; this is a documented scheduling tradeoff, not a changed matcher.
There is no guarantee that CPU inference itself can finish inside 250 ms.

## Target-machine measurement command

Install the normal project requirements (now including psutil), configure both
cameras and an existing person-model path, and run from the repository root:

```sh
python -m tools.profile_runtime --profile balanced --seconds 120 --output i5-two-camera.jsonl
```

This launches the **actual application**, uses normal login, camera settings,
events and database, then requests a normal window close 120 seconds after the
main window opens. It does not generate synthetic attendance or bypass login.
Use representative empty, entry, crossing and occupied footage in that run.
Do not run a second application instance on the same devices. Choose a fresh
output filename each run. A native camera read that never returns can defer
closing; the GUI remains responsive and will not destroy the live worker.

JSONL snapshots are written every five seconds and at normal process exit:

- `processed_fps`: per camera, from its first processed frame (includes warm-up).
- `stages_ms`: mean/p50/p95 over at most the latest 2,048 samples per stage.
- `capture.*.read`: host read duration, **not** camera exposure/transport latency.
- `camera.*.capture_age`: read-completion to processing-start age.
- `camera.*.preprocess.*`, `detector`, `tracker`, `face_detector`.
- `downstream.face/cache` and `downstream.reid.embed_batch`; face/cache queue delay.
- `events.*`, `db.*`, `preview`, `qimage`, `render`, `processing`, `total`.
- `total`: read-completion to worker-loop completion; asynchronous identity/event
  completion and GUI presentation are separate timings, not falsely included.
- `counts`: processed, skipped capture sequences, stale frames, failed reads,
  reconnects and downstream shedding. Sequence drops are intentional latest-frame
  replacement, not camera-driver dropped-frame measurements.
- `gauges`: active/stale tracks, one-frame mailbox occupancy, shared downstream
  running-plus-waiting depth. Snapshot gauges are not historical peak values.
- `cpu_percent`: process CPU, **100% = one logical CPU**; `rss_mb` and native threads.

Stages can be nested (tracker includes Re-ID, preview includes QImage, events
include database). Do not sum them. First-frame model setup affects warm-up.
Read periodic snapshots to distinguish startup from sustained operation.
No biometric payloads, crops, or identities are logged by the profiler.
Unset `FACE_PROFILE_OUTPUT` to disable sample collection and the reporter thread.

## Conservative starting presets (not target-hardware measurements)

| Target | Profile | Preview FPS | Torch intra-op | Shared downstream workers | Total downstream capacity | Empty-scene full check |
|---|---|---:|---:|---:|---:|---:|
| Weak dual-core/mobile CPU | `low-power` | 5 | 1 | 1 | 4 | 0.5 s |
| i5 8th gen, 8 GB | `balanced` | 10 | 1 | 1 | 6 | 0.3 s |
| i5 10th gen, 8 GB | `balanced` initially | 10 | 1 | 1 | 6 | 0.3 s |
| Optional after measuring headroom | `performance` | 15 | 2 | 2 | 8 | 0.2 s |

All presets retain 720p capture, 480 person input, current occupied-scene detection
cadence, current face inputs and all identity decisions. Empty-scene backoff can
add up to the specified interval before a slow entrant is detected, plus inference
time. Motion on a normal detection tick wakes detection sooner. Confirm this
tradeoff against the camera's field of view and entrance speed.

Select a preset with `FACE_RUNTIME_PROFILE`. Overrides are
`FACE_TORCH_THREADS`, `FACE_OPENCV_THREADS`, and `FACE_DOWNSTREAM_WORKERS` (1–16).
OpenCV defaults to one thread; torch inter-op is one. OMP/MKL/OpenBLAS/NumExpr
default to one **only if not already explicitly configured in the environment**.
Check inherited settings on deployment. Restart the application after changing
profiles: the shared executor is created once. Raw Bayer is explicitly selected
through DB settings `camera_1_raw_bayer` / `camera_2_raw_bayer`, only for cameras
that support the requested GRBG format.

## Local evidence

This workspace has no cameras, final-PC CPU, person weights, torch, dlib or Qt.
The only supplied model is YuNet. No end-to-end FPS or detector speedup is claimed.

- Focused pytest coverage includes two simultaneous synthetic 720p mailboxes,
  sequence freshness, restart, initial-open recovery, corrupt-frame recovery,
  executor capacity/cancellation, expired optional work, motion scheduling,
  tentative-track cleanup, GUI mailbox/shutdown coordination, cache completion
  with opaque fake models, concurrent attendance inserts, WAL integrity, and
  indexed day filtering.
- `python -m tools.microbench_runtime`: 120 motion checks at 720p; measured
  p50 **0.676 ms**, p95 **0.817 ms** in this workspace.
- Timing-record call: **0.036 µs disabled**, **0.336 µs enabled**, averaged over
  10,000 calls. This is only one `observe()` call, not total instrumentation cost.
- `python -m compileall -q src tools tests` and `git diff --check` pass.
- Existing `--smoke-test` needs unavailable full application dependencies;
  `reid_camera_test.py` is a hardware demo, not an automated regression suite.

Lightweight regression command:

```sh
python -m pip install -r requirements-test.txt
python -m pytest -q
```

## Remaining validation and bottlenecks

- Validate full app/Qt behavior, two real camera backends, field-of-view coverage,
  crossing-event semantics and crowded footage on the intended PC. Unit tests
  use opaque models/fake Qt for orchestration; they do not prove detector quality.
- YOLO, OSNet and original-resolution YuNet likely dominate CPU. Opaque model
  cost is now measured separately. Some native libraries may create additional
  threads; inspect actual native thread counts. The Ultralytics reset workaround
  was checked against wheel source 8.4.184; recheck when upgrading dependencies.
- Keep the supplied/current person detector. No weights were available to justify
  replacement. The most promising follow-up is exporting the **same** nano model
  to OpenVINO FP32 first, with the existing `.pt` retained as fallback. INT8 needs
  representative calibration footage and accuracy validation; it is not enabled.
  Do not introduce ONNX/OpenVINO thread pools without explicitly bounding them.
- Detector preprocessing/postprocessing is inside the Ultralytics detector timing;
  finer profiling requires its installed backend, not guessed timings.
- SQLite commits remain synchronous and event/presence updates span multiple
  transactions. WAL reduces contention but does not make a whole multi-event
  attendance transition crash-atomic. Long report queries and commit latency
  should be measured before introducing a DB writer with durable delivery.
- Capture backends may buffer before `read()` and may block indefinitely in native
  code. Backend timeouts/driver support need real devices. Concurrent `release()`
  during `read()` was removed because it can crash native backends. No unsafe
  thread termination is used; hard driver hangs require driver/process recovery.
- Mature tracker lifetime remains detector-update based, not wall-clock based;
  camera outages can retain those tracks until updates resume. Revisit lifecycle
  policy with representative footage rather than changing identity association.
- Enrollment and legacy `CameraWorker` are not converted to this scheduling path.
  The repository's existing Windows workflow is malformed/duplicated; packaging
  repair and Windows smoke validation remain separate from runtime verification.
