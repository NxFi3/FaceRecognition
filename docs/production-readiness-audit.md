# Independent source review and production-readiness verification

Date: 2026-10-25. Base: `feature/nxfi-commercial-platform@3d67e03`.
This pass adds no product features and does not redesign the recognition pipeline.
Separate review agents examined web authorization, commerce/license API, desktop
security and production configuration; their fixes were integrated and exercised
against an isolated real stack. This is not an accredited external penetration
test, ASVS certification, proof of zero vulnerabilities or a public deployment.

## Confirmed findings and implemented fixes

| ID | Severity / reference | Root cause and affected source | Smallest implemented fix / evidence |
| --- | --- | --- | --- |
| API-01 | High — CWE-367, CWE-863 | `service/api.py`: decorator authorization snapshots could remain active after device revoke, school suspension, token/password rotation while waiting before signing | Re-read current School/User/Device and bearer digest under School→Device locks immediately before signature; five baseline reproductions failed before fix, fresh-denial regressions and a real PG interleaving now pass |
| API-02 | Medium — CWE-362, CWE-613 | `service/api.py`: an older authenticated logout could clear a newer rotated token | The same fresh current-token lock check guards logout; stale requests cannot invalidate the newer credential |
| API-03 | Medium — CWE-613 | `service/api.py`: expired current tokens could not log out although synchronization intentionally accepted them | Current expired/revoked credential proof may invalidate itself under the same fresh locks; no expanded data/license access, two baseline failures now regressions |
| DESKTOP-01 | Medium — CWE-362 | `src/cloud/client.py`: parallel login/renew replies could apply an older active response after a newer signed denial | Serialize background auth exchanges, recheck session epoch after waiting; two deterministic races plus existing outage/admission/shutdown tests pass |
| BACKUP-01 | Medium — CWE-367, CWE-863 | `service/backups.py`: authorization could change during streaming; the commit rechecked School but not Device/token | Fresh locked authorization before quota/retention mutations. Baseline returned201 for both revoked and rotated credentials; now401, no new blob, and previous recovery backup remains intact |
| WEB-01 | Medium — CWE-307 | `service/security.py`: raw whitespace/Unicode login aliases hashed to distinct account rate-limit buckets despite normalized authentication | Normalize before hashing/truncation; actual baseline accepted16 wrong attempts against one account from distinct synthetic IPs; regression now enforces the shared account budget |
| DEPLOY-01 | Medium — CWE-295, CWE-319 | `service/settings.py`: PostgreSQL URL TLS verification options were silently discarded | Forward validated allowlisted TLS/connect parameters; reject malformed/duplicate/unknown options; subprocess tests confirm preservation, not a claimed remote-PG TLS handshake |
| DEPLOY-02 | Low security / medium reliability — CWE-15, CWE-20 | `service/settings.py`: test DB URL could override production DB; malformed origins/cache/DB endpoints were accepted | Fail closed outside explicit test mode, validate origins/ports/endpoints and raise credential-free configuration errors |
| WEB-02 | Low — CWE-362 | `service/admin.py`: saving a stale Device admin form overwrote newly rotated credentials/stamps | Lock and update only requested revocation state; latest token/stamp/expiry are preserved |
| WEB-03 | Low — CWE-841 | `service/admin.py`: hard deletion removed the bearer digest needed for signed revocation; School deletion could cascade on the baseline | Explicitly prohibit device/school hard deletion including bulk admin actions; use retirement/revocation. Tests demonstrate the baseline cascade and protected records |
| WEB-04 | Low — CWE-252/CWE-841 integrity | `service/platform.py`: some owner/profile mutations committed even when audit insertion failed | Make mutation and audit record atomic for plan/profile/release/contact operations; injected DB failure now rolls back changes |
| UX/availability | Low, confirmed functional bugs | Partial owner health probe failures caused500; missing trial UUID caused500; owner-denial response lacked normal RTL document | Guard probes/404, fail closed503 on rate-limit backend outage with security headers, reuse branded Persian403 template. Status/authorization remains enforced |

Similar paths were reviewed: API login and logout, password changes, customer and
technical-admin revocation, upload finalization/retention, auth/reset/resend/admin
rate counters, plan/release/profile/contact audit pairs, individual/bulk deletion,
and competing desktop auth exchanges. Financial idempotency and immutable
server-priced orders were already present; no new price or tenant bypass was
confirmed in those paths. No claim is made about all possible variants.

Detailed reviewer notes:

- [API/commerce review](independent-api-commerce-review.md)
- [Web review](independent-web-security.md)
- [Desktop review](security-audit-desktop-2026-10-25.md)
- [Deployment review](deployment-security-audit.md)

These checks map to relevant ASVS authentication/session, server-side authorization,
validation/encoding, cryptographic/data protection, business-logic and secure
configuration areas. They are selected control checks, not a claim of full ASVS
compliance. Dependency advisories and static scans do not replace penetration
testing or native Windows/DLL review.

## Actual full business workflow: production settings, not a Django test client

The browser/client harness ran against Docker Gunicorn with `SERVICE_TESTING`
unset (`TESTING=false`, `DEBUG=false`), PostgreSQL17 using the actual restricted
application role (not superuser, CREATEDB or CREATEROLE), RedisCache, a UID10001
read-only web container and Caddy HTTPS on a loopback-only port. TLS used a
temporary local CA trusted normally by Requests and Chromium; verification was
never disabled. The CA entry, fixtures and disposable containers were cleaned up.
Registration emails traversed a real local SMTP listener using STARTTLS. This
checks the real SMTP backend and TLS protocol, not internet mail deliverability.

The executed flow used only explicitly synthetic accounts/data/prices:

1. Browser registration → SMTP capture → actual verification link → login.
2. Real published FaceRecognition plan → server-priced checkout → receipt claim;
   actual desktop API admission still denied.
3. Other school denied owner console and the first school's invoice.
4. Owner reauthentication → reject invalid claim; no entitlement/payment created.
5. New order → wrong amount rejected without activation → independent-owner
   attestation of synthetic payment → actual desktop `CloudClient` obtained and
   verified a pinned Ed25519 license for the correct school/capacity/device.
6. Actual configured device limit rejected another installation.
7. Real HTTPS client-encrypted backup upload/download → full SQLite/event-file
   restore with contents checked. No real biometric records or student photos.
8. Stop/start the actual isolated TLS proxy → valid offline startup and failed
   sync retain paid admission → verified HTTPS synchronization recovers.
9. Actual owner expiry update → signed denial; actual online renewal → renewed
   offline startup; local data remains. Offline signed-expiration timing and
   repeated annual cycles are additionally covered by desktop functional tests.
10. Customer revocation → next authenticated sync returns signed denial → later
    offline restart stays denied.
11. The revoked current credential logged out over actual HTTPS; the old bearer
    then received401 and offline admission stayed denied.
12. Business notifications were dispatched through the real SMTP backend into the
    local STARTTLS sink after commit (four accepted, zero deferred).

No real money was transferred. The payment flow is owner-attested manual
verification, not a banking API or integrated Iranian gateway. The desktop vault
in this cross-process harness is an explicit memory test double, not Windows
Credential Manager. Camera/accuracy/Windows build results are not inferred.

**Verified UI evidence:** [11 actual screenshots and machine-readable reports](screenshots/audit-final/README.md).
Chromium154 executed34 canonical public/auth page checks at1440/390px, plus
registration/checkout/owner/school flows, mobile navigation, RTL/overflow checks,
interactive synthetic demo, validation/denial and empty states. The current
company/product design was preserved; no visual redesign was introduced.

## Repeatable commands and actual results

Use disposable PostgreSQL credentials only; the Django runner creates/drops a test
DB. Never point these commands at a production database.

```sh
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -q
SERVICE_TESTING=1 SERVICE_TEST_DATABASE_URL="$DISPOSABLE_PG_URL" python -m service.manage test service --noinput -v 1
SERVICE_TESTING=1 python -m service.manage makemigrations --check --dry-run
SERVICE_TESTING=1 python -m service.manage check --deploy --fail-level WARNING
python -m pip_audit -r requirements-service.txt -r requirements.txt --progress-spinner off
python -m bandit -r service src tools deploy -f json -o /tmp/nxfi-bandit.json
python -m pip check
python -m compileall -q service src tools deploy tests
node --check service/static/site.js
bash -n deploy/install-ubuntu.sh deploy/backup-server.sh
git diff --check
```

The local production-mode workflow can be repeated with Docker, Chromium,
`certutil`, Playwright and the repository test dependencies installed:

```sh
docker build -f deploy/Postgres.Dockerfile -t face-subscription-postgres:17 .
docker build -f deploy/Dockerfile -t nxfi-audit:test .
python -m tools.run_production_audit --output docs/screenshots/audit-final
```

Managed proxy environments must supply their trusted CA to the Docker build using
the documented optional BuildKit secret; never disable TLS checks. The runner is
strictly loopback/disposable and requires no real production account or key. It
installs one uniquely named temporary NSS trust entry and removes it in `finally`.
If forcibly terminated, inspect only its `nxfi-audit-*` resources/private temp
folder/trust entry before cleanup; never broadly delete containers or trust stores.

Final recorded results (actual runs):

| Check | Result |
| --- | --- |
| Full service suite on disposable PostgreSQL17 | **172 passed**, 9.069 s; includes ten real PostgreSQL-only concurrency cases |
| Full desktop suite | **131 passed**, 1.240 s |
| Final Docker build | Successful; image `sha256:4b7d2a45cea6425a43c9a6fbc8535bc9cc694bcb40e432d5462b712bda8e4e7c` |
| Non-root/read-only Docker service suite | **172 discovered, 162 passed, ten PostgreSQL-only skipped**, 10.730 s |
| Actual production-mode HTTPS workflow | **12 checks passed**, 34 public/auth responsive page checks, 11 actual screenshots; no TLS bypass |
| Real SMTP backend/outbox dispatch | Four synthetic notifications accepted by the local STARTTLS sink; zero deferred; external delivery not asserted |
| Resolved service + desktop Python dependency audit | **No known vulnerabilities found** at this run |
| Bandit on `service src deploy` | Zero high/medium, 39 LOW warnings; 18,922 lines examined |
| Migration drift / deployment check | No drift; no issues with two documented HSTS/preload policy recommendations silenced |
| Compilation, JS/shell syntax, pip consistency, whitespace | Passed |

The full-tree scan also includes verification tools and test fixtures. Its one
remaining MEDIUM `B108` warning identifies the Docker harness's bounded/sticky
container `/tmp` mount string, not a predictable host plaintext tempfile; this
was manually classified rather than represented as an application exploit or
blindly suppressed. LOW warnings include fixture passwords, smoke-test asserts,
controlled Docker/local-profiler subprocesses and inherited best-effort exception
paths. No zero-warning or zero-vulnerability guarantee is made. Native OS/DLL
packages and optional deployment-specific Re-ID dependencies need release checks.

No migration was introduced in this security pass. The only desktop runtime edit
is the authentication-exchange ordering fix; inference, models, thresholds and
attendance decisions are unchanged. Test runtimes are functional execution times,
not throughput/accuracy measurements of target school hardware.

## Remaining deployment blockers and limits

- Actual owned domain/VPS, ACME issuance/renewal, external SMTP delivery, alerts,
  off-VPS backup destination and operator rehearsal of cold server restoration
  still need real infrastructure. Nothing was purchased or publicly deployed.
- Real Windows build/signing, installed Qt/DLL/dlib/ONNX/OSNet parity, actual OS
  credential vault, two physical cameras, sleep/reboot, weak-PC latency and real
  authorized footage are not available in this Linux environment. No FPS or
  recognition-quality claim is added.
- Local SQLite/templates/CSV/rollback data still depend on school Windows-account
  ACLs and approved disk encryption. School recovery key files must be stored
  separately and securely; losing every copy is not vendor-recoverable.
- Long offline validity cannot provide immediate remote revocation. A privileged
  local executable/vault snapshot or compromised signing host is outside normal
  offline DRM protection. Server-time and ordinary clock rollback checks remain.
- Owner password step-up is not MFA. Consider independently managed MFA/access
  controls and a penetration test before sensitive real onboarding.
- The Iranian gateway, automated refunds/proration, formal fiscal invoice service
  and cloud inference are **not integrated/available**. Verified manual payment is
  functional. HTML invoices are working commercial records, not simulated tax
  certification. There are no placeholder installers: downloads require an actual
  operator-installed, hash-verified published artifact.
- Native camera calls may hang; GUI disk/report contention and pending cache
  finalization queues remain inspection-backed risks. No reproducible new camera
  failure was found or speculative scheduling rewrite introduced.
- Generic business-data export still buffers records; rate limiting bounds requests,
  but very large tenants need measured streaming work later. Release hashing is
  proportional to binary size; immutable/read-only operator artifacts matter.
- Retention/deletion includes operator-reviewed exceptions and separate cold-copy
  policies. Mail can be accepted twice after a crash despite bounded outbox
  retries; financial fulfillment is separately idempotent. Legal seller identity,
  student-processing consent, residency and actual contract/refund/tax conditions
  require qualified review before real customers.

No implemented commercial feature was silently removed. Corporate website,
FaceRecognition pages, checkout, school/owner portals and backup management remain
functional. The synthetic public demonstration intentionally is a demonstration;
no future product, customer, certification or real payment is fabricated.
