# Independent commerce and signed-license API review

Reviewed the actual `3d67e03` service commerce/models/API implementations. This
review adds no products, payment providers, biometric changes or runtime schedules.
It is a targeted source review and regression exercise, not an ASVS certification
or a statement that all application vulnerabilities are eliminated.

## Confirmed findings

### High — stale authorization could mint an active offline license after revocation

Affected: `service/api.py`, `device_auth` + `lease`. CWE-367 (TOCTOU), CWE-863
(incorrect authorization). The bearer decorator populated School/Device model
instances before the body was read. `lease` signed those cached instances without
fresh authorization/locks. A concurrent revocation or school suspension committed
between authentication and signing still produced a new signed **active** long
license. Token rotation/password changes in that interval likewise were not
rechecked. The problem is more serious than ordinary in-flight response timing:
the request could wait for its body before the signing decision while retaining
old authorization state.

Four deterministic tests reproduced active/200 results on the baseline when a
device was revoked, school suspended, token rotated or password changed after
the initial authentication snapshot. All four failed before the fix.

Fix: rate-limit/parse before acquiring DB locks, then re-read School/User and Device
under an atomic School -> Device lock order. Compare the current bearer digest
and password stamp. A revoked installation may authenticate only the special
status synchronization path, and receives a signed denial. Rotated credentials
are rejected. Signature generation and credential timestamp update occur in the
same transaction. Signing completed before a later revocation remains a valid
linearizable operation; long offline validity still cannot offer instant remote
revocation, and that design tradeoff is unchanged.

### Medium — stale logout could clear a newer authenticated device session

Affected: `service/api.py`, `logout`. CWE-362 (race), CWE-613 (session lifecycle).
After authentication, a concurrent login could rotate the digest. The old logout
still updated the row by Device PK and cleared the newer token. The regression
reproduced status 200 plus deletion on the baseline. The fix reuses the same fresh,
locked current-digest check before mutation and audit. A stale logout is rejected
and cannot delete the newer session.

Login also checks that the password authentication stamp still matches the locked
fresh User before issuing/rotating a credential, covering the similar password
change interleaving. No API response schema, nonce format, v1 compatibility,
Ed25519 payload semantics or paid offline validity was changed.

## Related paths checked

Commerce uses server-side immutable product/price snapshots and owner checks;
receipt uniqueness, School -> Order transaction ordering, idempotency, product
binding and paid-period preservation remain covered by existing tests. No new
financial or cross-product bypass was confirmed in this review. This is not a
claim about future payment gateways: only independent manual bank attestation
exists and automated bank settlement is not integrated.

The fresh-auth helper `locked_authenticated_device` requires an existing atomic
transaction. The parent review also uses it immediately before upload quota,
retention and file metadata changes, rather than trusting an old upload snapshot.
Customer/owner/technical device updates are being reviewed under the same lock
order; hard-deleting activated authorization records would prevent signed denial
and should be restricted to the explicit revocation path.

## Verification actually obtained by this task

- Baseline targeted reproduction: five tests ran and all five **failed**, proving
  the four stale-signature paths and stale logout behavior above.
- After fix, API core plus commerce/product-commerce and new deterministic tests:
  **76 discovered, 70 passed, six existing PostgreSQL-only skipped**, 1.199 s.
- Final focused new suite: **eight discovered, seven passed, one PostgreSQL-only
  skipped**, 0.066 s on SQLite.
- New PostgreSQL transaction test coordinates a real request thread paused after
  authentication, commits revocation on another connection, then verifies that the
  resulting signed payload is revoked. Its actual PostgreSQL run is recorded by
  the parent full-suite validation; this task does not label the skipped SQLite
  run as a PostgreSQL success.
- Python compilation and owned-file whitespace check passed.

Remaining limits: the parent must verify the final integrated PostgreSQL/Redis/
HTTP stack and desktop contract. Actual Windows vault/camera operation, external
security assessment, TLS certificate delivery and real bank settlement are not
verified by these isolated synthetic regressions.


## Follow-up: expired network credential logout (CWE-613, medium)

A further lifecycle mismatch was reproduced: the logout endpoint rejected an
expired current credential, but the synchronization endpoint intentionally accepted
that same secret after a long offline period and could issue an active lease.
The caller therefore could not actually invalidate that server credential through
logout. A revoked current token similarly could not invalidate itself. Two new
regressions failed before the fix (expired logout followed by successful sync;
revoked logout returned 401).

Logout now permits the same current-digest/password-stamp proof used for status
synchronization solely for credential invalidation. It still refreshes under
School -> Device locks and rejects stale/rotated credentials. This does not grant
license or data access to expired/revoked credentials and does not alter valid
paid offline admission. The final focused run after this change is recorded below.

Follow-up focused validation: **34 discovered, 33 passed, one PostgreSQL-only
case skipped**, 0.480 seconds. Both newly added regressions and all existing API
core/stale-token tests passed. No actual PostgreSQL result is inferred from this
SQLite run.
