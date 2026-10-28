# Actual production-mode browser and desktop-client verification

Captured 2026-10-25 with **synthetic accounts, synthetic prices, synthetic local
records and synthetic owner-attested payments only**. These are screenshots of
actual rendered pages, not design mockups or evidence of real customers/revenue.
No external banking settlement or external email delivery is asserted.

The isolated stack ran the real production Django settings (`TESTING=false`,
`DEBUG=false`), PostgreSQL 17 with a non-superuser/non-CREATEDB/non-CREATEROLE app
role, RedisCache, UID 10001/read-only Gunicorn container, and Caddy HTTPS on a
loopback-only port. A local CA was trusted normally; TLS verification was **not**
disabled. Real registration mail traversed a local SMTP server using STARTTLS.
The disposable containers, credentials and temporary CA trust entry were removed.
See [stack evidence](stack-evidence.json) and the [workflow report](audit-production-workflow.json).

The real Chromium 154 run passed **34 public/auth page checks** at 1440 and 390 px,
including Persian RTL, absence of horizontal overflow, responsive navigation and
interactive synthetic demo/empty-state behavior. It then executed:

1. School registration → actual local SMTP receipt → email-link verification → login.
2. Priced FaceRecognition checkout → unverified payment claim; desktop admission denied.
3. School-to-owner and cross-school invoice access denied.
4. Owner rejection → no activation; a fresh order then accepted a new claim.
5. Wrong payment amount → validation error and no activation.
6. Synthetic owner attestation → correct real pinned-Ed25519 license available to
   the actual desktop CloudClient over CA-verified HTTPS. A second installation
   was denied by the actual configured device allowance.
7. Client-encrypted backup upload/download → complete SQLite and event-file restore.
8. Actual TLS-proxy stop/start → offline client startup and failed synchronization
   preserved its valid license; CA-verified synchronization recovered afterwards.
9. Actual owner expiration → denied admission → online renewal → valid offline restart.
10. Customer device revocation → signed denial → denied subsequent offline restart.

The latest harness also checks an explicit HTTPS logout after revocation: the
current revoked credential can invalidate itself, the old bearer then receives
401 from signed-status synchronization, and a new offline client remains denied.
Count this additional case as passed only when its dedicated check appears in the
regenerated machine-readable workflow report.

The desktop vault used an explicitly labelled **memory test double**. This does
not verify Windows Credential Manager, real cameras, biometric accuracy or a
Windows build. Synthetic event-file bytes are not images of real students. The
payment screen represents a human manual-verification workflow, not a gateway
integration, bank API or real settlement.

## Screenshots

- [Company homepage — desktop](audit-production-company-desktop.png)
- [Company homepage — mobile](audit-production-company-mobile.png)
- [FaceRecognition landing — desktop](audit-production-product-desktop.png)
- [FaceRecognition landing — mobile](audit-production-product-mobile.png)
- [Registration — desktop](audit-production-registration-desktop.png)
- [Registration — mobile](audit-production-registration-mobile.png)
- [Unverified payment checkout](audit-production-checkout-unverified.png)
- [Owner rejects a wrong amount](audit-production-owner-payment-validation-error.png)
- [Owner console after synthetic verification](audit-production-owner-verified.png)
- [School portal with active signed entitlement](audit-production-school-active.png)
- [School portal — mobile](audit-production-school-mobile.png)

## Reproduce only with disposable data

From an execution environment with Docker, Chromium, `certutil`, Playwright and the
repository's desktop/service test dependencies:

```sh
python -m tools.run_production_audit --output docs/screenshots/audit-final
```

The orchestrator creates disposable private fixtures and starts the local stack.
It invokes `tools.verify_production_workflow` with a private fixture file; never
supply a real school's account, payment evidence, keys or database. The browser
harness accepts only explicitly attested disposable HTTPS loopback fixtures and
its optional outage hook only permits the dedicated audit TLS-proxy naming scope.
It does not seed production demo users or publish an application.
