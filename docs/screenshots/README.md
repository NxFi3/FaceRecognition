> Current company-first UI evidence is in [corporate-README.md](corporate-README.md).
> The unprefixed images below document the earlier single-product layout.

# Verified commercial UI evidence

Captured locally on 2026-10-25 with Chromium 154.0.8037.57 using the actual Django
application and self-hosted CSS/JavaScript/Vazirmatn assets. The local profile was
`service.dev_settings`, bound only to `127.0.0.1:8000`, with an isolated temporary
SQLite database. No production data or server signing keys were used.

Every school name, contact, ticket, price, order and person shown is **synthetic**.
The displayed plan prices are test fixtures, not proposed or confirmed selling
prices. The checkout claim was intentionally unverified, so the owner dashboard
correctly shows zero verified revenue. No gateway transaction, Windows build,
camera processing or production deployment is implied by these screenshots.

| Screenshot | Verified view |
| --- | --- |
| [home-desktop.png](home-desktop.png) | Persian RTL homepage at 1440 px |
| [home-mobile.png](home-mobile.png) | Responsive homepage at 390 px |
| [pricing-desktop.png](pricing-desktop.png) | Configured synthetic plans and checkout actions |
| [demo-desktop.png](demo-desktop.png) | Interactive demonstration with fictitious attendance data |
| [school-dashboard.png](school-dashboard.png) | Authenticated synthetic school portal |
| [school-dashboard-mobile.png](school-dashboard-mobile.png) | The same portal at 390 px |
| [checkout-pending.png](checkout-pending.png) | Manual claim waiting for independent payment verification |
| [owner-dashboard.png](owner-dashboard.png) | Explicit company-owner business console |
| [owner-dashboard-mobile.png](owner-dashboard-mobile.png) | The same console at 390 px |
| [owner-health.png](owner-health.png) | Database/cache health and pending email count |

The machine-readable [ui-verification.json](ui-verification.json) records the
actual browser version, 30 successful public/auth page checks at 1440/390 px,
screenshot names and interaction checks. Checks included horizontal overflow,
RTL/landmarks, the mobile menu and Escape key, synthetic day/filter/empty-state
changes, authenticated school/owner views at both viewport sizes, no page-level
JavaScript or console errors, and critical public/login/contact pages with
JavaScript disabled. The images were also visually inspected.

Re-run with Playwright and a locally installed Chromium against a separately
prepared **synthetic** fixture server:

```sh
python -m tools.verify_commercial_ui \
  --fixture /private/path/synthetic-fixture.json \
  --output docs/screenshots \
  --chromium /usr/bin/chromium
```

The fixture file contains `school_email`, `school_password`, `owner_email`,
`owner_password`, and `order_id`; keep it private and outside Git. The tool rejects
non-loopback origins. Playwright/Chromium are optional QA tools, not desktop or
production web runtime dependencies.

Browser verification complements the automated Django authorization/payment
regressions; it is not a full accessibility certification, external penetration
test, hardware benchmark or proof of Windows packaging.
