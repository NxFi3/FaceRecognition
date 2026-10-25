# Commerce authority and fulfillment

This implementation extends the existing Django service. Business records contain
school account/billing data only; no student rows, images, embeddings or attendance
are uploaded into commerce tables.

## Implemented boundary

- `service/commerce_models.py`: configurable plans, immutable order quote snapshots,
  and positive independently verified payments. Prices are integer **IRR (ریال)**.
  Order invoices use the full UUID and are commercial documents; they are not
  represented as official tax invoices or proof of company registration.
- `service/commerce.py`: transactionally creates orders, records customer claims,
  cancels unpaid orders, approves trials and verifies manual settlement. A browser
  claim/redirect/query amount cannot activate a subscription.
- `seed_plans`: adds inactive, unpublished drafts only. The example 900-student,
  one-device, 365-day plan has no selling price and cannot be purchased until the
  explicit owner enters an actual positive price and publishes/activates it.
  Re-running the command never replaces existing prices or settings.
- Plan editing never reprices existing orders. Each quote is valid for seven days.
  UUID idempotency keys are unique per school; reuse for another plan is rejected.
  Multiple checkout tabs reuse one pending quote for the same school and plan.
- References submitted by a customer are untrusted claims. Only the explicit
  `CompanyOwner` may verify an exact positive amount against the saved quote.
  Django staff/superuser flags do not grant this authority. Sensitive HTTP routes
  additionally require the owner's recent password step-up.
- The owner must independently inspect actual bank settlement before attesting
  verification. This is **manual verification**, not a tested Iranian bank API.
  The software cannot technically prove that a human really inspected a bank
  account. Production demonstration data must not be entered as paid receipts.
- A unique `(provider, reference)` and one payment per order prevent replay.
  References are normalized and bounded; the owner should include a bank prefix
  so references from different banks are unambiguous. Exact verification replay
  is idempotent; a changed amount or reference is rejected.
- The transaction locks School, then Order, inserts the payment, updates School
  entitlement, marks fulfillment, queues email and appends audit records together.
  Any DB/outbox failure rolls the whole operation back. SMTP is never sent inside
  this transaction. PostgreSQL row locks are the production concurrency authority.
- Renewals append the full plan period after existing paid expiry; they are not
  prorated. Expired subscriptions restart from verification time. Future contract
  starts stay in the future. Banned/disabled accounts cannot be reactivated by
  payment. Active-period capacity downgrades require support review, and excess
  bound devices must be explicitly revoked before a smaller-device plan proceeds.
- Free trials are zero-price, at most 30 days, once per school and explicitly
  owner-approved. They create no `VerifiedPayment` and therefore no paid revenue.
- Existing Ed25519 API issuance uses the resulting School dates/limits, preserving
  the existing offline-first expiry/revocation contract. No desktop recognition
  scheduling, thresholds, face crops or identity decisions are changed.

## Payment and cancellation limits

Unpaid orders can be cancelled. The owner can explicitly reject an invalid manual
payment claim with a bounded customer-visible reason. Rejection queues a notice
and audit event, leaves entitlement untouched and allows a fresh order. Repeating
the same rejection is idempotent; changing the first reason is forbidden by both
the service and model save guard. Fulfilled/verified orders cannot be rejected,
and trial orders are not misrepresented as rejected payments. Cancelling an already fulfilled order, prorating,
refund execution, chargebacks, scheduled future downgrades, tax-service integration
and live bank gateways are not automated. They require support and accounting
reconciliation. Payment totals are verified gross receipts, not net profit or
net revenue after external refunds. Commercial records are retained for financial
integrity; a privacy deletion request must include the agreed/legal retention
policy rather than deleting paid invoices automatically.

`PaymentProvider` and `VerifiedEvidence` define a future server-side verification
interface, but no live gateway adapter is enabled. A real Iranian provider must
be integrated with merchant credentials, authenticated server-to-server settlement
verification, replay controls, and real sandbox/production testing before being
advertised as available. A redirect alone must never be considered evidence.

Do not register Order/VerifiedPayment as writable Django administration models.
All state transitions belong in the tested commerce service. Direct DB access and
server code execution remain trusted administrative capabilities, not customer
interfaces or tamper-proof financial ledgers.

## Verification

Run only with disposable test databases:

```sh
SERVICE_TESTING=1 python -m service.manage test service.tests_commerce -v 1
SERVICE_TESTING=1 SERVICE_TEST_DATABASE_URL="$TEST_POSTGRES_URL" python -m service.manage test service.tests_commerce -v 1
```

The recorded isolated SQLite run discovered 30 cases: **24 passed, 6 PostgreSQL-only
cases skipped** (0.497 s). The actual disposable PostgreSQL 17 run then executed
**all 30 successfully** (2.685 s), including parallel checkout, double verification
(one extension), reuse of one actual settlement across two schools, customer claim
versus verification, cancellation versus verification and rejection versus verification. Every mutation locks
School before Order to avoid an inverted FK/row-lock dependency. The original
inverted deadlock was a structural review finding, not separately reproduced; the
new PostgreSQL interleavings were actually run successfully.

Other regressions cover tenant isolation, staff/superuser denial, false customer
claims, price changes, quote expiry/cancellation, incorrect amounts, reference
replay, trial approval/duplicate trials, multiple annual renewals, future dates,
capacity/device reductions, outbox rollback and actual signed-license entitlement.
