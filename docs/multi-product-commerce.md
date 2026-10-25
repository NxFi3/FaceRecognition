# Company catalog and product-scoped commerce

NxFi Technologies is the company. `Product` represents a catalog identity;
FaceRecognition (`facerecognition`) is the only product with an implemented,
registered commercial fulfillment handler in this release. Publishing an arbitrary
Product row cannot make it purchasable or activate FaceRecognition by mistake.
No future product is seeded as launched or advertised as available.

## Minimal extension points

- Product slug is its primary key; it is a stable SKU, separate from display name.
  Product names, summaries and publication metadata can evolve without changing
  existing orders. No catalog lookup occurs at module import.
- Each Plan belongs to one product. Plan slugs are unique within that product.
  Moving an existing plan to another product is rejected; create a new plan instead.
- Orders snapshot `product_id` and `product_name` together with their existing
  immutable prices/terms. Changing a catalog name or plan price does not rewrite
  an invoice. A per-school checkout UUID cannot be replayed against another product.
- ProductSubscription is unique per school/product and records the commercial
  status, dates, capacity, devices and latest source order. Its records contain no
  student or biometric data. Another product's subscription is never extended by
  a FaceRecognition payment.
- `PRODUCT_FULFILLMENT` is the explicit application registry. A product handler
  provides current entitlement, capacity checks and the application of its own
  entitlement. Only FaceRecognition is registered. A future launch requires an
  implemented/tested handler, product-specific licensing and device policy, privacy
  review, actual release artifacts and then explicit publication. Metadata or
  active plan flags alone are insufficient.

The current capacity fields describe the first education product. A later product
with different contractual units can add its own validated policy/fields without
replacing company accounts, payment evidence, immutable order snapshots, tenant
boundaries, invoices or idempotent financial fulfillment.

## FaceRecognition compatibility

The existing School fields remain authoritative for the installed FaceRecognition
license API and current owner controls. The registered Face handler applies the
same start/expiration/capacity/device behavior, then mirrors those values to the
product-scoped commercial ledger in the same payment transaction. The explicit
`sync_face_subscription` helper also supports owner changes; it writes only the
FaceRecognition row and preserves source-order history during manual edits.

The data migration creates FaceRecognition before adding non-null product foreign
keys, assigns all existing plans/orders/releases/tickets to that first product,
backfills order product names, and mirrors existing school entitlement states.
It must not activate suspended schools, change paid dates/capacities, rotate keys,
reprice orders or create verified payments. ProductSubscription does not introduce
an online dependency or replace Ed25519/School licensing authority.

Local capture, detection, face matching, Re-ID, attendance rules and offline license
signatures are not changed by this catalog boundary. Future handlers cannot reuse
the FaceRecognition handler for a different SKU: the registry and immutable product
binding are checked before checkout and fulfillment.

## Verification scope

`service.tests_product_commerce` covers immutable product snapshots and plan
ownership, scoped idempotency, unsupported/published metadata rejection, product
binding corruption, Face ledger synchronization and preservation of another
product's records. Existing commerce and signed-license tests remain regression
coverage for actual first-product behavior. Real future product fulfillment is
not claimed or exercised by production code.

Recorded local checks during implementation: the existing commerce suite plus the
first eleven product regressions discovered 41 tests, with 35 passing and six
PostgreSQL-only cases skipped on SQLite (0.657 s). The completed twelve product
regressions then all passed (0.164 s), including a test-only handler proving that
an additional product can fulfill its own ledger without altering Face state.
The parent integration run records the final full PostgreSQL results; no future
product or external payment settlement is claimed as tested by this fixture.
