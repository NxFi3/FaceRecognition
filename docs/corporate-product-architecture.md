# NxFi: company first, FaceRecognition as the first product

This update continues `feature/nxfi-commercial-platform` after `b7c9e70`.
The company brand, account entry and catalog are independent from the first
product's website. FaceRecognition remains the only commercially supported SKU.
No future product, cloud processing service or placeholder subscription is launched.

## Public and account boundaries

| Route | Responsibility |
| --- | --- |
| `/` | NxFi: software/AI company introduction and its actual first product |
| `/products/` | Company product catalog; only published, implemented products with a dedicated landing qualify |
| `/products/facerecognition/` | Complete FaceRecognition landing, preserving the previous full product content |
| `/products/facerecognition/pricing/` | FaceRecognition's real published plans and secure order creation |
| `/products/facerecognition/downloads/` | Actual published FaceRecognition releases |
| `/products/facerecognition/account/` | Complete Face customer portal: orders, checkout, invoices, devices, backups and support |
| `/account/` | Company customer account entrance linking to the first product workspace |
| `/owner/` | Company-wide owner console, product filter, orders, plans, subscriptions and gross verified receipts |
| `/contact/`, `/support/`, `/privacy/`, `/terms/` | Company-wide information with accurate product-specific conditions |

Features, workflow, synthetic demo, comparison, requirements and FAQ retain their
full implementations under the Face product prefix. The company and product
share the same self-hosted RTL design system; Face has an additional product nav.
Legacy public URLs permanently redirect to canonical product URLs with query
strings preserved. Previously open POST checkout/ticket forms still dispatch to
the same protected view, preserving request data and CSRF. Old private mutation
and download endpoints remain compatible. No desktop API URL or signature changes.

## Product-scoped business records

`Product` has a stable slug separate from its presentation name. Plan belongs to
one product; its slug is unique within that product. Moving an existing plan to
another SKU is rejected. Orders keep immutable product identity/name in addition
to immutable financial terms. `ProductSubscription` is unique per customer/product;
release and support-ticket records also carry a product foreign key. Face portal
queries enforce both tenant and product scope, including exports and downloads.

The existing `School` row remains the current school customer's tenant/account
boundary and the authoritative Face license state. No second account or new login
is created for existing customers. `sync_face_subscription` mirrors Face dates,
status and limits to its commercial ledger on verified fulfillment and existing
owner editing paths. This explicit compatibility layer avoids replacing the
working license/device/backup authority or touching biometric runtime logic.

The owner console can filter financial/plan/subscription records by product.
Publishing an unknown Product row or active plan cannot sell it: the production
fulfillment registry contains **only FaceRecognition**. The public catalog also
requires a real landing-route registration. A future product requires its own
implemented/tested fulfillment and authorization policy, genuine release and
privacy/consent review, then explicit metadata publication. It can reuse customer
accounts, order/payment evidence, idempotency, invoices, owner controls and design
without redesigning them. Different contract units may add product-specific
fields/policies later; this is not a generic product-builder feature.

Face-specific bans cannot alter another product's state; a disabled customer
account still blocks every product. Tests use a temporary fake handler to prove
this isolation. That handler and SKU are not registered or seeded in production.

## Existing-data migration

Migration `0005` creates FaceRecognition before adding foreign keys; all existing
plans, orders, release metadata and tickets are assigned to it. Existing school
states are copied into Face commercial subscriptions without changing original
status, paid dates, limits, device UUIDs/tokens, order amounts or verified payments.
No keys are regenerated and no unlaunched product is seeded. Back up first as for
any production migration. Historical-schema regression tests actually migrate
synthetic data from `0004` to `0005` and compare these values.

## Actual verification on 2026-10-25

- Full server suite with disposable PostgreSQL 17: **139 passed**, 10.145 seconds.
  Includes purchase, idempotency, product/tenant boundaries, migration and all nine
  PostgreSQL-only concurrency cases.
- Non-root/read-only Docker image: built successfully; **139 discovered, 130 passed,
  nine PostgreSQL-only tests skipped**, 7.361 seconds.
- Desktop suite: **129 passed**, 0.792 seconds. No desktop source was modified by
  this architecture correction.
- Real desktop/server offline-license contract smoke: **PASS**, including renewal,
  signed revocation and encrypted recovery.
- Actual Chromium 154: **34 canonical route/viewport checks passed** (17 paths at
  1440/390 px), plus interactive demo, company navigation and product-specific
  customer portal/checkout checks. **Nine new actual screenshots** use synthetic
  data and explicitly synthetic prices. See [current visual evidence](screenshots/corporate-README.md).
- Migration drift, Django deployment checks, Python compilation, JavaScript syntax
  and whitespace checks: passed. The two previously documented HSTS/preload policy
  recommendations remain intentionally silenced.

These are functional checks, not camera accuracy/FPS or final Windows-build
measurements. Real Windows signing/packaging, actual school footage/cameras,
public TLS/SMTP, live Iranian payment gateway and independent security assessment
remain the earlier release blockers. The corporate correction adds no external
service dependencies, cloud image transport or new available products.
