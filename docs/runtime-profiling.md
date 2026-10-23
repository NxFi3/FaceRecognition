# Recording the existing production runtime

From the repository root, using the application's normal Python environment:

```sh
python -m pip install psutil
python -m tools.profile_runtime --seconds 120 --output baseline.jsonl
```

Use the existing two-camera settings and authenticate normally. This starts the
real application: attendance/events are persisted normally. It does not attach
to an already running instance; close the other instance first to avoid opening
the same cameras twice. It does not modify camera settings, model selection,
thread counts, scheduling, UI, or biometric decisions.

Recording ends approximately 120 seconds after the **first completed camera
frame**, rounded to the next reporting interval (default 1 second). The app
stays open; close it normally. This deliberately avoids introducing another
camera shutdown mechanism. Closing early writes a partial summary. Without a
completed frame, recording ends after `--startup-timeout 300`. Startup rows are
retained, so model cold starts, login delays and camera initialization remain
visible. Histograms include any measured cold-start invocation; they are not
steady-state-only measurements. Run a second normal session if comparing cold
starts; do not infer warm-run results from a different machine.

The output file must not already exist. `--interval` changes only telemetry
report frequency. Optional application arguments (such as `--open-reports`) pass
through to the normal app. File-write errors disable telemetry and print an
error without stopping inference. A hard process kill cannot guarantee a final
summary; preceding flushed JSONL rows remain usable.

`psutil` supplies process CPU, RSS and native thread count. If it is absent,
those fields are null with an explicit reason; inference still runs. CPU is
process-wide: **100% means one logical CPU**, and multi-core use can exceed 100%.
RSS/thread counts cannot be attributed accurately to individual cameras sharing
native libraries and a recognition executor, so they are reported once per
process. No hardware performance is inferred from core count.

## Reading the JSONL

The first row contains schema/version/environment metadata. Remaining rows have
`type: startup | sample | summary`, `elapsed_s`, `active_elapsed_s`, `interval_s`,
`process` and `cameras`. Camera keys `1` and `2` are UI slots, with physical device
IDs in `details`. `application` includes GUI/DB work outside a camera context;
`aggregate` combines all observed scopes.

- **FPS:** `capture_fps` counts successfully published frames; `processed_fps`
  counts finished worker iterations including preview production. Both are
  interval rates. For a long-run rate, sum frame-count deltas and divide by
  elapsed interval time. Do not average camera latency percentiles or sum FPS
  samples. Aggregate timings merge observation histograms, not camera averages.
- **Counters:** cumulative since profiling began. `captured_frames`,
  `consumed_frames`, `processed_frames`, `skipped_capture_frames`, read failures,
  wait timeouts, worker starts/crashes and preview counts describe the pipeline.
  Skipped frames are exact gaps in the capture publication sequence at each
  consume, including frames overwritten during worker startup. They **do not**
  count frames lost in the camera/USB/driver, nor byte-identical driver frames.
  The unconsumed tail at shutdown is not included in sequence-gap counts.
- **Timings:** each has invocation `count`, `mean_ms`, `p50_ms`, `p95_ms`, `max_ms`.
  Mean/max/count are exact over recorded observations. Percentiles use fixed
  256-bin logarithmic histograms; upper bounds are within 10% for normal values
  (first bin <=1 microsecond; the overflow bin is reported as the maximum).
  Timings are cumulative and inclusive. **Do not add nested or async stages.**
- **Gauges:** `current` and, where meaningful, `peak`. Pending means submitted
  but not started; active means executing, not just an unfinished Future.
  Recognition and cache work have separate gauges and shared `downstream`
  totals. Aggregate job peaks are observed at simultaneous transitions, not
  sums of different cameras' peaks. Other aggregate gauges expose current sums.
  `unconsumed_*_futures` also includes completed jobs awaiting a camera frame.
- **Summary:** final cumulative observations and the last (possibly short)
  interval's rates, plus stop reason. Active/pending jobs at cutoff may be
  nonzero; recording does not cancel them. There is no synthetic zero/FPS claim
  for a model stage that never ran: its timing key is absent.

| Timing/counter | Endpoint / interpretation |
|---|---|
| `capture_read` | Entire backend read, including normal blocking for next camera frame; not exposure latency |
| `frame_age_at_consume` | Publication after read/demosaic to consumer acquisition, timestamp sampled under the frame lock |
| `frame_processing` | Acquisition to completed iteration, including result application, events/DB and preview, excluding asynchronous recognition still running |
| `frame_age_at_completion` | Publication to iteration completion; not exposure-to-display or time-to-identity |
| `person_detector` | Existing detection API including preprocessing, inference and decode |
| `tracker_update_including_reid` | Entire tracker update, including OSNet when invoked |
| `track_predict` | Each track prediction, including between detector frames |
| `osnet_batch` / `osnet_single` | Existing extractor API duration and invocation count, including conversion/normalization |
| `yunet_including_resize` | Existing resize + YuNet interval, before face-coordinate mapping |
| `face_recognition` | Executor task compute time for tracked and face-only jobs |
| `face_encode_and_match` | Opaque face encoding/matching API, including calls from cache fallback; nested inside task/cache timings |
| `*.queue_delay` | Submission to start in the unchanged shared executor; separate recognition/cache series |
| `cache_finalization` | Entire existing finalizer task; excludes queue delay and later worker application |
| `event_face_identity`, `event_reid_identity`, `event_crossing` | Existing public event-engine call including synchronous callbacks/DB |
| `db_event`, `db_attendance`, `db_setting`, `db_recent_events` | Full database function including open/PRAGMAs/query/commit where applicable/close |
| `qimage` | Existing wrapping and ownership-detaching copy |
| `preview_including_qimage` | Worker overlay through direct mailbox signal completion |
| `gui_render` | QPixmap creation, scaling and setPixmap call for each camera; actual compositor presentation is not measured |
| `preview_mailbox_age_at_render` | Mailbox timestamp to render call completion; repeated display of an unchanged image increases this age |
| `gui_repeated_renders` | Repeated QImage cache key; does not eliminate any renders |
| `time_to_identity.first`, `.face`, `.reid` | Track creation to first successful public identity-event application, separately for first identity, first face application (including cache), first Re-ID application; includes event callback time |
| `tracks_retired_without_*_identity`, `live_tracks_without_face_identity` | Unresolved/censored tracks; time-to-identity is conditioned on eventual successful application, not accuracy or recognition rate |
| `global_observe_including_lock_wait` | Entire existing observation API including lock wait and candidate decisions |
| `cross_camera_candidate_scans`, `cross_camera_candidates`, `cross_camera_links` | Completed eligible candidate scans, summed candidates after existing similarity/role gates and before margin selection, and accepted return objects; not unique students or identity accuracy |

Time-to-identity starts at local track creation (not first sensor exposure).
No identity/name/embedding/image is retained by the profiler. The face-only
preview fallback has no person track and is covered by recognition timing/job
counts, not track-based time-to-identity. Weak references prevent diagnostic
tracking state from keeping dead production tracks alive. Histogram storage is
fixed per stage/camera; no per-frame history, extra frame copies, new work queue
or instrumentation on-disk writes occur in camera/model threads. A short metrics
lock is shared; reporter copies histograms before formatting/writing. Profiling
has nonzero overhead and should be compared once enabled/disabled on the target.
Normal `python src/app.py` leaves collection disabled.

## Local validation and limits

On the development environment (Python 3.12), the focused suite passed **38 tests plus four parameter subtests** (0.37 seconds in the recorded run):

```sh
python -m pytest -q tests/test_runtime_profile.py tests/test_engine_primitives.py tests/test_global_identity.py tests/test_gate_config.py tests/test_onnx_person_detector.py
```

The 13 new unittest cases also pass with the repository's standard runner:

```sh
python -m unittest discover -s tests -p test_runtime_profile.py -v
```

New tests cover disabled passthrough, exact argument/return/exception behavior,
weighted aggregate histograms and bounded storage, nested camera context,
two-camera shared-executor accounting/cancellation/failure, latest-frame
reference ownership and sequence gaps, weak-reference track cleanup and identity
timing, JSONL finalization/startup timeout/write failure, invalid CLI durations,
preview repeats/FPS deltas, opaque OSNet/handoff output preservation and SQLite
attendance uniqueness/event persistence. They use tiny fake frames/tasks and a
temporary database; there is no camera/model hardware benchmark.

The existing ONNX error-message test had unescaped regex brackets; only its
assertion was corrected. **No production correctness fixes** were made. The
full suite was attempted but collection stops on missing `dlib`; both import
smoke tests also fail for that missing dependency. Thus the Qt/worker/enrollment
and real-model tests are not claimed to pass here. Syntax compilation and
`git diff --check` passed. Run the full suite in the deployment environment:

```sh
python -m pytest -q
```

One 100,000-call, single-thread `timeit` check measured a trivial function at
23 ns/call directly, 243 ns/call through a disabled timing wrapper, and 1,951
ns/call with histogram collection enabled. This measures only wrapper overhead
on this development machine; it does not estimate camera FPS, model latency,
contention or target CPU performance. Storage remained 256 bins for that stage.
No deployment FPS, p50/p95, CPU or RSS figures were fabricated.

Validate on the Ryzen baseline first with normal two-camera traffic, including
an empty scene, appearances, occlusion and cross-camera handoff. Keep settings
and models fixed. Then collect the same telemetry on weak hardware. Model
costs, native thread contention, queue bursts, real USB reconnect/shutdown,
preview responsiveness and recognition/attendance parity require real footage
and devices. No weak-hardware presets are introduced in this audit pass.
