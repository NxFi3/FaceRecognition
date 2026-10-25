# Explicit owner authorization and administrative step-up

The commercial owner is a separate authority from Django `is_staff`,
`is_superuser`, groups and permissions. Neither existing staff nor an existing
superuser automatically becomes the company owner. Customer registration cannot
set or edit this authority. Existing school accounts are preserved.

## Provisioning and recovery

After applying migrations, on the private service host:

```sh
python -m service.manage provision_owner --email owner@your-company-domain.tld
```

The command prompts for a new strong password twice. Passwords are never command
arguments, logged output, environment defaults or repository secrets. It creates
an active account with `is_staff=True` for the gated technical admin UI and
`is_superuser=False`; company authority comes solely from the singleton
`CompanyOwner(id=1)` record. Existing accounts require their existing password;
inactive accounts and accounts associated with any school cannot be designated.

A second provisioning attempt is rejected. Explicit replacement:

```sh
python -m service.manage provision_owner --email next-owner@your-company-domain.tld --replace
```

Replacement also requires the current owner's password. Atomic record locking,
a singleton DB constraint and a revision counter protect against concurrent or
accidental replacement. The old account's existing company sessions lose
permission immediately on their next authorization check, regardless of staff or
superuser flags. Provisioning and replacement are audited. Do not expose this
command as a public endpoint or permit untrusted users to run deployment commands.

If the owner loses the password, use the normal email reset flow on a trusted
mailbox, or Django's private-host `changepassword` command under your documented
infrastructure access/recovery policy. Host/DB administrators can ultimately
modify application authority; this control does not protect against a compromised
server operator.

## Enforcement

- `is_company_owner(user)` checks the explicit record, account activity and
  separation from school accounts on every authorization check.
- `@owner_required` protects owner read views. Use
  `@owner_required(recent=True)` for sensitive owner mutations.
- `/owner/reauth/` verifies the current password with CSRF, a five-attempt / five-
  minute shared Redis-backed limit, and session-key rotation.
- Successful step-up lasts 300 seconds by default and is bound to user ID, owner
  revision and the current password-auth hash. Expired, future-dated, wrong-user,
  wrong-revision and password-changed proofs fail. Code clamps configured duration
  to 60–900 seconds. Redis protection failure denies step-up with HTTP 503.
- The technical Django administration uses `OwnerAdminSite`, requiring explicit
  owner, staff status and recent step-up. Ordinary staff/superusers cannot log in.
  A successful owner password login to this admin counts as step-up.
- `OwnerModelAdmin` grants business model permissions directly to the explicit
  owner without granting global auth.User/auth.Group powers. The owner designation
  is not registered as an editable admin model. Keep payment/order models read-
  only in technical admin; all fulfillment must use the verified transactional
  commerce service.
- Cookies remain secure/HttpOnly/SameSite; session auth uses Django's password
  hash binding. CSP, CSRF, private Gunicorn, trusted Caddy proxy and shared rate
  limiting are still required in deployment.

## Verified baseline weaknesses and remediation

The previous `service/admin.py` registered ordinary ModelAdmin classes on the
default admin site. Staff with granted permissions and all superusers inherited
administrative access without a separate company-owner designation. There was no
recent-authentication requirement for subscription or device mutations. These
paths now use explicit authority and step-up; existing staff flags confer no
company ownership.

The previous `PUBLIC_URL` settings validation rejected usernames and paths but
accepted password-only URL userinfo, query strings and fragments. Such
misconfiguration could create malformed security links. Validation now rejects
all of those values while preserving a valid HTTPS origin.

## Regression coverage and limits

`SERVICE_TESTING=1 python -m service.manage test service.tests_owner -v 1`
checks staff/superuser denial, explicit owner access, step-up expiration and
forgery, CSRF, safe redirects, rate limiting and unavailable protection, session
rotation, singleton DB constraint, school/owner separation, interactive
provisioning/replacement and public-origin validation. The final integration
report records actual runs; a command listing is not a test-pass claim.

This is password reauthentication, not MFA. Hardware-backed or TOTP/WebAuthn MFA,
external penetration testing, identity verification of the human operator and
server host security remain deployment tasks. A stolen current owner session can
act within a still-valid step-up window. No security certification or absence of
all vulnerabilities is claimed.

Recorded focused run (2026-10-25):
`SERVICE_TESTING=1 python -m service.manage test service.tests_owner -v 1` ran
**14 tests, all passed** (0.410 seconds in the recorded run). This used disposable
SQLite server test data, not production accounts or Windows. The full PostgreSQL,
portal, commerce and deployment test results belong to the integration report.

Device-allowance follow-up: the technical `SchoolAdminForm` now rejects reducing
an allowance below non-revoked bound installations, including installations whose
network credentials have expired. In the admin POST transaction it locks the same
school row as API admission before counting. It never silently revokes devices;
explicit revocation/logout is required first. New schools' preassigned UUIDs are
handled safely. Three regression cases cover reduction, explicit release and new
school creation. Recorded focused suite after this follow-up: **17 tests passed**.
