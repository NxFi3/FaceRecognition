# Verified UI evidence for the unified-account increment

All accounts, orders, payment attestations and local records are synthetic.
These are actual browser/Qt screenshots, not design mockups. No real payment,
customer, Windows build, biometric accuracy or external mail delivery is claimed.

- [Company — English desktop](company-en-1440.png)
- [Company — English mobile](company-en-390.png)
- [Company — Persian desktop](company-fa-1440.png)
- [Company — Persian mobile](company-fa-390.png)
- [Web login — English](login-en.png) / [Persian](login-fa.png)
- [Company account before creating a school](workspace-en-no-product.png)
- [Unified-account browser workflow](unified-workflow.json)
- [Real Qt login — English](production/audit-production-desktop-login-en.png)
- [Real Qt login — Persian](production/audit-production-desktop-login-fa.png)
- [Production owner payment verification](production/audit-production-owner-verified.png)
- [Production-config purchase-to-license/Qt workflow](production/audit-production-workflow.json)
- [Actual PostgreSQL/Redis/non-root production settings](production/stack-evidence.json)
- [Actual source MainWindow, English direction with legacy Persian content](desktop-main-en-development.png)
- [Actual source MainWindow, Persian locale](desktop-main-fa-development.png)

The seven root browser captures use actual Chromium and the isolated local Django
SQLite developer server with console email verification/recovery tokens. The
13 production captures use the separate Docker PostgreSQL/Redis/Gunicorn stack,
Caddy HTTPS with normally trusted ephemeral CA, and local STARTTLS mail sink.
The Qt login performed real HTTPS activation asynchronously against that stack;
its OS credential vault is an explicit memory test double, not a native Windows
vault test. Those disposable resources/trust entry are cleaned up by the runner.

The two MainWindow captures use the real source MainWindow with a separate SQLite
sandbox and an explicit DEVELOPMENT ONLY status. Camera startup is disabled in
the harness. English translation of this inherited operational UI is unfinished;
its screenshot documents that limitation rather than claiming bilingual parity.
See the [audit and remaining release blockers](../../unified-account-i18n-audit.md).
