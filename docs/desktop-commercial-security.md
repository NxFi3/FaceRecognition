# Desktop commercial readiness review — 2026-10-25

Baseline inspected: `bb75a65` on `hardening/offline-first-licensing`. Inspection
preceded edits. This review does not change face thresholds, models, crops,
recognition confirmation, detector/Re-ID cadence, cross-camera association, or
attendance decisions. No camera performance or accuracy measurement is claimed.

## Existing controls and architecture

The production path is `LatestFrameCapture` (one newest-frame slot) →
`SmartCameraWorker` → person detection/tracker/optional OSNet → YuNet/face cache →
shared one-to-two-worker face executor → global identity/event state machine →
SQLite → coalesced Qt preview/report UI. The pipeline is kept unchanged.

The desktop already uses PBKDF2-HMAC-SHA256 local-admin hashes (400,000 iterations),
parameterized SQLite operations, WAL/foreign keys/busy timeout, signed Ed25519 v2
offline licenses, OS-vault-only credentials and checkpoints, trusted server-time
observations and rollback checks, background network jobs, bounded network timeouts,
client AES-GCM recovery bundles, tenant binding, authenticated/path-checked restore,
startup process locking and a durable restoration journal. Cloud backup is opt-in.
No cloud inference or public student-data upload API exists.

## Confirmed findings fixed

1. **Logout could be undone by normal shutdown.** `CloudClient.logout` deleted the
   vault checkpoint and cleared `_lease`, but retained `_envelope`.
   `close` called `checkpoint_time`, recreating an active signed checkpoint.
   Reproduction before the fix: immediately after logout `allowed=False`; after
   `close` and constructing a new client using the same state/vault, `allowed=True`.
   The fix clears the entire local entitlement before a logout network request,
   serializes it with persistence, records explicit sign-out, and prevents stale
   login/renew responses from committing after a session epoch changes. Explicit
   fresh login remains supported; ordinary shutdown still preserves valid offline
   admission. Sign-out also survives a vault-delete failure when the JSON sign-out
   record is available. If *all* storage writes/deletions fail, durable changes
   cannot be guaranteed; current-process access is still denied.
2. **GUI report/event CSV and XLSX exports permitted formula input.** Raw student
   codes/names, class/major names and notes were passed to CSV/openpyxl writers in
   `MainWindow.export_report` and `export_report_events`; the existing recovery CSV
   sanitizer did not protect these paths. A shared export-only sanitizer now
   escapes formula prefixes, including whitespace/control-prefixed variants. Python
   numeric values remain numeric and stored records/calculations are unchanged.
   The legacy `src/export_attendance.py` export uses the same boundary.
3. **Attendance notifications silently lost student display fields.**
   `_mark_attendance_event` imported `.database` relative to `src.engine`, which
   does not exist, swallowed the exception, and emitted blank name/code. It now
   uses the same package/direct-execution database import as the rest of the
   worker. The attendance write and its arguments/decision remain unchanged.
4. **Recovery-key size check followed an unbounded file read.** `import_key`
   previously allocated the whole file before enforcing 4 KiB. It now reads at
   most 4097 bytes before JSON parsing, preventing a mistakenly selected huge file
   from causing an unnecessary allocation.
5. **Protected checkpoint decompression accepted trailing data.** The bounded zlib
   decoder now rejects `unused_data` as well as incomplete/oversized input. This
   is corruption strictness at an OS-protected boundary, not a remote license
   forgery finding; Ed25519 verification remains mandatory.

## Evidence and tests

`tests/test_desktop_security.py` exercises logout→shutdown→restart, logout network
failure, vault deletion failure, explicit subsequent login, stale renewal/login
responses coordinated by thread Events, ordinary offline shutdown, bounded key
reads, corrupted compressed state, formula payloads, real GUI CSV/XLSX file output,
and the actual worker attendance signal payload. It uses synthetic records and
memory vaults. It does **not** claim Windows Credential Manager or physical camera
verification. `tests/test_inference_provider.py` verifies the new unused local
adapter preserves input/output identity and call counts, propagates errors and
refuses unimplemented cloud selection without invoking any model or transport.

The full desktop suite is run by the parent delivery process after integration.
No hardware benchmark matrix or long stress test is introduced.

## Remaining evidenced risks and boundaries

- Local SQLite face templates, plaintext CSV/rollback files and decrypted restore
  staging rely on the school account's filesystem permissions and disk encryption.
  No per-field encrypted-template migration is introduced into a working matching
  pipeline in this change. Use a dedicated restricted Windows account/NTFS folder
  and BitLocker or an equivalent approved disk-encryption policy.
- The approved OS vault must be available under the same account. The exported
  school recovery key is a sensitive unencrypted key file; keep it separately on
  protected encrypted removable storage. Losing every copy is not recoverable by
  the vendor, because the server has no client recovery key.
- Long offline entitlement cannot provide immediate remote revocation. A valid
  license survives server outage; revocation arrives on the next successful
  authenticated sync. Full machine/vault snapshots, writable executable/model
  replacement by a privileged local owner and absolute anti-crack guarantees are
  outside the ordinary desktop license threat boundary.
- Build-time asset hashes do not replace signed Windows releases, protected install
  directories, verified provenance/model licenses, or a real Windows smoke test.
  No automatic self-updater exists; release replacement is an explicit operator
  action. Optional OSNet assets must match the validated deployment.
- The shared face executor bounds concurrent recognition, but pending cache
  finalization submissions can accumulate under prolonged overload. This is a
  structural scheduling concern, not measured queue growth here; changing its
  cadence or dropping confirmation work is deliberately outside this task.
- GUI report queries use the SQLite eight-second busy timeout; disk contention can
  therefore stall a query. Native camera/model calls can outlast shutdown waits.
  These are inspection-backed risks requiring actual hardware/footage testing,
  not newly reproduced deadlocks. Forced native-thread destruction is unsafe and
  is not added.
- A live backup is not a cross-file atomic snapshot of a photo still being written;
  the stopped-application recovery CLI remains the complete quiet-state option.
- Local-admin authentication remains one shared local administrator, not a full
  per-teacher authorization/audit system. A pilot requiring multiple restricted
  local operator roles needs a separately specified migration.
