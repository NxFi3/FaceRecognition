# Future inference-provider boundary — not a shipping cloud product

`src/inference_provider.py` is a small local-only adapter and protocol. The
factory defaults to local execution and passes observations/results to an
existing callable without copies, preprocessing, retries, queues or decision
changes. It owns no models or threads. The current camera/face/Re-ID/event code
is **not rewired** to this adapter in this release. Selecting `cloud` explicitly
raises `NotImplementedError`; there is no cloud endpoint, network implementation,
cloud subscription, pricing promise, or automatic transfer of student images.

## Proposed future service contract (design only)

An eventual opt-in generic-inference service could use a versioned HTTPS API such
as `POST /future/v1/inference/jobs` with request UUID/idempotency key, school and
installation binding, camera-local sequence, capture time, capability/model
version, deadline and an explicitly consented observation. This route is not
implemented or exposed now. Responses should bind request/sequence/model version,
return only the approved stage's outputs and include server timing/error codes.
Biometric identity decisions, thresholds, crops and confirmation behavior require
separate review; this adapter does not implement them remotely or locally.

Device authentication must use separately scoped, revocable inference credentials
issued through authenticated account/device onboarding. A desktop subscription
license is public signed entitlement, not an upload secret. Do not broaden the
existing licensing/status bearer token into a general image-upload credential.
Use short-lived service access credentials refreshed using OS-protected secrets,
TLS certificate/hostname validation, least privilege, per-school/device limits,
request size limits and strict tenant ownership. The Ed25519 private signing key
and cloud credentials must never be packaged into the application.

## Before any implementation

- Obtain explicit school authorization for the exact data categories and purpose;
  assess guardian/student requirements and applicable Iranian law with qualified
  counsel. Cloud inference and cloud backup are separate consent choices. Keep
  local operation as the default and retain a clear withdrawal/deletion path.
- Agree data residency, processor/subprocessor terms, retention and deletion
  guarantees. Start with ephemeral inference payloads; do not retain images for
  training, troubleshooting or marketing analytics by default. Logs should hold
  request IDs/timing/size/status, not frames, embeddings, names or student codes.
- Measure target footage, encode cost, upload bandwidth, regional network latency,
  p50/p95 response time, concurrency and actual provider cost before defining
  plans. Two 720p streams are not a sustainable automatic raw-video upload policy.
  A privacy-approved crop/downscale/compression policy must be explicitly reviewed
  for quality and licensing scope; this change introduces none.
- Bound in-flight work and enforce capture deadlines. Drop stale inference jobs
  rather than queueing old video or writing an offline image backlog. Account for
  interrupted requests and idempotent billing/processing; never bill repeated
  retries as fresh work accidentally.
- Define and test local fallback before enabling the feature. A network outage
  must not invalidate a valid offline license or prevent local camera/report
  operation. Avoid processing the same observation twice or emitting duplicate
  attendance events during provider transitions. Do not silently switch identity
  decisions/models when connectivity changes.
- Validate replay protection, cross-tenant rejection, credential rotation/revocation,
  consent enforcement, encrypted transport, deletion, overload, timeout, regional
  outage recovery and operational cost on synthetic/authorized data. Commission
  a threat model and independent security review before accepting student data.
