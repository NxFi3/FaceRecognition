# Unified NxFi identity, localization and installation audit

Base: `feature/nxfi-commercial-platform@7de6dff` (verified successor of the
requested commercial branch). New branch: `feature/nxfi-unified-account-i18n`.
No merge, public deployment, production data change or signing-key rotation.

**This is an implemented, tested increment, not completion of the entire request.**
English is the configured default and the core company/acquisition/onboarding
surfaces have reviewed translations. Legacy product/owner/report/camera screens
still contain Persian literals. Full English/Persian parity is a release blocker;
the application must not yet be advertised as fully bilingual.

## Evidence before changing architecture

- `service/settings.py` hardcoded `LANGUAGE_CODE='fa'`, omitted LocaleMiddleware;
  `service/templates/base.html` fixed `lang=fa,dir=rtl`; `src/app.py:main` fixed
  application direction and loaded only Persian Qt translations.
- Existing company and first-product routes, server-priced orders, explicit owner,
  atomic verified-payment fulfillment, tenant filters, Ed25519 API and offline
  cache were already implemented. They were extended, not replaced.
- `service/views.py:SchoolLoginView.form_valid` rejected a verified company user
  without School. Registration and verification required School, and
  `service/platform.py:workspace` returned404 for the same account. This was
  architectural coupling, not an accusation of a payment or tenant exploit.
- `buildwindows.bat`, `tools/prepare_windows_assets.py` and the Windows workflow
  already copied public service configuration into distributions. Customers were
  **not** supposed to configure servers. The operator's manual build required a
  root-level JSON copy; the new staging command removes that step and explicitly
  validates the two-field public schema. No actual production domain/key exists
  in this checkout and none was fabricated.
- `src/cloud/ui.py:run_application` required the local administrator dialog before
  NxFi sign-in and `LoginDialog` had only fixed Persian labels. Local-admin
  authorization is preserved, now after NxFi admission. The existing startup
  account/recovery dialog is also retained; the journey is not falsely described
  as having exactly one dialog.
- Recognition/model/camera/tracker/attendance decisions are unchanged. The source
  preview deliberately overrides camera startup only in its separate development
  subclass, never in the production MainWindow method.

## Actual implementation

### Company identity without changing entitlement authority

`service/customer_models.py`, `customer.py`, `views.py`, `platform.py` and
`migrations/0006_customerprofile.py`:

- The existing Django User remains the single NxFi login. CustomerProfile stores
  company display name, preferred language and email-verification observation.
- Registration can omit school_name; signed email verification and login then
  lead to the company workspace with no product or paid entitlement.
- Explicit CSRF-protected POST creates an inactive FaceRecognition School only
  for a verified active account. A User row lock makes repeated first-workspace
  requests idempotent; submitted foreign user/school identifiers are ignored.
- The company owner cannot create a School for itself. Owner customer overview
  includes accounts which have not created a product workspace.
- Company profile updates never rename a School or modify roles. Existing
  School-based financial/licensing/backup records and product filters remain the
  authority. This is not a speculative multi-company membership/RBAC rewrite.
- Migration adds one table and backfills existing users from their School without
  changing passwords, account-active flags, School UUIDs, signed licenses,
  subscriptions, invoices, biometric records or order history. Legacy school
  customers start with their existing Persian preference; new customers use en.
  Reverse migration drops only the new profile table; existing School data stays.
  Take an operator DB backup before applying any deployment migration.

### Reviewed localization and company UX

`service/settings.py`, `service/templates`, `src/locales/fa.json`,
`tools/compile_translations.py`, `src/localization.py`:

- Standard Django LocaleMiddleware/gettext, en default, fa alternative, cookie
  persistence and a CSRF-protected selector preserving current path/query and
  rejecting external redirects. Authenticated selections update profile preference.
- A shared reviewed JSON source compiles deterministically to standard GNU MO;
  no runtime translation service, new dependency or browser auto-translation.
- Company homepage and About present NxFi separately from FaceRecognition.
  Existing responsive visual system remains, with LTR/RTL-aware chrome.
- Company/catalog/first-product landing, preview, pricing/download copy,
  registration/login/recovery forms, company workspace/profile and desktop login
  use reviewed translations. Django supplies its built-in form validation locale.
- Operator-authored names, release notes, payment instructions and plan descriptions
  are preserved exactly. The historical seeded product summary has an explicit
  reviewed translation; arbitrary database text is not silently translated.
- `tools.audit_localization` inventories remaining static review candidates in
  `docs/localization-review-candidates.json`. The 55 source files counted in the
  initial inventory include intentional language names/logs as well as real UI
  work; this is not a percentage or proof of complete runtime coverage.

### Installation and desktop admission

`tools/package_client.py`, `ClientConfig.packaged`, `buildwindows.bat`,
`.github/workflows/build-windows.yml`, `tools/prepare_windows_assets.py`:

- Operator stages generated public origin/verification key into build/public,
  then PyInstaller includes it and the locale catalog as resources. Customer
  downloads the **entire** official distribution; no JSON creation/copy step.
- Frozen admission reads its bundled resource, not current working directory or
  an environment-supplied URL/key. Configuration rejects malformed ports,
  credentials and unknown fields (including accidentally supplied private keys).
- LoginDialog has persistent English/Persian selection without clearing typed
  credentials or triggering network work just to change language; sign-in remains
  a background job. Network failures remain outside GUI/camera threads.
- Existing signed activation, offline validity, renewal, expiration, revocation,
  school binding, device allowance and client-encrypted backups remain intact.
- Local admin protection and existing recovery workflow are intentionally retained.
  No production bypass, fake offline entitlement or embedded signing secret added.

## Reproduce

Developer actual MainWindow (camera-disabled source preview, no commercial server):

```sh
python -m pip install -r requirements.txt
python -m tools.desktop_dev --data-dir /tmp/nxfi-ui-sandbox --language en
# Headless screenshot, only when no native desktop is available:
QT_QPA_PLATFORM=offscreen python -m tools.desktop_dev --data-dir /tmp/nxfi-ui-sandbox --language fa --screenshot /tmp/nxfi-ui.png
```

The source-only tool refuses frozen builds, requires a Git checkout, refuses the
repository/data directory and refuses nonempty unmarked directories. It sets a
separate SQLite/evidence root, starts the actual MainWindow without camera startup,
and displays DEVELOPMENT ONLY. It is never imported by the executable. It is
**not** a licensed production launch or proof of camera/model accuracy. Much of
the legacy MainWindow remains Persian even when language is en.

Operator release (existing signing key and deployment-generated public settings):

```sh
python -m tools.package_client
# Windows, with required original models and validated OSNet cache installed:
buildwindows.bat
```

Do not run deploy.configure over existing production secrets. Follow
[existing deployment instructions](commercial-deployment-fa.md) with the existing
.env/signing key. Build from the same checkout; deploy the service image, apply
migration0006 using the existing migrate step, and configure published real plans,
manual-payment instructions, mail and signed artifacts. Do not publish a package
with a synthetic origin/key. No native Windows build was executed here.

Validation commands:

```sh
python -m tools.compile_translations
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -q
SERVICE_TESTING=1 SERVICE_TEST_DATABASE_URL="$DISPOSABLE_PG_URL" python -m service.manage test service --noinput -v0
python -m tools.verify_unified_ui
# After building the service Docker image, see the existing production runner:
python -m tools.run_production_audit --output docs/screenshots/unified-account/production
```

Browser verification uses synthetic fixtures only. The local unified-account
runner uses real Chromium and Django's explicitly isolated SQLite development
settings and console email tokens. The separate production runner uses real
production settings, PostgreSQL/Redis, restricted app role, Caddy TLS with normal
CA verification, real local STARTTLS and actual CloudClient plus LoginDialog.
OS credential storage in that harness remains an explicit memory double.

## Remaining blockers

- Complete translations of legacy camera/enrollment/reports, AccountDialog,
  recovery/roster UI, owner administration, legal/support/feature pages,
  commercial validation/notifications and operator-authored database copy. Do not
  market bilingual completeness or approve this branch for production yet.
- Review company-only identity suspension/deletion/export and support policies
  before wider multi-product use. There is one school per account, no multi-user
  organization membership, additional product runtime or cloud inference.
- Native Windows packaging/code signing, real credential vault and camera/model
  regression on actual target hardware are unverified. Source GUI screenshots
  and Linux/offscreen event-loop tests are not substitutes.
- A real owned domain, public HTTPS renewal, actual SMTP delivery, published
  signed Windows installer, operator recovery rehearsal, legal seller terms,
  deployment consent/data-residency review remain required.
- Payments remain independently verified manual payments; no Iranian banking
  gateway, refund automation or certified fiscal invoice integration is claimed.
- Existing limitations from the [security audit](production-readiness-audit.md)
  still apply: owner password step-up is not MFA, local data needs account ACLs
  and disk encryption, offline revocation waits for synchronization, and no
  automated test proves absence of vulnerabilities.

## Executed results for this increment

- Full service suite on disposable PostgreSQL17: **181 passed**, 11.488s;
  PostgreSQL-only concurrency tests executed, not skipped.
- Full desktop suite after the styled bilingual login change: **136 passed**,
  1.161s. These are functional test durations, not performance measurements.
- Non-root/read-only service Docker suite: **181 discovered, 171 passed,
  10 PostgreSQL-only skipped**, 12.741s.
- Actual production-config HTTPS workflow: **13 checks, 34 responsive page
  checks, 13 screenshots**. Includes actual Qt background login and bilingual
  login captures, purchase/rejection, verified manual activation, device limit,
  encrypted recovery, outage, renewal, expiry and durable revocation/logout.
- Actual unified-account Chromium workflow: **4 scenario groups, 7 screenshots**;
  en/fa navigation at desktop/mobile, registration without School, verification,
  login, explicit workspace creation and real password-recovery token.
- Actual MainWindow opened offscreen in the isolated source harness in both
  locale modes, with two screenshots. Its inherited Persian UI is explicitly
  incomplete for English. Cameras were disabled, not simulated.
- Reviewed catalog: **317 messages**. Final static review inventory has **54
  source files** with candidates, including intentional constants and real UI work.
- pip-audit on service+desktop requirements: **no known vulnerabilities found**.
  Bandit on service/src/deploy: **0 HIGH, 0 MEDIUM, 41 LOW** (not a clean security
  certification). Compilation, JS syntax, migration drift, deployment checks and
  Git whitespace checks passed. No migration drift; deployment check retains the
  existing documented HSTS/preload policy exceptions.

Old Persian-only presentation tests and browser harnesses now request Persian
explicitly. New tests separately verify the English default; no payment or
license test assertions were removed. Nine service regressions cover company
identity/locales/migration/isolation/owner boundaries; five desktop regressions
cover GUI persistence/no-network language switching, public-only staging/frozen
lookup, sandbox rejection and deterministic catalog compilation.

Evidence: [screenshots and machine-readable workflow reports](screenshots/unified-account/README.md).
