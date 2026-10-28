# Verified company-first and FaceRecognition product UI

These screenshots are the current **company-first** hierarchy after the corporate
architecture clarification. Older unprefixed screenshots in this directory are
historical evidence from the previous single-product website implementation.

All screenshots were actually captured with Playwright and Chromium 154 against
an isolated loopback development server. The database, school, plan prices and
pending payment claim are synthetic; no production student data, credentials or
bank settlement was used. The labelled plan price is **not a commercial quote**.

- [NxFi Technologies company homepage — desktop](corporate-home-desktop.png)
- [NxFi Technologies company homepage — mobile](corporate-home-mobile.png)
- [Products catalogue — desktop](corporate-products-desktop.png)
- [Dedicated FaceRecognition landing page — desktop](corporate-facerecognition-desktop.png)
- [Dedicated FaceRecognition landing page — mobile](corporate-facerecognition-mobile.png)
- [FaceRecognition pricing — synthetic price](corporate-facerecognition-pricing.png)
- [FaceRecognition customer portal — desktop](corporate-facerecognition-portal.png)
- [FaceRecognition customer portal — mobile](corporate-facerecognition-portal-mobile.png)
- [FaceRecognition pending-payment checkout](corporate-facerecognition-checkout.png)

Actual browser checks:

- 34 canonical public/auth route checks: 17 paths at 1440 and 390 pixels wide.
- Persian RTL document, main landmark, no page-level horizontal overflow.
- Corporate homepage and Products separated from the full FaceRecognition landing.
- Existing synthetic product demo still changes day counters.
- Canonical product-scoped school portal and pending checkout work after login.
- Mobile company navigation opens and closes with Escape.
- Critical company/catalogue/product/pricing/login pages render without JavaScript.
- No page-level JavaScript exceptions or browser-console errors in the run.

Machine report: [corporate-ui-verification.json](corporate-ui-verification.json).
Optional harness: `python -m tools.verify_corporate_ui --fixture /PRIVATE/disposable-fixture.json`.
It only accepts a loopback HTTP development origin and requires operator-supplied
synthetic fixture credentials; it never seeds or signs into production accounts.

Backend companion verification actually ran:

`SERVICE_TESTING=1 python -m service.manage test service.tests_product_portal --noinput -v 1`

Result: **13 tests passed** (1.067 s), including a real migration of synthetic
legacy 0004 school/order/device/release/support records into the product model
without changing existing FaceRecognition grant dates, capacities, status or price.

These checks do not claim a live payment gateway, Windows build, production
hosting, accessibility certification, real-camera quality or hardware throughput.
