# Independent web source review — 2026-10-25

Scope: authentication, owner/session authorization, tenant/product portal queries,
file downloads, audit writes and administrative device operations on baseline
`3d67e03`. This is a focused source/regression review, not external certification
or a claim that every vulnerability is eliminated. Commerce/API licensing and
backups are also independently reviewed by separate integration tasks.

## Confirmed findings and minimal fixes

| ID / severity | Source and root cause | Fix / regression evidence |
| --- | --- | --- |
| WEB-01 Moderate — CWE-307, inconsistent canonicalization | `service/security.py`: authentication normalized EmailField/UsernameField values while the per-account limiter hashed raw whitespace/Unicode aliases. Middleware also truncated before normalization. Distributed IPs could target one real account with separate account budgets. | NFKC/strip/casefold before hashing/truncating. Before fix, sixteen wrong logins from sixteen synthetic IPs all returned 200 rather than the sixteenth returning 429. Regression now passes, and reset/resend/admin Unicode aliases share the same account guard. Password verification itself was never bypassed. |
| WEB-02 Low — CWE-362, lost device credentials | `service/admin.py`: a Device form instance read before concurrent login/renewal could write its stale token digest, password stamp and expiry back through a full `save()`. | Only the editable `revoked` column is saved under School→Device locks. The regression actually failed before the fix by overwriting a newer credential and now preserves its digest/stamp/expiry. |
| WEB-03 Low — CWE-841, broken revocation lifecycle | `service/admin.py`: hard-deleting an installation removed the digest needed to authenticate and receive signed revocation. An existing otherwise valid offline license would then only get an unsigned credential failure on sync. | Device deletion is disabled in technical admin; explicit revocation retains the digest. Authenticated owner deletion POST is tested as 403, and supported customer revocation retains authentication evidence. This does not promise immediate offline revocation. |
| WEB-04 Low — CWE-778 / transaction integrity | `service/platform.py`: price/profile/release/contact mutations and their audit insert were not one transaction. Injected audit DB failure left a changed plan price despite an error response and no audit. | Mutation and audit are atomic. The original test observed 2000 rather than the old 1000 price after a synthetic DB failure. Plan/profile/release withdrawal/contact regressions now preserve previous values. Release publication uses the same atomic pattern, with file hashing outside transaction locks. |
| WEB-05 Low — CWE-755, failure reporting | Authentication protection backend exceptions produced unhandled 500s; early 429 responses skipped the middleware's security headers. Owner health counted outbox rows outside its probe guard; unknown trial-order IDs also raised uncaught lookup errors. | Authentication stays fail-closed with a generic 503 and security headers. Throttled responses receive the same policy headers. Owner probes handle partial data-query failure without leaking exceptions, unknown counters show unavailable, and invalid trial orders return 404. |

Customer device revocation and DeviceAdmin mutations now follow the same
School→Device locking order as the signed-license issuance correction. Revocation
and its audit entry are committed together. No biometric or desktop scheduling
logic is changed.

## Tests actually executed

```sh
SERVICE_TESTING=1 python -m service.manage test service.tests_web_hardening service.tests_owner_customers service.tests_portal --noinput -v 1
```

Result: **40 tests passed, 0.865 seconds**. Eleven tests are in the new independent
web-hardening file. Tests use synthetic accounts/IPs and Django's isolated SQLite
runner; the parent delivery performs the real PostgreSQL concurrency and live
browser/desktop compatibility integration checks. Whitespace and compilation
checks for changed files also passed.

The first broader local run exposed an unrelated settings-message assertion in
`tests_owner` while another settings audit was being edited. The origin validation
itself rejected the invalid inputs; that compatibility mismatch was reported to
the parent rather than misreported as a passing full run.

## Controls checked and remaining limits

The reviewed portal object lookups consistently bind school ownership and product
where required. Financial browser input remains a claim, not authorization to
activate; payment services independently check the explicit owner. Owner actions
use recent password/auth-hash/revision-bound step-up; ordinary staff/superusers are
not company owners. Browser mutation paths use CSRF middleware and POST guards.
Django template escaping and restrictive CSP cover user-controlled display values;
no arbitrary URL fetch or browser file-upload path was introduced. Release paths
are restricted and validated on a single stream. These observations are not proof
against all attacks.

Relevant ASVS areas: authentication anti-automation and normalized identifiers;
server-side authorization/session checks; business-logic and audit transaction
integrity; safe error handling; data and file access boundaries.

Existing limitations remain: password step-up is not MFA; signed long-offline
licenses cannot be revoked instantly without connectivity; approved local-vault
and OS/filesystem administrators are trusted; local plaintext SQLite/export/restore
files need restricted ACLs/disk encryption. Privileged in-place release mutation
is an operator/infrastructure risk; per-download hash checking does not replace
signed Windows installers. Full history exports are buffered in memory (rate
limited) and release verification is proportional to artifact size. These were
not redesigned without target-load evidence. Real SMTP/TLS delivery, Windows
packaging/vaults, physical cameras and independent penetration testing require
separate real environment verification.


## Cascade follow-up: school retirement (same WEB-03 lifecycle)

The baseline hard-delete policy also allowed deleting an otherwise manually active
school with issued devices but no protective order/backup/subscription rows. An
isolated synthetic reproduction restored only the baseline School/Device delete
permissions and executed the actual authorized Django admin delete POST: school
and device/digest rows were both removed (one proof test passed, 0.399 seconds).

The earlier DeviceAdmin deletion guard already makes Django's nested collector
refuse that cascade in the current code; this is **not** a newly claimed bypass of
that guard. SchoolAdmin now independently forbids hard deletion as an explicit
lifecycle policy, including the `delete_selected` bulk action. Retire/revoke the
account/license through supported controls; privacy erasure remains a separate
reviewed process rather than an accidental cascade from the technical admin.

Two additional regressions cover fresh-owner single and bulk deletion attempts.
The school/device fixture deliberately has no incidental PROTECT records, so the
permission guard itself is verified. No production data or migration was changed.
