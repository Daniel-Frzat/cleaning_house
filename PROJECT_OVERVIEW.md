# Cleaning House — Project Overview

A technical reference for the backend as a whole: what it is, how it is built, the
decisions that shape it, and what is deliberately missing.

**Audience:** engineers joining the project, and anyone deciding what to build next.
For client integration use the live API reference at `/api/docs` or the
checked-in [openapi.json](openapi.json) (regenerate it with
`python manage.py export_openapi`).

---

## Contents

1. [What this is](#1-what-this-is)
2. [Stack and layout](#2-stack-and-layout)
3. [The nine domains](#3-the-nine-domains)
4. [The business flow, end to end](#4-the-business-flow-end-to-end)
5. [Architectural rules](#5-architectural-rules)
6. [Identity and roles](#6-identity-and-roles)
7. [Pricing](#7-pricing)
8. [Dispatch](#8-dispatch)
9. [Money](#9-money)
10. [Live tracking](#10-live-tracking)
11. [Configuration and environments](#11-configuration-and-environments)
12. [Testing](#12-testing)
13. [What does not work yet](#13-what-does-not-work-yet)
14. [Open decisions](#14-open-decisions)
15. [Running it locally](#15-running-it-locally)

---

## 1. What this is

An API-only backend for an Australian cleaning marketplace. Customers book cleans on
their properties; the system finds the nearest eligible contractor, freezes a price
when one accepts, charges the customer, tracks the job to completion, and pays the
contractor.

There is no web UI beyond the Django admin. Every client — mobile app, admin
dashboard — talks to the same JSON API.

| | |
|---|---|
| Domains | 9 |
| Endpoints | 51 (4 public) |
| Tests | 925, all passing |
| Python / Django | 3.14 / 5.2.17 LTS |

---

## 2. Stack and layout

**Django 5.2 + Django Ninja 1.1** (not DRF), **PostgreSQL** in staging/production,
SQLite locally. **JWT** via `django-ninja-jwt`. **Celery + Redis** for the one
periodic task. **Gunicorn** in production — `runserver` is never used outside dev.

```
cleaning_house/
├── config/              settings (dev/staging/production), urls, celery
├── apps/                the nine domains — each identical in shape:
│   └── <domain>/
│       ├── models.py    data + invariants enforced in clean()
│       ├── services/    ALL business logic and permission checks
│       ├── api/         thin routers + hand-written schemas
│       ├── adapters/    abstract provider interfaces (where relevant)
│       └── admin.py     mostly read-only
├── adapters/            Phase-0 abstract interfaces (SMS, social, storage…)
├── tests/               flat, one file per concern, 925 tests
└── openapi.json         generated; CI-checked for drift
```

Every app follows the same shape. If you have read one domain you can navigate any
other.

---

## 3. The nine domains

| Domain | Owns | Key models |
|---|---|---|
| **accounts** | identity, OTP, social login, JWT | `User`, `OTPVerification`, `SocialAccount` |
| **properties** | customer addresses, serviceability | `Property`, `PropertyAddress` |
| **services** | service catalog and pricing config | `ServiceType`, `PricingConfig` |
| **contractors** | profiles, ABN/insurance verification | `ContractorProfile`, `BusinessRegistration`, `InsuranceDocument` |
| **bookings** | bookings, dispatch, offers | `Booking`, `BookingServiceSelection`, `DispatchOffer` |
| **payments** | customer charge | `Payment` |
| **jobs** | execution, proof photos, live location | `Job`, `JobPhoto`, `JobLocation` |
| **payouts** | contractor payment | `Payout` |
| **support** | support requests | `SupportRequest` |

---

## 4. The business flow, end to end

```
Customer creates booking
  → PENDING, no price, no contractor
  → dispatch offers it to the nearest eligible contractor (60-minute TTL)
       ├─ declines / expires → next nearest, same cascade
       └─ accepts
            → price calculated and FROZEN, booking CONFIRMED
            → customer charged directly (no escrow)
            → Job created ASSIGNED
                 → contractor on the way — live location visible
                 → /start        → IN_PROGRESS   (photos now accepted)
                 → /mark-done    → AWAITING_CUSTOMER_CONFIRMATION
                                   (requires ≥1 BEFORE and ≥1 AFTER photo)
                 → customer confirms → COMPLETED
                      → contractor payout released, zero commission
```

Two points that surprise people:

- **The price does not exist until a contractor accepts.** A booking is created with
  no price and none is calculated. Acceptance is the first and only moment a price is
  computed, frozen, and revealed.
- **Only the customer can complete a job.** There is no admin override and no
  time-based auto-confirmation. The payout is released by the customer's confirm.

---

## 5. Architectural rules

These hold across every domain. Breaking one is a bug, not a style difference.

### The API layer never touches `.objects`

Every read and write goes through `services/*.py`, which re-enforces role gates and
ownership **independently of the view**. Enforcement never depends on the router
having remembered to call a check. Several tests assert this by inspecting source for
`.objects.` in API modules.

### Two separate permission layers

The role gate protects the endpoint; the ownership check protects the object. Neither
is dispensable. Role checks always call `user.has_customer_access()` /
`has_contractor_access()` / `has_admin_access()` — never `role ==` directly.

### 403 vs 404 is a deliberate, documented policy

A resource that exists but is not yours returns **404**, not 403, wherever
distinguishing the two would leak its existence. The exceptions are documented in
place: an unowned *property* at booking creation returns 403 because the spec asks for
it; the shared service catalog returns 403 because nothing user-specific can leak.

### Hiding fields is structural, never conditional

A schema that must not expose a field **does not define it**. Not `None`, not
`exclude_none` — absent. This follows a real regression: the public and admin payout
shapes were once joined by a `Union`, and one wrong member ordering widened the public
shape. Current examples: `ServicePublicOut` (no prices, for every role including
admin), `PaymentSummaryOut` (no `provider_reference`), `OfferOut` (no price).

### Frozen values are never recomputed

`DispatchOffer.distance_km` freezes when the offer is created;
`Booking.computed_price` freezes on acceptance. If distance were recomputed at
acceptance and the contractor had moved, the price would not match the offer they
accepted.

### Side effects run after commit, isolated

Charging, job creation and payout release are `transaction.on_commit(..., robust=True)`
with their exceptions swallowed and logged. A payment provider failure must never turn
a valid acceptance into a 500 or undo it.

### Invariants live in `clean()`, not only in services

`Payment.amount` and `Payout.amount` must equal the frozen price exactly; a rejected
verification document must carry a reason. Enforcing these on the model means every
write path — API, management command, shell — is bound by them.

### Open decisions stay open

Where a product decision has not been made, the code does nothing and says so. It does
not invent a default. See [§14](#14-open-decisions).

---

## 6. Identity and roles

**The phone number is the identity.** There is no username and no password login —
`AbstractBaseUser` with `USERNAME_FIELD = "phone"`, and accounts created through OTP
get an unusable password. Authentication is OTP or Apple/Google only.

Three roles, final: `CUSTOMER`, `CONTRACTOR`, `ADMIN`. `PropertyManager` was settled
as *not* a separate role — it is a CUSTOMER.

**One account can be both customer and contractor.** This is a boolean capability flag
(`is_contractor`) layered on top of `role`, not a role list — because only that one
combination is legitimate, and a general list would have implicitly allowed
`ADMIN + CUSTOMER`, a privilege escalation nobody asked for. ADMIN is exclusive.

Granting the flag is not approval: a new contractor profile starts `UNAVAILABLE` with
no approved documents. **Eligibility is computed live**, never stored — the latest ABN
registration verified, the latest insurance verified and unexpired. A stored flag
would become a silent lie the day after the insurance expired.

`GET /api/auth/me` returns `roles` (an array — read this), `contractor_status` and
`available_modes`, all derived on the fly. The singular `role` is kept only for
backwards compatibility.

---

## 7. Pricing

```
total = Σ[(room_price × room_count) + base_price]  +  (distance_km × price_per_km)
```

Per-service rates live on `ServiceType`; `price_per_km` is one global value in the
`PricingConfig` singleton. Rounding happens at exactly one point — the returned total.

**Add-ons are not a concept in the backend.** An add-on is a normal `ServiceType` with
`room_price = 0`, so the formula yields the flat fee whatever `room_count` arrives —
a client bug cannot distort the price. Grouping them visually is a front-end decision
built on their stable seeded UUIDs.

`calculate_price` is a pure function over primitives and is **stateless by design**: it
reads live catalog values every call. Freezing is the booking domain's job alone. The
module explicitly forbids adding taxes, discounts, platform fees or a price floor —
each is an independent business rule that has not been approved.

`float` is banned from the money path and rejected outright.

---

## 8. Dispatch

A candidate must satisfy all five: `AVAILABLE`, eligible (verified ABN + unexpired
insurance), not already offered this booking in any status, measurable distance, and
**not the booking's own customer**.

Distance is **haversine on stored coordinates** — computed locally, not a routing
engine and not an external API. Nearest-first, with no radius cap. A contractor
without coordinates silently drops out: unknown distance is not zero and not a
default.

**Self-assignment is blocked twice** — once at the query level (`exclude(user_id=...)`,
so it cannot enter the candidate list by any path) and once at the response level, on
both accept *and* decline. Declining is blocked too: allowing it would let a booking's
own owner advance the cascade from a position they have no right to occupy.

A decline and an expiry trigger the **identical** cascade; they remain distinct states
only for future contractor reporting. The expiry task runs every minute, is idempotent
via a conditional `UPDATE`, and never prices anything.

`dispatch_status` (`SEARCHING` / `NO_CONTRACTOR` / `ASSIGNED`) exists because a booking
stays `PENDING` whether the search is live or exhausted — the customer could not tell
"still looking" from "nobody available". It is **display only**: it changes no status,
cancels nothing, and schedules no retry.

A booking stranded at `NO_CONTRACTOR` can be rescheduled
(`POST /api/bookings/{id}/reschedule`), which deletes its old offers and starts a fresh
round. Deleting them is load-bearing: every eligible contractor is otherwise still
excluded and the new search would end instantly.

---

## 9. Money

- **Charge is direct.** No escrow, no authorize/capture, no split. `Payment` has three
  states and the model forbids adding `HELD`/`AUTHORIZED`/`CAPTURED` — escrow was
  withdrawn from the design.
- **Payout is immediate per booking, zero commission**, released by the customer's
  confirmation. `Payout.amount` must equal the frozen price *exactly*; the equality is
  absolute, not a percentage, so a commission cannot be slipped into a calculation.
- **`Payout.contractor` is a User id; `Booking.assigned_contractor` is a profile id.**
  Different identifiers for the same person — never compare them.
- **Idempotency keys are deterministic** (`booking-{id}`, `payout-booking-{id}`): a
  retried request reaches the provider with the same key and cannot double-charge.
- **Acceptance and payment are not atomic.** The booking confirms, then the charge is
  attempted. `CONFIRMED` + `FAILED` is reachable and is surfaced to clients rather than
  hidden. What should happen next — retry, grace period, cancellation — is an open
  decision.

---

## 10. Live tracking

`JobLocation` holds the contractor's position while they travel. It is **not**
`ContractorProfile.latitude/longitude`, which is their fixed business address used to
rank dispatch candidates — writing a moving fix over it would silently change which
bookings they are nearest to.

**The window is `ASSIGNED` only**, the exact inverse of photo upload (`IN_PROGRESS`
only). No job state permits both. Once work starts, the cleaner is inside the
customer's home; following them there is surveillance, not service. When the window
closes the stored point stops being *returned*, so a position is never visible after
the service ends.

One row per job, overwritten — no movement archive, which would need a retention
policy that does not exist. Staleness is computed (90s) from the **server's** receipt
time, never the device's clock, which is untrusted and rejected if set in the future.

`JobLocation` is deliberately absent from the Django admin, for the same reason the
window exists.

**No ETA and no street route** — two coordinates are not a path. That needs a
directions provider (Google/Mapbox), which is not wired in.

---

## 11. Configuration and environments

`DJANGO_ENV` (`dev` / `staging` / `production`) is **mandatory with no default**. This
comes from a real incident: deploying without env vars silently loaded dev settings —
SQLite, no hardening — then failed later on a misleading error. Silent failure in
environment selection is more dangerous than a loud failure on one key.

`SECRET_KEY` and `JWT_SIGNING_KEY` likewise have no defaults. The database reads
`DATABASE_URL` first (what Railway/Heroku inject) and falls back to discrete `DB_*`
vars; an empty URL is treated as absent, because platforms define the variable as an
empty string when a reference fails to resolve.

Production additionally sets `SECURE_PROXY_SSL_HEADER` — mandatory behind Railway's
proxy, or HTTPS redirects loop forever.

Every external provider is selected by a dotted path in settings
(`SMS_ADAPTER`, `SOCIAL_AUTH_ADAPTER`, `PAYMENT_PROVIDER_ADAPTER_CLASS`,
`PAYOUT_PROVIDER_ADAPTER_CLASS`, `JOB_STORAGE_ADAPTER_CLASS`). Swapping in a real
provider changes that string and nothing else.

---

## 12. Testing

925 tests, plain pytest functions, real JWTs (auth is never mocked), one file per
concern, and a distinct phone-number block per file so fixtures cannot collide.

Beyond behaviour, the suite guards architecture:

- **Source introspection** — API modules must contain no `.objects.`; payment modules
  must import no provider SDK.
- **Schema introspection** — public schemas must not define price fields; a
  parametrized check plus a grep-based guard ensure no path id is declared `str` (that
  bug turned malformed ids into 500s across 15 endpoints).
- **Anti-tautology** — the seeded service UUIDs are re-declared in the test rather than
  imported, so the test cannot pass by agreeing with itself.
- **Query counts** — the booking list asserts a fixed query count, so the N+1 that the
  payment summary would otherwise introduce cannot come back.

```bash
pytest                              # all
pytest tests/test_bookings_api.py   # one file
python manage.py export_openapi --check   # schema drift (CI)
```

---

## 13. What does not work yet

Five subsystems depend on providers that have **not been chosen**. Each is an abstract
adapter with a development-only fake, and **every fake refuses to run when
`DEBUG=False`** unless an explicit per-adapter fuse is set — so a production deploy
fails loudly instead of pretending to charge cards or store photos.

| Subsystem | Consequence in production |
|---|---|
| SMS / OTP | `POST /api/auth/otp/request` fails — no code is sent |
| Social login | token cannot be verified |
| Payment | booking confirms, no `Payment` row |
| Payout | job completes, no `Payout` row |
| Photo storage | photo upload fails |

**The practical consequence: login does not work in production.** OTP and social are
the only auth paths, and both depend on unselected providers.

A sixth gap is different in kind — nothing breaks, a feature is simply absent: there is
**no directions provider**, so tracking returns two points with no route and no ETA.

Because `GET /api/services` needs a JWT that production cannot issue, the
`python manage.py list_services` command exists to read the real service UUIDs without
the API.

---

## 14. Open decisions

Deliberately unimplemented. The code does nothing and records why; **do not close one
by inference.**

| Decision | Current behaviour |
|---|---|
| **#16 — nobody accepts** | Returns `None`, booking stays `PENDING`, `dispatch_status = NO_CONTRACTOR`. No auto-cancel, no notification, no scheduled retry. The customer can reschedule. |
| **#12 — cancellation policy** | There is no `CANCELLED` job state at all, and `Booking.CANCELLED` exists but is unreachable. No cancel endpoint. |
| **Payment failure** | Stays `FAILED`; booking stays `CONFIRMED`. No retry, no grace period, no cancellation. |
| **Payout failure** | Stays `FAILED`. No retry, no notification. |
| **Re-verification policy** | No scheduling and no expiry task; expiry is read live. |
| **Duration / recurrence (#17)** | `scheduled_at` is a single UTC instant — no range, no duration, no slot id. |
| **Commission** | Zero, enforced as exact equality. A commission would be a new rule changed explicitly, never slipped into a calculation. |

Before adding a state, field or fallback in these areas, search for `قرار مفتوح`,
`بند مفتوح` or "Open Decision" near the code.

> **A note on the comments.** Explanatory comments throughout the codebase are in
> Arabic, and they carry the *reasoning* — why a field is structured a particular way,
> what was tried before, what must not be added. They are not decoration. Read them
> before changing the code they sit above.

---

## 15. Running it locally

```bash
# 1) create and activate a virtualenv
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # macOS / Linux

# 2) install
pip install -r requirements.txt

# 3) configure
cp .env.example .env
#    set at minimum: DJANGO_ENV=dev, SECRET_KEY, JWT_SIGNING_KEY

# 4) migrate and seed the catalog
python manage.py migrate
python manage.py seed_services      # 7 services with stable UUIDs
python manage.py list_services      # copy the ids for the client team

# 5) run
python manage.py runserver
#    docs at http://127.0.0.1:8000/api/docs

# optional: background worker (needs Redis)
celery -A config worker -l info
celery -A config beat -l info       # offer-expiry task
```

Local development uses SQLite and the fake adapters, so the full flow — OTP, payment,
photos, payout — works end to end without any external provider.

---

*Generated 2026-09-16. Endpoint counts and test totals reflect commit `c385941`.*
