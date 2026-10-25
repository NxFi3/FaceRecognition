# Independent desktop audit — 2026-10-25

Baseline reviewed: `3d67e03`, existing commercial feature branch. This pass adds no
features or inference changes. The audited boundary includes the signed offline
client, OS credential/checkpoint storage, background Qt jobs, encrypted recovery,
legacy backup restoration, exported key files and the existing shutdown guards.

## Confirmed finding: older authentication response could undo revocation

Severity: **medium**, CWE-362 (concurrent execution without sufficient ordering),
relevant to ASVS authorization, session lifecycle and state-transition controls.
Affected code: `src/cloud/client.py`, `login()` and `renew()`.

The main controller's background sync and the account dialog's login/sync use the
same client but different Qt job managers. The existing session epoch rejected
replies after logout/shutdown, yet two exchanges within the same epoch could still
overlap. A delayed, valid older active license could commit after a newer signed
revocation. The old response's nonce and signature were valid for its own request,
so cryptographic verification did not prevent the state rollback.

Reproduced before the fix with the actual `CloudClient.renew()` and coordinated
synthetic responses:

```
after_new_revocation_allowed: False
after_old_response_allowed: True
```

The same overlap could save a newly rotated login token after a sync already sent
the obsolete token, causing avoidable unauthorized responses/re-authentication.

Fix: serialize login/sync exchanges with a dedicated authentication network lock,
then recheck the existing session epoch after acquiring it. The lock is separate
from the in-memory camera gate and state-file locks. Only background callers wait;
valid paid offline admission is unaffected by network errors. Login credentials,
nonce generation, local signature checks, signed expiry and revocation policy are
unchanged. Logout still invalidates local admission before its network request.

`tests/test_desktop_audit.py` contains two regressions: coordinated concurrent
revocation cannot be overwritten, including after restart; concurrent login/sync
uses the newly saved rotated token. Similar authentication paths share the same
lock, rather than fixing only the automatic renewal caller.

## Actually run validation

- New independent regressions: **2 passed**, 0.235 s.
- `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -q`:
  **131 passed**. This includes existing activation/offline/renewal/revocation,
  backup/corruption/school-isolation/interrupt, Qt responsiveness, camera/tracking,
  export and shutdown regressions.
- Python compilation and owned-file whitespace checks passed.

No throughput, target-machine accuracy or final Windows-build claim is made.
The parent verification runs the real server/client contract separately.

## Remaining inspected boundaries

- Actual Windows Credential Manager, Qt/DLL packaging, sleep/reboot behavior,
  antivirus/code signing and real-camera native shutdown still need Windows and
  authorized deployment footage. Linux memory-vault unit tests do not prove them.
- Local SQLite templates and decrypted restore/CSV/rollback data still rely on
  restricted OS accounts/NTFS ACLs and approved disk encryption. The school key
  export is intentionally a separately protected plaintext key file; losing every
  copy is not recoverable by the server.
- A long valid offline license cannot receive immediate remote revocation. Full
  machine/vault rollback or executable replacement by a privileged local owner is
  not made impossible by ordinary offline licensing.
- Existing GUI report DB waits, pending cache-finalization work and native camera
  calls remain structural runtime risks. No inference cadence, identity decisions,
  threshold, model or event-write logic was changed or tuned.
- Recovery authenticates school-bound GCM before extraction and rejects unsafe ZIP
  paths. A live snapshot still cannot atomically capture a photo not yet written;
  use the stopped-application recovery path for a quiet complete snapshot.
- Credential/checkpoint storage must be available under the same OS account. Network
  serialization does not replace filesystem access controls or protect a fully
  compromised OS user.

This is a focused source and regression audit, not a claim that every vulnerability
has been eliminated or that external penetration testing has completed.
