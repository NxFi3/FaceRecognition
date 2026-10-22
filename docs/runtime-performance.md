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
| Known ov534 dual-camera auto-selection, with explicit per-camera overrides | Keeps native low-bandwidth capture on PS3 Eye devices without forcing unrelated webcams into Bayer | capture mode/size gauges, startup and image quality |
| One shared downstream executor with a hard running-plus-queued limit | Bounds retained crops, native concurrency, and pending work | downstream depth, RSS, queue-delay p95 |
| One regular in-flight job per track; stable-track jobs expire after 500 ms; unknown jobs get priority within the hard bound | Protects unknown observations while limiting redundant/background work | downstream calls, opportunity counts, expired/deferred/rejected counts, CPU |
| Cache futures retain their track/camera context outside the opaque result | Fixes the original completion handler's invalid tuple unpacking, which discarded pending crossings | crossing regression test and real event trace |
| Empty, quiet scene detector backoff with motion wake-up and periodic full checks | Avoids person detection when nothing changes; occupied scenes retain existing cadence | detector call rate and CPU when empty; first-person acquisition delay |
| Single-detection tracks without face evidence expire after three misses; repeat detections/face evidence keep main’s 18-miss window | Removes obvious noise without prematurely losing useful evidence | stale tracks, short-pass recognition, RSS |
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
profiles: the shared executor is created once. Known ov534 cameras use raw Bayer automatically in dual-camera mode; DB settings
`camera_1_raw_bayer` / `camera_2_raw_bayer` override that choice. Raw mode is only
appropriate for cameras that support the requested GRBG format.

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

## Quality recovery follow-up (deployment feedback)

The operator reported approximately 20.74 / 20.57 processed FPS, 108% process
CPU, 1.81 GB RAM and near-zero downstream queue depth on the real two-camera
system, but worse recognition and apparent hand-off failure. Those are
operator-provided measurements, not measurements reproduced in this workspace.
This follow-up preserves the four deployment fixes already pushed to this branch.

### Comparison against main and demonstrated failure mechanisms

- **Introduced by the first performance pass:** tentative tracks with one *or
  two* detections expired after three misses, versus 19 on main. This can discard
  accumulated face evidence and continuity during a brief occlusion. Only a
  single-detection track with no admitted face observation now uses that short
  lifetime. Repeat detections or face evidence retain main's occlusion window.
- **Introduced tightening:** per-track in-flight suppression, a reserved queue
  slot, and 500 ms expiry reduced available opportunities. They can compound
  main's existing five-frame wait measured again from completion. In-flight
  suppression is retained, but unknown admission is now every face-detection tick
  (two processed frames), measured from submission. Unknown evidence uses all
  available bounded capacity and does not expire while waiting. With near-zero
  measured queues, expiry is a possible overload failure mechanism, **not evidence
  that it caused this particular deployment run's quality drop**.
- **Pre-existing on main:** face detection every two frames, recognition interval
  five, CPU Re-ID interval floor 12, and 160-pixel cache thumbnails. None of those
  constants was introduced by the first pass. Re-ID's modulo-only refresh could
  also miss opportunities when detector ticks did not align. Scheduling now uses
  elapsed processed frames since an actual embedding, requests the first two
  observations early, and removes the unconditional CPU floor for unknown tracks.
- **Pre-existing missing callback, exposed by repaired cache completion:** cache
  identities never called `register_face_identity`. An active track might publish
  later via `observe_track`, but a departed source never did. Both regular and
  cache confirmation now register immediately; a late confirmation preserves its
  appearance profile while removing the obsolete active track link.
- **Pre-existing visibility mismatch:** worker hand-offs are stored as
  `pending_global_identity`, while the preview checked `global_identity`. The
  preview now explicitly shows `RE-ID CANDIDATE`. Existing face-proof policy is
  preserved: an appearance candidate is not silently promoted to verified
  attendance identity. The actual global similarity/margin/role/time-window
  decisions and local association scores remain byte-for-byte unchanged.
- **Event ordering:** identity reconciliation could process a deferred exit, then
  the cache callback processed it again and emitted `UNEXPECTED_EXIT`. A pending
  crossing now owns event delivery until its finalizer/fallback flushes it once.

These are code-demonstrated mechanisms, not a claim to have isolated every cause
of the observed recognition decline without the original footage and database.

### Observation and cache behavior now

Unknown tracks take priority over identified tracks. Within that group, the oldest
opportunity goes first, followed by crossing proximity and existing quality score.
Poor-quality faces are not newly rejected: a profile/blur filter must not eliminate
an unknown person's only opportunity. Same-track duplicate in-flight work remains
suppressed. Identified tracks get a periodic observation every 30 processed frames,
or an improving-quality observation after at least five frames. No acceptance or
confirmation thresholds change; the three-confirmation regular path remains.

OSNet receives an initial opportunity for new/missing-appearance tracks and an
additional early refresh (minimum three-frame spacing for the first two samples).
Unknown tracks then use the configured interval (default six), identified tracks
at least 12 frames. Entering the crossing band requests an earlier refresh; staying
in that band does not continually force the early cadence. Each inference still
uses the original OSNet preprocessing and existing local/global association logic.

Each admitted face observation now retains its completed future alongside its
compact sample. Finalization reuses the original-resolution descriptor and matcher
result from **that observation and that matcher instance**. It no longer encodes a
lossy thumbnail of the same observation again. The cache still holds at most 12
compact images and small descriptor results; no extra full-frame cache is added.
The existing clustering, vote weighting, vote ratios and margins are unchanged.
The descriptor input to final voting is deliberately the already-used full-quality
recognition crop, not numerically identical to the former thumbnail descriptor.
Legacy samples or failed/expired jobs still use the thumbnail fallback. Synthetic
same-input tests verify unchanged voting results and no mutation of reused vectors.

Cache work waits for admitted face jobs **outside** the executor, so a single-worker
pool cannot deadlock on its own queued jobs. A visible track with one sample gets
up to one second to collect the second sample required by existing voting policy.
Pending plus submitted finalizations are bounded per camera by the profile's
capacity; the shared executor's original hard limit still applies. Admission has
an explicit two-second deadline with the existing visible fallback/error behavior.
This bounds memory even if downstream work stalls. Ordinary ready caches incur no
extra fixed wait.

### Camera and crop checks

The remote branch had already restored ov534 auto-selection. The first camera was
still evaluated before the second enabled slot was counted. Both known PS3 Eye
cameras now receive the same dual-camera decision; explicit per-camera settings
still win, and generic webcams are not automatically forced into raw mode.

Synthetic GRBG input was checked through actual capture/demosaicing: red, green
and blue land in the expected BGR channels. Crop padding (20%), small-face upscale
(2.5x), local rectangle scaling, original-image ownership and preview isolation are
covered by tests. Capture now reports negotiated width/height and raw-mode gauges.
This cannot validate a physical camera's firmware, exposure or white balance.

GUI enrollment and live matching both use the existing BGR descriptor helper.
The separate CLI `enroll.py` converts to RGB. This older mismatch was not introduced
by the optimization and is **not silently changed** for an existing embedding DB.
If enrollment used that CLI, deployment validation must establish the stored-input
convention before changing descriptor inputs or re-enrolling. No global channel
swap, landmark/model replacement or biometric threshold adjustment is made here.

### Focused validation and deployment check

All **30 tests pass**, including the 17 prior cases (one worker fake accepts the new scheduling
argument and runs long enough to exercise actual deferred completion). New tests
cover delayed short-pass face opportunities, unknown priority, evidence-preserving
retirement, early/modulo-independent Re-ID, late source confirmation after loss,
exactly-once synthetic hand-off candidates, one deferred exit, encoder-result reuse,
matcher isolation, pixel ownership, GRBG color order and ov534 override behavior.

In the delayed-job worker test the three face submissions occur at processed
frames 2, 6 and 10 with only one job in flight, and the unchanged confirmation rule
identifies the synthetic track. This is an orchestration result, **not measured
walking speed or recognition accuracy**. Three admitted sample encodings followed
by two finalizations call the opaque encoder exactly three times; the finalizations
make zero additional encoding calls. No target-PC speedup is inferred from mocks.

Run the same short real-camera profile command with a fresh filename, e.g.:

```sh
python -m tools.profile_runtime --profile balanced --seconds 120 --output quality-followup.jsonl
```

In addition to the existing stages, compare:

- `camera.*.face.unknown_opportunities` / `stable_opportunities`
- `camera.*.face.time_to_identity`: first admitted face job to accepted identity;
  tracks never recognized have opportunity counts but no successful latency sample
- `camera.*.reid.observations`, `global.registered`, `global.appearance_anchors`,
  `global.registered_without_appearance`, and `global.candidates`
- `face_cache.reused_encodings` versus `fallback_encodings`
- `camera.*.downstream.cache_pending`, `cache_deadline`, `cache_admission` waiting
  time, and original executor queue delay
- `capture.*.width`, `height`, `raw_bayer`

Use the previously enrolled person entering, crossing, briefly occluding and moving
to the second camera, then an empty/identified steady scene. Unknown/bootstrap
inference should consume more CPU briefly; stable-track backoff and removal of
redundant cache encodings should offset some cost. Actual accuracy, time to identity,
cache p95 and aggregate CPU still require this real run. No model replacements,
thread-count increases, large local benchmarks or accuracy percentages are claimed.
