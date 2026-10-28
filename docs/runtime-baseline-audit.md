# Production runtime baseline audit

Baseline: `fix/enrollment-dialog-responsive`, commit
`abe1b71e8d78c93fb81565deac0110726cac1f9d`. No code or behavior from
`perf/bounded-cpu-camera-runtime` / PR #5 was used. The reported ~30 FPS per
camera on a Ryzen 9 7900X is a deployment observation supplied by the user,
not a measurement made in this environment. This pass does not optimize FPS.

## Execution path (before instrumentation)

1. `MainWindow.start_camera` creates up to two `SmartCameraWorker` QThreads,
   sharing `AttendancePresenceState` and `GlobalIdentityManager`. Settings
   select devices, roles and gates. Both enabled cameras cap requested FPS at
   30. The current Linux raw-Bayer condition uses the incrementally populated
   enabled-slot list (normally only the second camera gets raw Bayer).
2. Each worker opens `LatestFrameCapture`, with its own daemon capture thread.
   The platform-specific opener tries DSHOW/MSMF/ANY on Windows or V4L2/ANY on
   Linux, requests buffer size 1 and configured dimensions/FPS (the app currently
   requests 1280x720; actual driver negotiation is authoritative). The capture
   thread reads continuously, optionally demosaics GRBG, and publishes one
   frame reference plus increasing sequence under a Condition. The consumer
   waits for a newer sequence; overwritten frames do not queue. Backend/device
   buffering before `read()` is outside this guarantee.
3. Each worker owns a person detector, YuNet, tracker, FaceMatcher and optional
   OSNet x0.25 extractor. Person-model resolution prefers a configured PT's ONNX
   sibling if present, otherwise existing candidates; ONNX runs CPU-only with
   sequential ORT, one inter-op and `FR_ONNX_THREADS` intra-op (default 1).
   PT uses Ultralytics. Person detection runs every third **processed** frame
   on CPU/ORT, every second on GPU. Requested person input is 512 (static ONNX
   shape takes precedence). Original-resolution frames feed downstream crops.
4. `SmartPersonTracker.update` runs on person-detection frames: prediction,
   optional batched OSNet crops, association, geometry/appearance updates,
   creation and expiry after more than 18 missed detection updates. Between
   detections, currently visible tracks are predicted. OSNet runs when due,
   bootstrapping, or an active track lacks appearance; CPU interval is at least
   12 processed frames. Optional-dependency failures disable Re-ID with status.
5. Global identity observation runs for freshly observed tracks when Re-ID is
   available. A shared RLock protects profiles, links, settings cache and
   candidate decisions. Face registration anchors profiles; accepted handoffs
   reach the event engine before crossing checks. Profiles keep at most eight
   embeddings each; link state expires, while profiles are daily-scoped.
6. YuNet runs independently every second processed frame at 0.75 dimensions
   (minimum 160), with boxes/landmarks mapped back. Fresh detections reach the
   current face-crop/quality/cache logic. Tracked recognition is gated by five
   processed frames and two pending tracked futures per camera; face-only
   fallback allows one pending job per camera. Three-observation confirmation
   and all matching/voting rules are unchanged.
7. All cameras share one ThreadPoolExecutor with 1–2 workers. This bounds active
   recognition concurrency, **not its internal work queue**. Cache finalization
   uses that same executor, snapshots up to 12 compact samples, reuses available
   embeddings and encodes missing ones. Dlib predictor/encoder models are
   lazily cached per executor thread, not reloaded per recognition. Finished
   results are consumed on a subsequent camera frame, then update identity/events. Pending unresolved
   crossings wait for this existing witness/finalization path.
8. `AttendanceEventEngine` coordinates shared daily presence and crossing
   reconciliation. Entry requires a face witness. Track disappearance is not
   an exit. Worker suppresses UNKNOWN_PERSON persistence. `mark_attendance`
   uses a unique student/date conflict guard. Each event/attendance operation
   opens its own SQLite connection, applies foreign keys/WAL/NORMAL/8s busy
   timeout, commits and closes synchronously on the caller thread.
9. At most 15 Hz per worker, overlays modify the consumed frame, BGR QImage
   wrapping plus `.copy()` detaches its lifetime, and a DirectConnection stores
   the latest QImage under a short lock. The GUI's 33 ms timer copies the mailbox
   dictionary, creates/scales QPixmaps and updates widgets. No queued frame
   signal backlog. Event alerts query SQLite every 500 ms; dashboard/report
   work runs on the GUI thread.

## Findings classified by evidence

“Strongly evidenced” means visible in the production control/data flow; it
is not a claim of measured performance impact. None of the model costs below
has been benchmarked on the deployment hardware in this pass.

| Finding / source | Classification | Consequence and next measurement |
|---|---|---|
| Cache finalization has no submission cap (`schedule_face_cache_finalization`); it shares recognition's executor | Strongly evidenced | Burst of unresolved crossings/track churn can retain snapshots/tracks and delay recognition. Measure pending/high-water, queue delay, cache service time. Ordinary recognition has finite per-camera pending limits. |
| `stop_camera` waits up to 5s per camera on GUI thread, ignores timeout, clears ownership; shared pool is never explicitly shut down | Strongly evidenced | UI freeze; potential live-QThread destruction/restart overlap and interpreter exit waiting for running native tasks. Requires blocked-backend test before a lifecycle fix. |
| Capture error persists after ten failures; normal worker restart is via supervisor after 1.5s; backend `read()` may block indefinitely | Strongly evidenced control flow; device hang unmeasured | Measure failures, restarts and timeouts. Capture stop may release a handle concurrently with blocked `read()`. Do not alter driver policy without actual camera validation. |
| `_configure_cpu_runtime` returns before the OpenCV limit when torch is absent, or selected device starts with CUDA (even if detector is CPU ORT) | Strongly evidenced | Native thread count can differ substantially by installation/settings. Measure process threads/CPU first; do not impose new pools this pass. |
| GUI renders each mailbox image every 33ms even without replacement; workers produce at most 15Hz | Strongly evidenced duplicate work | Measure repeated renders and per-camera conversion/scaling time. Avoiding repeated scaling is a future candidate, provided resize behavior stays correct. |
| Synchronous per-event transactions and settings reads; identity settings refresh opens five connections under shared lock once per second when requested | Strongly evidenced blocking paths | DB event/settings and global-observation latency can explain tail stalls. WAL already enabled; do not blindly add writer threads or change transaction semantics. |
| Event presence membership check and attendance write are separated by unlocking; attendance and event rows use separate transactions | Strongly evidenced race window; duplicate event occurrence unmeasured | Two concurrent entry paths could emit duplicate movement events despite unique attendance. Needs controlled concurrency tests and explicit semantic review, not a perf patch. |
| `_mark_attendance_event` imports `.database` from `src.engine`, then swallows failure | Strongly evidenced correctness defect | Successful attendance payload falls back to blank code/name. Deferred here to avoid unrelated UI changes; no inference consequence. |
| Tracker update includes synchronous OSNet batch; cache finalization may run several missing embeddings; per-worker model instances | Plausible CPU/RAM bottleneck, unmeasured | Measure actual OSNet/face/cache invocation counts and inclusive durations. No model/session sharing without thread-safety and quality validation. |
| Global candidate scan compares profiles and checks track links under a shared RLock | Plausible contention/scaling bottleneck, unmeasured | Time `observe_track` including lock wait, count qualifying candidates and accepted links. Do not change identity decisions. |
| Full-frame QImage detachment, compact-cache copy and async cache snapshot copies | Strongly evidenced copies; avoidability unmeasured | They protect ownership/lifetime. Removing them speculatively risks mutable crops or dangling GUI buffers. No unconditional input frame copy in the main worker. |
| FaceMatcher crop encoding may run again for missing cached embeddings; embeddings already reused when available | Plausible workload cost, unmeasured | Count and time actual opaque recognition calls, including cache fallback, before calling this redundant. |
| Existing latest-value stage logs mix recent samples from different frames, and FPS excludes parts of preview work | Strongly evidenced instrumentation gap | Add independent invocation histograms, processed-frame completion timing and actual queue counters. |
| Date predicates such as `date(timestamp)` and synchronous alert/report refresh | Plausible DB/GUI cost, unmeasured | Existing timestamp/student indexes do not necessarily serve expression filtering. Need real DB cardinality/query plans before changing indexes/queries. |
| Legacy `CameraWorker`, enrollment and diagnostic workers coexist with SmartCameraWorker | Maintainability-only for production performance | MainWindow uses SmartCameraWorker; don't optimize unused branches or remove enrollment compatibility. |
| Re-ID module docstring says not wired live although tracker uses it | Maintainability-only | Documentation mismatch, not runtime work. |

## Preserve, do not optimize in this pass

Keep detection, YuNet and recognition cadence; model files/providers; input
sizes/crops/quality scoring; association/matching/voting thresholds; confirmation
and cache decisions; cross-camera policies; event order and persistence;
thread budgets; driver formats/backends; UI behavior. Keep latest-value capture,
shared recognition concurrency and preview mailboxes. No hardware profiles or
adaptive scheduling are appropriate until a reliable baseline exists. The
profiler must not write student identifiers, images, embeddings or credentials.

## Future candidates (only after target measurements)

| Rank | Candidate | Expected benefit if confirmed | Regression risk |
|---|---|---|---|
| 1 | Explicit ownership and asynchronous shutdown/restart lifecycle | Reliability and UI responsiveness | Medium: native read cancellation, pending events and QThread lifetime need tests on actual cameras |
| 2 | Skip unchanged preview conversion/scaling, accounting for resize | GUI CPU and render latency | Low–medium: verify resize, gate editor and snapshots |
| 3 | Bound cache admission with an explicitly preserved completion policy | Queue/RAM and recognition tail latency during bursts | High: dropping/delaying cache work can change witness/attendance outcomes |
| 4 | Measure and consistently configure existing native thread pools | CPU contention on weak hardware | Medium: hardware/backend dependent; retain Ryzen baseline |
| 5 | Reduce settings connection overhead / optimize measured report query | DB tail latency and GUI stalls | Medium: cache freshness, transaction ownership and schema compatibility |
| 6 | Review shared presence transition atomicity | Duplicate-event reliability | High: requires event semantics review, not identity tuning |

OSNet, matching, confirmation and cross-camera association are opaque behavior
for this task. A large latency does not authorize changing their decisions or
invocation policy. No FPS gains are claimed.

## Profiling and validation delivered

See [runtime-profiling.md](runtime-profiling.md) for the opt-in command, exact
metric definitions, local test/overhead measurements and remaining limitations.
The code changes in this pass are instrumentation only; the existing ONNX test
regex assertion is corrected. All runtime fixes and optimizations above remain
proposals pending dedicated validation.
