# Cleaning House — Developer Handbook

**For:** mobile app engineers and admin-dashboard engineers building against this backend.
**Backend:** Django 5.2.17 + Django Ninja 1.1.0 · **API version:** 1.0.0
**Interactive docs:** `/api/docs` · **Machine-readable spec:** `/api/openapi.json`

Everything here — every field, status code, error code and message — was extracted
from the running code, not written from memory. Model fields come from Django's
own registry; error responses were produced by calling the endpoints and
recording what came back.

> **Companion document:** [`API_INTEGRATION_GUIDE.md`](API_INTEGRATION_GUIDE.md)
> is the request/response cookbook with a full example per endpoint. This
> handbook covers what that one does not: the data model, the domain rules
> behind the API, the complete error catalogue, and what the admin dashboard can
> and cannot do.

---

## Contents

1. [Read this first — what does not work yet](#1-read-this-first--what-does-not-work-yet)
2. [System shape and conventions](#2-system-shape-and-conventions)
3. [Roles and who can do what](#3-roles-and-who-can-do-what)
4. [The business flow, end to end](#4-the-business-flow-end-to-end)
5. [Data model reference](#5-data-model-reference)
6. [State machines](#6-state-machines)
7. [API surface by role](#7-api-surface-by-role)
8. [Error catalogue — every code the API emits](#8-error-catalogue--every-code-the-api-emits)
9. [Building the mobile app](#9-building-the-mobile-app)
10. [Building the admin dashboard](#10-building-the-admin-dashboard)
11. [Business rules that will surprise you](#11-business-rules-that-will-surprise-you)
12. [Constants and enumerations](#12-constants-and-enumerations)
13. [Deploying and first-run setup](#13-deploying-and-first-run-setup)

---

## 1. Read this first — what does not work yet

Five subsystems depend on external providers **that have not been chosen yet**.
The code defines each as an abstract adapter with a development-only fake, and
each fake refuses to run when `DEBUG=False` — which is the case in production.
They raise `ImproperlyConfigured` the moment they are constructed.

| # | Subsystem | Triggered by | What you will see |
| --- | --- | --- | --- |
| 1 | **SMS / OTP** | `POST /api/auth/otp/request` | Server error; no SMS is sent |
| 2 | **Social login** | `POST /api/auth/social/{provider}` | Server error; token cannot be verified |
| 3 | **Payment** | a contractor accepting an offer | Booking confirms, but no `Payment` row is created |
| 4 | **Payout** | the customer confirming a job | Job completes, but no `Payout` row is created |
| 5 | **Photo storage** | `POST /api/contractor/jobs/{id}/photos` | Server error; nothing is stored |

### The consequence you must plan around

Items 1 and 2 are **the only two ways to obtain a token**. There is no password
login. **Until an SMS or social provider is configured, nobody can authenticate
against production**, so no authenticated endpoint can be exercised there.

Develop against an environment where the fake adapters are enabled
(`SMS_DEV_ALLOW_INSECURE`, `SOCIAL_AUTH_ALLOW_FAKE`, `PAYMENTS_ALLOW_FAKE_ADAPTER`,
`JOBS_ALLOW_FAKE_STORAGE_ADAPTER`, `PAYOUTS_ALLOW_FAKE_ADAPTER`), and treat
production as unavailable for end-to-end testing until those decisions land.

### Two different failure shapes

- **Synchronous (1, 2, 5)** — the provider is called while handling your request,
  so you get a failed HTTP response.
- **Deferred and swallowed (3, 4)** — payment and payout run *after* their
  transaction commits, and their errors are logged, not returned. **The
  triggering request still returns `200`.** The booking really is confirmed and
  the job really is complete; only the money movement is missing.

> **Never treat that `200` as proof the money moved.** Read
> `GET /api/bookings/{id}/payment` or `.../payout` to find out. A `404` there
> means no record exists — which is exactly what happens when the provider is
> unselected.

### What does work

Properties, the service catalog (public and admin), contractor profiles,
verification submission and review, booking creation, auto-dispatch, offer
accept/decline, job state transitions, and every read endpoint.

---

## 2. System shape and conventions

### Base URL

| Environment | URL |
| --- | --- |
| Production | `https://cleaninghouse-production.up.railway.app` |

All paths below include the `/api` prefix and are relative to that host.

### Request and response conventions

| Topic | Rule |
| --- | --- |
| Format | **JSON only.** No XML, no content negotiation. |
| Request `Content-Type` | `application/json` for every body — **except** photo upload, which is `multipart/form-data` |
| Lists | Returned as a **bare array**, never wrapped in `{"results": …}` |
| Pagination | **None.** No `page`/`limit`/`offset`/`cursor` anywhere, no `count`/`next` fields |
| Ids | UUID strings, e.g. `"7c79b140-a05b-4d23-a467-034b582b04e2"` |
| Money | **JSON strings**, not numbers — `"215.37"`. Parse as decimal, never float |
| Coordinates | Also strings — `"-33.868800"` (6 decimal places) |
| Timestamps | ISO 8601 UTC with `Z` and milliseconds: `"2026-09-13T10:15:18.400Z"` |
| Dates | Plain `"YYYY-MM-DD"` (insurance expiry only) |
| Optional fields | Present with `null` — except the role-hidden fields in §7.4 |
| Encoding | UTF-8 throughout |

### Authentication

Send the access token on every protected request:

```
Authorization: Bearer <JWT access token>
```

| Token | Lifetime |
| --- | --- |
| `access` | **15 minutes** |
| `refresh` | 7 days |

> ### ⚠️ There is no token-refresh endpoint
>
> You are issued a `refresh` token, but **no endpoint in this API accepts it** —
> the route is not mounted. When the 15-minute access token expires, the user
> must log in from scratch. Store the refresh token for when the endpoint
> appears, but do not build a silent-refresh flow expecting it to work today.

Public endpoints (no token): `POST /api/auth/otp/request`,
`POST /api/auth/otp/verify`, `POST /api/auth/social/{provider}`, `GET /api/health`.

---

## 3. Roles and who can do what

Accounts are always created as `CUSTOMER`. A single account can hold **both**
the customer and contractor permissions — one login, one `user_id`, one phone
number, both sides of the marketplace. `ADMIN` is **exclusive**: it is never
combined with either.

Read permissions from `roles` (an array) on `GET /api/auth/me`. The legacy
singular `role` remains for backwards compatibility and reports only the primary
role, so a dual-role account shows `"role": "CUSTOMER"` while `roles` holds both.

| | CUSTOMER | CONTRACTOR | ADMIN |
| --- | --- | --- | --- |
| Reads and edits **own** name / email | ✅ | ✅ | ✅ |
| May also hold the other permission | ✅ + contractor | ✅ + customer | ❌ exclusive |
| Owns properties, creates bookings | ✅ | — | — |
| Confirms job completion | ✅ **only for own booking** | — | — |
| Contractor profile + availability | — | ✅ own only | — |
| Submits ABN / insurance | — | ✅ own only | — |
| Responds to dispatch offers | — | ✅ addressee only | — |
| Uploads job photos, marks done | — | ✅ if assigned | — |
| Reads the public service catalog | ✅ | ✅ | ✅ |
| Manages catalog + pricing | — | — | ✅ |
| Reviews verifications | — | — | ✅ |
| Sees `provider_reference` | — | — | ✅ |

**Three things that surprise people:**

- **`ADMIN` is not a superuser over this API.** An admin cannot create a booking,
  cannot edit a contractor's profile or availability, and **cannot confirm a job
  on the customer's behalf**.
- **Contractors accept offers without seeing the price.** The amount is only
  readable afterwards, via the payout endpoint.
- **Nobody sees raw prices outside `/api/admin/*`** — see §7.4.

---

## 4. The business flow, end to end

**1 · The customer creates a booking — with no price shown.**
They pick one of their properties, one or more services with a room count each,
and a visit time. The booking is saved as `PENDING` with **no price and no
contractor**: `computed_price` is `null`. Nothing in the API reveals a price yet.

**2 · The system dispatches it automatically.**
Right after the booking commits, the backend finds the **nearest eligible
contractor** and sends them an offer. Eligible means all of:

- `availability_status == AVAILABLE`
- an **approved** business registration **and** a valid, unexpired insurance document
- has not already been offered this booking
- has coordinates on file, so distance can be measured
- **is not the booking's own customer** — one account can hold both sides, and a
  user is never offered their own booking

Candidates are ranked by straight-line (haversine) distance. The customer does
not choose the contractor and the contractor does not browse for work.

> Self-assignment is blocked in two independent places: the candidate query
> excludes the owner, and accepting *or* declining an offer on your own booking
> returns `403 self_assignment_forbidden`. Declining is blocked too — allowing
> it would let the booking's owner drive the dispatch cascade.

**3 · The contractor has 60 minutes.**

- **Declines** → cascades immediately to the next-nearest eligible contractor.
- **Doesn't respond** → a Celery task (running every minute) marks the offer
  `EXPIRED`, which cascades identically.
- **Nobody eligible** → the booking simply **stays `PENDING` with no active
  offer**. It is *not* cancelled and nothing retries. This is an open product
  decision, not a bug.

**4 · On acceptance, five things happen at once.**

1. The price is **calculated and frozen** onto the booking.
2. Booking → `CONFIRMED`, contractor assigned.
3. **The price becomes visible to the customer** — the first moment it is.
4. The customer is **charged directly** (no escrow, no separate capture).
5. The job is created as `IN_PROGRESS`.

The price is never recalculated afterwards, even if an admin edits catalog prices.

> **Price formula.** For each selected service:
> `(room_price × room_count) + base_price`, summed across services, plus
> `distance_km × price_per_km` added **once** for the whole booking. The distance
> is the one frozen on the accepted offer, not re-measured.

**5 · The contractor works and documents it.**
Uploads at least one `BEFORE` and one `AFTER` photo, then marks the job done.
Marking done without both is rejected. This moves the job to
`AWAITING_CUSTOMER_CONFIRMATION` and freezes the photo evidence — **it does not
complete the job**.

**6 · The customer confirms.**
Only the booking's own customer. **No timeout, no admin override.** If the
customer never confirms, the job stays in `AWAITING_CUSTOMER_CONFIRMATION`
forever.

**7 · The contractor is paid.**
Confirmation releases the payout immediately, per booking — never batched — for
the **full booking price with zero commission**.

```
Customer                 API                          Contractor
   |                      |                                |
   |-- create booking --->| PENDING · no price             |
   |                      |-- auto-dispatch -------------->| offer · 60 min TTL
   |                      |<-- decline / timeout ----------| -> next contractor
   |                      |                                |    (or stays PENDING)
   |                      |<-- accept ---------------------|
   |<-- price revealed ---| price frozen · CONFIRMED       |
   |                      | charged · job IN_PROGRESS      |
   |                      |<-- BEFORE + AFTER photos ------|
   |                      |<-- mark-done ------------------| AWAITING_CUSTOMER_CONFIRMATION
   |-- confirm ---------->| COMPLETED                      |
   |                      |-- payout · full · 0% fee ----->|
```

---

## 5. Data model reference

Field types are Django's; the JSON type is what you actually receive.

### 5.1 `User` — `accounts_user`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key |
| `phone` | string(32) | **unique — the login identifier** |
| `email` | string(254) | nullable, unique when present |
| `full_name` | string(255) | may be empty |
| `role` | enum | `CUSTOMER` · `CONTRACTOR` · `ADMIN` — default `CUSTOMER`; the **primary** role |
| `is_contractor` | bool | may **also** work as a contractor. Never exposed by the API — read `roles` instead |
| `status` | enum | `ACTIVE` · `INACTIVE` · `SUSPENDED` — default `ACTIVE` |
| `is_active` | bool | technical login gate, **separate from `status`** |
| `is_staff` | bool | Django-admin access only |
| `date_joined`, `updated_at` | datetime | |

> `status` is the business state; `is_active` is the technical one. They are
> deliberately independent. The API exposes `id`, `phone`, `role`, `status`,
> `full_name`, `email`, `roles`, `contractor_status` and `available_modes` — and
> nothing else. `is_staff`, `is_superuser`, `password`, `last_login`,
> `is_contractor` and the permission relations are never returned.
>
> **Permissions are read from the database on every request**, not from the JWT.
> The token carries a `role` claim for information only. Suspending an account or
> revoking its contractor permission therefore takes effect **immediately**, on
> tokens that were already issued — no logout, no token revocation needed.
>
> `full_name` and `email` are the only two a user can change themselves, via
> `PATCH /api/auth/me`. `phone` is the login identifier and would need OTP
> verification of the new number; `role` and `status` are privilege fields and
> are managed by an administrator.

**Related:** `OTPVerification` (stores only a `code_hash` — **the raw code is
never stored**) and `SocialAccount` (unique per `provider` + `provider_user_id`).

### 5.2 `Property` / `PropertyAddress`

**`Property`** — `properties_property`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | |
| `owner` | FK → User | `CASCADE` |
| `label` | string(100) | may be empty, e.g. "Home" |
| `property_type` | enum | `HOUSE` `UNIT` `TOWNHOUSE` `APARTMENT` `OTHER` |
| `is_active` | bool | **soft-delete flag** |
| `created_at`, `updated_at` | datetime | |

**`PropertyAddress`** — one-to-one with `Property`, `CASCADE`

| Field | Type | Notes |
| --- | --- | --- |
| `street_address` | string(255) | |
| `suburb` | string(120) | |
| `state` | enum | the 8 Australian states/territories |
| `postcode` | string(4) | **exactly 4 digits** |
| `country` | string(2) | always `"AU"`, **read-only** |
| `latitude` / `longitude` | decimal(9,6) | **nullable — see warning** |
| `raw_input` | text | optional, as originally typed |

> ⚠️ **Coordinates come from the device's GPS**, sent with the address on
> `POST /api/properties` and fixable later with `PATCH`. There is no geocoding:
> the server never derives them from the street address, nor checks that the two
> agree. Range is validated (±90 / ±180, `422` otherwise) but not the country.
>
> **A property with no coordinates can never be dispatched** — it drops out of
> every contractor search silently. The booking is still accepted with `201` and
> then never receives an offer, so clients should treat the two fields as
> required, and the admin dashboard should surface properties that lack them.

### 5.3 `ServiceType` / `PricingConfig`

**`ServiceType`** — `services_servicetype`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | this is the `service_type_id` used in bookings |
| `name` | string(120) | **unique** |
| `description` | text | may be empty |
| `room_price` | decimal(10,2) | per room, **for this service** — ≥ 0 |
| `base_price` | decimal(10,2) | flat, **for this service** — ≥ 0 |
| `is_active` | bool | soft-delete |

**`PricingConfig`** — a **singleton** (`id` is always `1`)

| Field | Type | Notes |
| --- | --- | --- |
| `price_per_km` | decimal(10,2) | **one global rate for the whole platform**, not per service |

> The row is created on first read, so it always exists once anyone has looked
> at it.

### 5.4 `ContractorProfile` and verification

**`ContractorProfile`** — one-to-one with `User`

| Field | Type | Notes |
| --- | --- | --- |
| `user` | OneToOne → User | `CASCADE` |
| `business_name` | string(255) | may be empty |
| `street_address`, `suburb`, `state`, `postcode` | | all may be empty |
| `country` | string(2) | always `"AU"`, read-only |
| `latitude` / `longitude` | decimal(9,6) | **from the device's GPS; required for dispatch** — a contractor without them is never offered work |
| `availability_status` | enum | `AVAILABLE` · `UNAVAILABLE` — **default `UNAVAILABLE`** |

**`BusinessRegistration`** and **`InsuranceDocument`** share a common review base:

| Field | Type | Notes |
| --- | --- | --- |
| `contractor` | FK → ContractorProfile | `CASCADE` |
| `status` | enum | `PENDING` · `VERIFIED` · `REJECTED` — default `PENDING` |
| `reviewed_by` | FK → User | `SET_NULL` — deleting an admin keeps the record |
| `reviewed_at` | datetime | nullable |
| `rejection_reason` | text | **mandatory when rejecting** (enforced in the model) |

`BusinessRegistration` adds `abn` (string(11), **11 digits, format only — no
registry lookup**) and `business_name`.
`InsuranceDocument` adds `document_reference` (**a policy number as text, not a
file upload**) and `expiry_date`.

> **Eligibility is computed, never stored.** There is no `is_verified` flag. A
> contractor is eligible when they currently have an approved registration *and*
> an approved, unexpired insurance document. Multiple submissions are legitimate
> (re-submitting after rejection, renewing insurance) — the newest wins.
>
> **Nothing re-checks expiry.** There is no scheduled re-verification; an expired
> document simply stops counting the next time eligibility is read.

### 5.5 `Booking` and dispatch

**`Booking`** — `bookings_booking`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | |
| `customer` | FK → User | `CASCADE` |
| `property` | FK → Property | **`PROTECT`** — a booked property cannot be deleted |
| `status` | enum | `PENDING` · `CONFIRMED` · `CANCELLED` |
| `scheduled_at` | datetime | **the visit time, in UTC**; nullable only for legacy rows |
| `customer_timezone` | string(64) | IANA name from the property's state — **display only** |
| `computed_price` | decimal(10,2) | **null until acceptance**, then frozen forever |
| `assigned_contractor` | FK → ContractorProfile | `SET_NULL`, null until acceptance |

**`BookingServiceSelection`** — the lines of a booking

| Field | Type | Notes |
| --- | --- | --- |
| `booking` | FK → Booking | `CASCADE` |
| `service_type` | FK → ServiceType | **`PROTECT`** |
| `room_count` | positive int | **0 is allowed** — means base fee only |

> **There is no per-line price.** Deliberately: the customer sees one total, and
> a stored line price would be a second source of truth competing with the frozen
> snapshot.

**`DispatchOffer`** — `bookings_dispatchoffer`

| Field | Type | Notes |
| --- | --- | --- |
| `booking` / `contractor` | FK | **unique together** — never offered twice to the same contractor |
| `status` | enum | `PENDING` · `ACCEPTED` · `DECLINED` · `EXPIRED` |
| `distance_km` | decimal(10,3) | **frozen at offer time, never recomputed** |
| `offered_at`, `responded_at`, `expires_at` | datetime | TTL is **60 minutes** |

### 5.6 `Job` / `JobPhoto`

**`Job`** — one-to-one with `Booking` (`PROTECT`)

| Field | Type | Notes |
| --- | --- | --- |
| `status` | enum | `IN_PROGRESS` · `AWAITING_CUSTOMER_CONFIRMATION` · `COMPLETED` |
| `marked_done_at` | datetime | set by the contractor |
| `confirmed_at` | datetime | set by the customer |

**`JobPhoto`**

| Field | Type | Notes |
| --- | --- | --- |
| `job` | FK → Job | `CASCADE` |
| `photo_type` | enum | `BEFORE` · `AFTER` |
| `storage_key` | text | **opaque provider reference — admin-visible only** |
| `uploaded_by` | FK → User | `PROTECT` |

> There is **no cancelled job state** and no admin override.

### 5.7 `Payment` / `Payout`

Both are **one-to-one with `Booking`** at the database level — a hard guarantee
against double-charging and double-paying.

**`Payment`** — `payments_payment`

| Field | Type | Notes |
| --- | --- | --- |
| `amount` | decimal(10,2) | **must equal `booking.computed_price` exactly** |
| `method` | enum | `CARD` · `APPLE_PAY` · `GOOGLE_PAY` — defaults to `CARD` |
| `status` | enum | `PENDING` · `SUCCEEDED` · `FAILED` |
| `provider_reference` | text | opaque, **admin-only** |
| `failure_reason` | text | populated on failure |

**`Payout`** — `payouts_payout`

| Field | Type | Notes |
| --- | --- | --- |
| `contractor` | FK → **User** | `PROTECT` — copied at creation, not derived later |
| `amount` | decimal(10,2) | **the full booking price — zero commission** |
| `status` | enum | `PENDING` · `SUCCEEDED` · `FAILED` |

> ⚠️ **`Payout.contractor` is a USER id.** `Booking.assigned_contractor` is a
> **ContractorProfile id**. Different identifiers for the same person — never
> compare them directly.
>
> There are **no escrow states** (`HELD`/`AUTHORIZED`/`CAPTURED`) and **no
> batching states** (`BATCHED`/`SCHEDULED`) — both models were deliberately
> ruled out.

---

## 6. State machines

### Booking

| State | Entered when | Next |
| --- | --- | --- |
| `PENDING` | created | `CONFIRMED` |
| `CONFIRMED` | a contractor accepts — price frozen, charged, job created | *(terminal)* |
| `CANCELLED` | — | *(defined but **unreachable**)* |

> **No cancellation endpoint exists.** `CANCELLED` is in the model but nothing
> sets it. Do not ship a cancel button. Also note `PENDING` is **not** guaranteed
> to progress — with no eligible contractor it stays there indefinitely.

### DispatchOffer

| State | Entered when | Next |
| --- | --- | --- |
| `PENDING` | dispatch creates it — valid 60 min | `ACCEPTED` · `DECLINED` · `EXPIRED` |
| `ACCEPTED` | contractor accepts in time | *(terminal)* |
| `DECLINED` | contractor declines | *(terminal)* → cascades |
| `EXPIRED` | 60 min pass; the beat task records it | *(terminal)* → cascades |

`DECLINED` and `EXPIRED` behave identically; they stay separate states only to
distinguish "refused" from "ignored" for future contractor reporting.

> **Timing subtlety:** an offer is refused the instant the clock passes
> `expires_at`, even before the beat task flips the stored status. Compare
> `expires_at` to now — do not trust `status` alone.

> **There is no endpoint to list or read offers.** A contractor must already know
> the `offer_id`. **An "available jobs" screen cannot be built today.**

### Job

| State | Entered when | Next |
| --- | --- | --- |
| `IN_PROGRESS` | booking confirmed — **photos accepted only here** | `AWAITING_CUSTOMER_CONFIRMATION` |
| `AWAITING_CUSTOMER_CONFIRMATION` | contractor marks done (needs BEFORE **and** AFTER) | `COMPLETED` |
| `COMPLETED` | **customer** confirms — releases payout | *(terminal)* |

### Payment and Payout

Identical shape for both:

| State | Entered when | Next |
| --- | --- | --- |
| `PENDING` | record created, just before the provider call | `SUCCEEDED` · `FAILED` |
| `SUCCEEDED` | provider confirms | *(terminal)* |
| `FAILED` | provider declines — see `failure_reason` | *(terminal)* — **no retry endpoint** |

> A failed payment leaves the booking `CONFIRMED` and the job proceeding. A
> failed payout leaves the job `COMPLETED`. Reconciliation is currently manual.

---

## 7. API surface by role

44 endpoints. Full request/response examples live in
[`API_INTEGRATION_GUIDE.md`](API_INTEGRATION_GUIDE.md); this is the map.

### 7.1 Public (no token)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | liveness probe |
| `POST` | `/api/auth/otp/request` | send an OTP |
| `POST` | `/api/auth/otp/verify` | verify it → `access` + `refresh` + user |
| `POST` | `/api/auth/social/{provider}` | `APPLE` or `GOOGLE` → same shape |

Both login flows **create the account implicitly** on first success, always as
`CUSTOMER`. There is no registration endpoint.

### 7.2 Any authenticated role

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/auth/me` | **authoritative** identity + `roles`, `contractor_status`, `available_modes` |
| `PATCH` | `/api/auth/me` | update **own** `full_name` and `email` only |
| `GET` | `/api/services` | active services — **no prices** |
| `GET` | `/api/services/{id}` | one active service — **no prices** |

> `GET /api/auth/me` beats the JWT claims: a role changed after the token was
> issued shows up here first.

> ### ⚠️ Never hard-code services as a client-side enum
>
> `service_selections[].service_type_id` in `POST /api/bookings` is a **UUID**,
> and there is **no endpoint anywhere that accepts a service by name**. Sending a
> name gives `422`; sending a made-up UUID gives `400 service_not_found`.
>
> So a client-side `enum { regular, deep, endOfLease }` has nothing to send when
> the user actually books. Matching back by name is fragile in three ways, all
> real:
>
> - `ServiceType.name` is editable at any time via
>   `PATCH /api/admin/services/{id}`.
> - An admin can add a fourth service, or more.
> - An admin can deactivate one — it then **disappears** from `GET /api/services`
>   and booking it returns `400 inactive_service`. Your list can shrink, not just
>   grow.
>
> **Drive the list from `GET /api/services` and keep each `id`.** If your UI maps
> services to fixed icons and local copy, key that map on **`id`, not `name`** —
> the id never changes, the name can. Fall back to a generic icon for an
> unrecognised service rather than dropping it silently.

#### Getting the real ids

The catalog starts **empty**: a fresh database has zero services, so
`GET /api/services` returns `[]` until an admin creates some. Two management
commands make the ids available without needing a token — useful because login
is currently blocked in production (§1):

```bash
python manage.py seed_services          # create the three baseline services
python manage.py seed_services --dry-run # preview; writes nothing
python manage.py list_services          # table: id, name, active, description
python manage.py list_services --json   # paste-ready JSON for the client
```

`seed_services` is **idempotent** and uses **fixed ids that are identical in
every environment**, so a client icon map keyed on id can be written once:

**Baseline services** — priced per room plus a base fee:

| Service | `service_type_id` |
| --- | --- |
| Regular Cleaning | `a1b2c3d4-0001-4000-8000-000000000001` |
| Deep Cleaning | `a1b2c3d4-0002-4000-8000-000000000002` |
| End of Lease Cleaning | `a1b2c3d4-0003-4000-8000-000000000003` |

**Add-ons** — a flat fee, not per room:

| Add-on | `service_type_id` |
| --- | --- |
| Inside Oven Clean | `a1b2c3d4-0101-4000-8000-000000000101` |
| Interior Windows | `a1b2c3d4-0102-4000-8000-000000000102` |
| Carpet Steam Clean | `a1b2c3d4-0103-4000-8000-000000000103` |
| Balcony Clean | `a1b2c3d4-0104-4000-8000-000000000104` |

> **There is no "add-on" concept in the backend.** An add-on is an ordinary
> `ServiceType` whose `room_price` is `0`, so the pricing formula
> `(room_price × room_count) + base_price` collapses to the flat `base_price`.
> Send it in `service_selections` like any other service, with
> `"room_count": 0`. Because `room_price` is zero, a wrong `room_count` cannot
> distort the price.
>
> They appear in `GET /api/services` mixed in with the baseline services — the
> API does not group them. Showing them in a separate "Add-ons" section is a
> client decision, made by matching the four ids above.

Re-running it never duplicates a service and **never overwrites an admin's
edits** — prices and names stay under admin control via
`PATCH /api/admin/services/{id}`. The seeded prices are starting values, not a
pricing decision.

> These three ids are pinned by a test. Changing one fails the build, because it
> would break the icon map in every shipped client.

### 7.3 Role-gated

**CUSTOMER** — properties (5), bookings (3), job confirm (1)

| Method | Path |
| --- | --- |
| `POST` `GET` | `/api/properties` |
| `GET` `PATCH` `DELETE` | `/api/properties/{property_id}` |
| `POST` `GET` | `/api/bookings` |
| `GET` | `/api/bookings/{booking_id}` |
| `POST` | `/api/bookings/{booking_id}/job/confirm` |

**CONTRACTOR** — profile (4), verification (4), offers (2), jobs (2)

| Method | Path |
| --- | --- |
| `POST` `GET` `PATCH` | `/api/contractor/profile` |
| `PATCH` | `/api/contractor/profile/availability` |
| `POST` `GET` | `/api/contractor/business-registration` |
| `POST` `GET` | `/api/contractor/insurance` |
| `POST` | `/api/contractor/offers/{offer_id}/accept` |
| `POST` | `/api/contractor/offers/{offer_id}/decline` |
| `POST` | `/api/contractor/jobs/{job_id}/photos` *(multipart)* |
| `POST` | `/api/contractor/jobs/{job_id}/mark-done` |

**ADMIN** — catalog (7), contractors (2), verification review (3)

| Method | Path |
| --- | --- |
| `POST` `GET` | `/api/admin/services` |
| `GET` `PATCH` `DELETE` | `/api/admin/services/{service_id}` |
| `GET` `PATCH` | `/api/admin/pricing-config` |
| `GET` | `/api/admin/contractors` |
| `GET` | `/api/admin/contractors/{profile_id}` |
| `GET` | `/api/admin/verifications/pending` |
| `PATCH` | `/api/admin/business-registration/{registration_id}` |
| `PATCH` | `/api/admin/insurance/{document_id}` |

**Mixed audience** — the resource decides, not a role gate

| Method | Path | Who |
| --- | --- | --- |
| `GET` | `/api/bookings/{id}/job` | owner customer · assigned contractor · admin |
| `GET` | `/api/bookings/{id}/payment` | owner customer · admin |
| `GET` | `/api/bookings/{id}/payout` | payee contractor · admin |

### 7.4 Fields hidden by role

| Field | Visible to | Everyone else sees |
| --- | --- | --- |
| `provider_reference` on payment | `ADMIN` | **key absent from the JSON** |
| `provider_reference` on payout | `ADMIN` | **key absent from the JSON** |
| `storage_key` on a job photo | `ADMIN` | key present, value `null` |
| `room_price` / `base_price` on `/api/services` | **nobody** — use `/api/admin/services` | **keys absent for every role** |

> The distinction matters. An **absent key** means "you may not see this"; a key
> **present with `null`** means "you may, and there is no value yet". Do not
> write client code assuming `provider_reference` always exists.

---

## 8. Error catalogue — every code the API emits

### 8.1 Two response shapes — handle both

**Shape A — domain errors** (`400` `403` `404` `409`, and OTP `429`). Flat, with
a machine-readable `code`:

```json
{ "code": "booking_not_found", "detail": "Booking not found." }
```

**Auth-domain errors carry a third key.** Every error from `/api/auth/*` — not
only the cooldown — includes `retry_after_seconds`, because that domain's error
schema declares it. It holds a number only on `429 otp_resend_cooldown` and is
`null` otherwise:

```json
{ "code": "otp_resend_cooldown", "detail": "Please wait before requesting another code.", "retry_after_seconds": 42 }
```

```json
{ "code": "unsupported_provider", "detail": "Unsupported provider: FACEBOOK", "retry_after_seconds": null }
```

Errors from every **other** domain omit the key entirely. So read it as
"`retry_after_seconds` is a number" rather than "the key exists".

**Shape B — framework errors** (`422` validation, `401` auth). **No `code`
field**, and for `422` the `detail` is an **array**:

```json
{ "detail": [ { "type": "string_pattern_mismatch", "loc": ["body", "payload", "address", "postcode"], "msg": "String should match pattern '^\\d{4}$'", "ctx": { "pattern": "^\\d{4}$" } } ] }
```

```json
{ "detail": "Unauthorized" }
```

**Branch on the type of `detail`, not on the status code:**

```js
if (Array.isArray(body.detail)) {
  showFieldErrors(body.detail);        // 422 — loc[2:] is the field path
} else if (body.code) {
  handleDomainError(body.code, body.detail);
} else {
  showMessage(body.detail);            // 401 and anything else
}
```

### 8.2 `403` vs `404` — the part that costs teams a day

- **`403`** = your **role** is wrong. The resource may well exist.
- **`404`** = it does not exist **or** it is not yours — **and the API will not
  tell you which.**

That second response is byte-identical either way, deliberately: if ownership
failures returned `403`, an attacker could enumerate valid ids by watching which
ones came back `403` instead of `404`.

**So never show "this booking was deleted".** Say "not found or not available to
you".

| Resource | Wrong role | Exists but not yours |
| --- | --- | --- |
| Properties | `403` | `404` |
| Bookings (read) | `403` | `404` |
| **Booking creation, unowned property** | `403` | **`403`** — the exception |
| `GET .../job`, `.../payment`, `.../payout` | **`404`** | `404` — every failure is `404` |
| Job photos, mark-done | `403` | `403` |
| Contractor offers | `403` | `403` |
| Public services | n/a — all roles may read | `404` if unknown **or inactive** |
| Admin endpoints | `403` | `404` |

### 8.3 Every domain error code

Captured by actually triggering each path. `detail` text is the real message.

#### Authentication

| Status | `code` | `detail` | When |
| --- | --- | --- | --- |
| `400` | `unsupported_provider` | `Unsupported provider: FACEBOOK` | provider is not `APPLE`/`GOOGLE` |
| `400` | `otp_not_found` | `Verification failed.` | no pending code for that phone |
| `400` | `otp_invalid_code` | `Invalid verification code.` | wrong code |
| `400` | `otp_expired` | `This verification code has expired.` | past its 5 minutes |
| `401` | `social_auth_failed` | `Invalid provider token.` | provider rejected the token |
| `403` | `inactive_user` | `This account is not active.` | account not `ACTIVE` |
| `429` | `otp_resend_cooldown` | `Please wait before requesting another code.` | re-requested within 60 s — the **only** case where `retry_after_seconds` is non-null |
| `429` | `otp_max_attempts` | `Too many incorrect attempts.` | 5 wrong tries |

#### Properties

| Status | `code` | `detail` |
| --- | --- | --- |
| `403` | `invalid_owner_role` | `Only customers can manage properties.` |
| `404` | `property_not_found` | `Property not found.` |
| `422` | *(shape B)* | schema validation — e.g. postcode not 4 digits |

#### Services

| Status | `code` | `detail` |
| --- | --- | --- |
| `403` | `admin_role_required` | `Only admins can manage the service catalog.` |
| `404` | `service_not_found` | `Service type not found.` |
| `422` | `validation_error` | `name: Service type with this Name already exists.` |

#### Contractor profile and verification

| Status | `code` | `detail` |
| --- | --- | --- |
| `403` | `invalid_contractor_role` | `Only contractors can have a contractor profile.` |
| `403` | `admin_role_required` | *(admin-only verification routes)* |
| `404` | `contractor_profile_not_found` | `Contractor profile not found.` |
| `404` | `verification_not_found` | `Business registration not found.` |
| `409` | `contractor_profile_exists` | `This user already has a contractor profile.` |
| `400` | `rejection_reason_required` | `A rejection reason is required when rejecting.` |
| `422` | `invalid_review_status` | status is not a valid decision |

> Note `rejection_reason_required` is a **`400`**, not the `422` used elsewhere
> for input problems — a deliberate exception in this project.

#### Bookings

| Status | `code` | `detail` |
| --- | --- | --- |
| `403` | `invalid_customer_role` | `Only customers can manage bookings.` |
| `403` | `property_forbidden` | `You do not have access to this property.` |
| `404` | `property_not_found` | `Property not found.` |
| `404` | `booking_not_found` | `Booking not found.` |
| `400` | `empty_service_selection` | `A booking requires at least one service selection.` |
| `400` | `inactive_service` | `Service type 'X' is inactive and cannot be booked.` |
| `400` | `service_not_found` | `Service type <uuid> not found.` |
| `400` | `invalid_room_count` | room count negative or not an integer |
| `400` | `scheduled_at_in_past` | `The scheduled visit must be in the future.` |
| `400` | `outside_business_hours` | `The scheduled visit must fall between 07:00 and 19:00 local time (Australia/Sydney); got 23:00.` |
| `422` | *(shape B)* | `scheduled_at` missing entirely |

#### Dispatch offers

| Status | `code` | `detail` |
| --- | --- | --- |
| `403` | `invalid_contractor_role` | caller is not a contractor |
| `403` | `offer_forbidden` | `This offer is not addressed to you.` |
| `403` | `self_assignment_forbidden` | `You cannot accept an offer on your own booking.` — also on decline |
| `404` | `offer_not_found` | `Offer not found.` |
| `409` | `offer_not_actionable` | `Offer is accepted or expired and cannot be accepted.` |

#### Jobs

| Status | `code` | `detail` |
| --- | --- | --- |
| `403` | `job_forbidden` | `Only contractors can act on jobs.` |
| `404` | `job_not_found` | `No job found for this booking.` |
| `400` | `invalid_photo_type` | `Invalid photo type: SIDE.` |
| `400` | `empty_photo` | `Uploaded file is empty.` |
| `400` | `missing_proof_photos` | `At least one BEFORE photo and one AFTER photo are required … (missing: AFTER, BEFORE).` |
| `409` | `job_not_accepting_photos` | `Job is AWAITING_CUSTOMER_CONFIRMATION and no longer accepts photos.` |
| `409` | `invalid_job_status` | `Job is IN_PROGRESS and cannot be confirmed.` |

#### Payment and payout

| Status | `code` | `detail` |
| --- | --- | --- |
| `404` | `payment_not_found` | `No payment found for this booking.` |
| `404` | `payout_not_found` | `No payout found for this booking.` |

Both are the **only** error these endpoints return — unknown booking, missing
record and no permission are all indistinguishable.

Codes that exist internally but are not reachable through today's endpoints
(there is no manual charge/payout trigger): `booking_not_confirmed`,
`booking_has_no_price`, `payment_already_exists`, `payout_already_exists`,
`job_not_completed`, `no_assigned_contractor`, `job_already_exists`.

### 8.4 `409` means re-read, not retry

The request was valid and you were allowed, but the resource **is not in a state
that permits it**. Retrying identically will not help — refresh and update the UI.

| Situation | Endpoint |
| --- | --- |
| Offer already answered, or past its 60 minutes | offer accept/decline |
| Contractor profile already exists | `POST /api/contractor/profile` |
| Photo uploaded after mark-done | photo upload |
| Mark-done on a job not `IN_PROGRESS` | mark-done |
| Confirm before mark-done, or twice | job confirm |

Two contractors racing to accept the same booking is the canonical case: one gets
`200`, the other `409`. That is normal, not an error to report.

---

## 9. Building the mobile app

### 9.1 Customer journey → endpoints

| Screen | Call |
| --- | --- |
| Login | `POST /api/auth/otp/request` → `POST /api/auth/otp/verify` |
| Bootstrap | `GET /api/auth/me` — branch the whole UI on `role` |
| Profile / "Welcome back, {name}" | `GET /api/auth/me` → `full_name` |
| Edit personal details | `PATCH /api/auth/me` — `full_name` and `email` only |
| "Work with Cleano" entry point | `POST /api/contractor/profile` — see [§9.2](#92-contractor-journey--endpoints) |
| My properties | `GET /api/properties` |
| Add property | `POST /api/properties` (property + address in **one** call) — **send the device's GPS** |
| Pick a service | `GET /api/services` — use `id` as `service_type_id` |
| Pick add-ons | same call — the flat-fee services, sent with `"room_count": 0` |
| Create booking | `POST /api/bookings` — **show no price on this screen** |
| Track booking | `GET /api/bookings` / `GET /api/bookings/{id}` — **poll** |
| Job progress + photos | `GET /api/bookings/{id}/job` |
| Confirm completion | `POST /api/bookings/{id}/job/confirm` |
| Receipt | `GET /api/bookings/{id}/payment` |

### 9.2 Contractor journey → endpoints

| Screen | Call |
| --- | --- |
| "Work with Cleano" — join | `POST /api/contractor/profile` — **must include GPS coordinates** |
| Application status | `GET /api/auth/me` → `contractor_status` |
| Submit ABN | `POST /api/contractor/business-registration` |
| Submit insurance | `POST /api/contractor/insurance` |
| Per-document detail + rejection reason | `GET` on both of the above — arrays, newest first |
| Go online/offline *(once approved)* | `PATCH /api/contractor/profile/availability` |
| Respond to an offer | `POST /api/contractor/offers/{id}/accept` or `/decline` |
| Upload photos | `POST /api/contractor/jobs/{id}/photos?photo_type=BEFORE\|AFTER` *(multipart)* |
| Finish | `POST /api/contractor/jobs/{id}/mark-done` |
| Earnings | `GET /api/bookings/{id}/payout` |

There is **no separate contractor account and no dedicated join endpoint**. An
existing customer becomes a contractor by creating a contractor profile on the
account they already have — same login, same `user_id`, and every booking and
property they owned stays theirs.

Creating the profile grants the contractor permission immediately, which is what
puts `CONTRACTOR` into `roles` and makes these screens reachable. It does **not**
approve them: dispatch additionally requires an approved ABN *and* valid
insurance. Use `contractor_status` to decide what to render:

| `contractor_status` | Show |
| --- | --- |
| `NONE` | the "Work with Cleano" call to action |
| `PENDING` | "your application is under review" |
| `ACTION_REQUIRED` | what to re-submit — read `rejection_reason` from the document endpoints |
| `APPROVED` | the working contractor UI |
| `SUSPENDED` | a blocked state; the account itself is inactive |

A second profile on the same account returns `409 contractor_profile_exists`; an
`ADMIN` attempting to apply gets `403 invalid_contractor_role`.

### 9.3 Things that will bite you

**Branch the UI on `roles`, not `role`.** One account can be both a customer and
a contractor. The singular `role` is legacy and reports only the primary role, so
a dual-role user looks like a plain `CUSTOMER` through it. Build the mode switch
from `available_modes`, store the last-used mode locally, and re-check on launch
that it is still listed — a suspended contractor loses it. Switching modes needs
no API call, no new OTP and no re-login; it grants nothing, and every endpoint
re-checks the real permissions server-side. See
[§3.6 of the integration guide](API_INTEGRATION_GUIDE.md) for the four
`contractor_status` states and the join flow.

**Services must come from the API, never from a client enum.** Booking takes a
`service_type_id` UUID and nothing else — a hard-coded enum has no id to send
when the user books. Keep your fixed icons and local "what's included" copy, but
**key them on `id`**, populate the list from `GET /api/services`, and fall back
to a generic icon for anything unrecognised. Full reasoning and the fixed ids are
in §7.2.

**A missing GPS reading fails silently.** Both `POST /api/properties` and
`POST /api/contractor/profile` accept `latitude`/`longitude` and both treat them
as optional — but the matching engine ranks by distance, so either side without
a location drops out of every search. The booking still returns `201` and then
**never receives an offer, with no error anywhere**. Capture the device's GPS and
treat both fields as required; a property saved without them can be repaired with
`PATCH /api/properties/{id}`.

**Polling is the only option.** No push channel, no websocket, no
offer-list endpoint. A customer learns of acceptance by polling the booking until
`status` becomes `CONFIRMED`.

**Sessions die after 15 minutes.** No refresh endpoint — on `401`, send the user
back to login. Do not build silent refresh yet.

**The contractor onboarding order is strict.** Profile (with coordinates) →
submit ABN **and** insurance → admin approves **both** → set `AVAILABLE`. Miss any
step and they silently receive no offers, with **no error to explain why**. Build
an onboarding checklist screen from `GET /api/contractor/profile` plus the two
verification lists.

**`photo_type` is a query parameter**, not a form field. The image goes in a part
named `file`.

**Money is strings.** `"215.37"` — never parse as float.

**Show visit times from `scheduled_at_local`.** The server already converted it
using the property's timezone; `customer_timezone` tells you which. Do not
convert `scheduled_at` yourself.

**Validate the visit time client-side first:** future, and 07:00–19:00 **in the
property's local timezone** (not the device's). It saves a round-trip.

### 9.4 Suggested client-side handling

| Response | Do |
| --- | --- |
| `401` | Drop the token, go to login |
| `403` | "Your account type cannot do this" |
| `404` | "Not found or not available to you" — never "deleted" |
| `409` | Re-fetch the resource and re-render; do not retry blindly |
| `422` | Map `detail[].loc[2:]` onto form fields |
| `429` | Respect `retry_after_seconds`; disable the resend button |

---

## 10. Building the admin dashboard

### 10.1 What the API gives you — and what it doesn't

**Available (8 endpoints, §7.3):** full CRUD on the service catalog, the global
per-km price, read-only contractor listings, the verification queue, and
approve/reject.

**Not available through the API — plan around these:**

| Need | Status |
| --- | --- |
| List all bookings across customers | ❌ no endpoint — `GET /api/bookings` is caller-scoped |
| List all payments / payouts | ❌ only per-booking by id |
| List all jobs | ❌ only per-booking by id |
| Search or filter anything | ❌ no query parameters anywhere |
| Pagination | ❌ every list returns everything |
| Change a user's role | ❌ Django admin only |
| Cancel a booking | ❌ no endpoint, and the state is unreachable |
| Retry a failed payment/payout | ❌ no endpoint |
| Reassign a contractor | ❌ no endpoint |
| Create a booking for a customer | ❌ `ADMIN` gets `403` |
| Edit contractor profile/availability | ❌ deliberately contractor-only |

> **Read this as a scoping constraint.** A dashboard listing "all bookings today"
> **cannot be built on the current API**. It needs either new admin endpoints or
> the Django admin site at `/admin/`.

### 10.2 The Django admin site

`/admin/` is live and covers every model, for users with `is_staff=True`. It is
already configured defensively — several models are **view-only**:

| Model | In Django admin |
| --- | --- |
| `User`, `Property`, `PropertyAddress`, `ServiceType`, `PricingConfig` | editable |
| `ContractorProfile`, `BusinessRegistration`, `InsuranceDocument` | editable |
| `OTPVerification` | **read-only** (and `code_hash` is never displayed) |
| `DispatchOffer`, `Job`, `JobPhoto` | **no add**, largely read-only |
| `Payment`, `Payout` | **no add, no delete** — financial records |

For anything in the "not available" table above, the Django admin is the
supported path today.

### 10.3 Dashboard screens you *can* build now

**Verification queue** — the highest-value screen.
`GET /api/admin/verifications/pending` returns two **separate** arrays
(`business_registrations`, `insurance_documents`); render them as separate
queues, not one merged list. Approve/reject with the two `PATCH` routes.

> **Rejecting requires a `rejection_reason`.** Omitting it returns `400`
> `rejection_reason_required`. Make the reason field mandatory in the UI when
> "Reject" is selected — the applicant reads that text in the app.

This queue is now also the gate for **existing customers joining as
contractors**: they appear here like any other applicant, and approving both of
their documents is what flips their `contractor_status` to `APPROVED` and makes
them eligible for dispatch. Nothing else in the API grants that.

**Service catalog manager** — full CRUD. Two things to surface clearly:

- **`DELETE` is a soft delete.** It returns `200` with the record and
  `is_active: false`. Label the button "Deactivate", not "Delete", and offer
  re-activation via `PATCH {"is_active": true}`.
- **Price edits are never retroactive.** Confirmed bookings keep their frozen
  price. Say so next to the price field, or you will field support tickets.

**Pricing configuration** — a single number, `price_per_km`, for the whole
platform. Not per service. Same non-retroactive warning.

**Contractor directory** — `GET /api/admin/contractors` returns everyone,
available and unavailable. **Read-only by design.** The highest-value column you
can compute: whether `latitude`/`longitude` are null, since **those contractors
can never be dispatched**.

### 10.4 An operational gap worth surfacing

Because payment and payout failures are swallowed (§1), a booking can be
`CONFIRMED` with **no payment record at all**, and a job `COMPLETED` with **no
payout**. Nothing in the API flags this.

Until admin listing endpoints exist, reconciliation means querying the database
or the Django admin: bookings with `status=CONFIRMED` and no related `payment`,
and jobs with `status=COMPLETED` and no related `payout`.

---

## 11. Business rules that will surprise you

1. **No price exists before acceptance.** Not hidden — not calculated. Do not
   build a quote screen.
2. **Contractors accept blind**, then read the payout.
3. **The frozen price never changes.** Catalog edits do not touch existing bookings.
4. **Only the customer can complete a job.** No timeout, no admin override — a
   job can wait forever.
5. **Zero commission.** Payout `amount` equals payment `amount` exactly.
6. **Payouts are per booking, never batched**, and fire automatically.
7. **Soft deletes everywhere.** Properties and services deactivate; rows persist
   because bookings reference them (`PROTECT`).
8. **Eligibility is computed live**, never stored, and nothing re-checks expiry.
9. **A contractor is offered a booking at most once** — DB-enforced.
10. **One account can be both customer and contractor**, and is never offered its own booking. Holding the contractor permission is not the same as being approved for work — see `contractor_status`.
10. **60 minutes** to respond; silence is treated as a decline.
11. **No eligible contractor is not an error** — the booking waits in `PENDING`.
12. **Dispatch is straight-line distance**, not driving distance, and needs
    coordinates on **both** sides — either one missing means no offer, silently.

13. **Business hours 07:00–19:00**, bounds inclusive, in the **property's**
    timezone — one UTC instant can be inside hours in Perth and outside in Sydney.
14. **A booking has a start time only** — no duration, no end time, no recurrence.
15. **Insurance is a reference string**, not a file.
16. **ABN is checked for shape only** — 11 digits, no registry lookup.

---

## 12. Constants and enumerations

| Field | Values |
| --- | --- |
| `User.role` | `CUSTOMER` `CONTRACTOR` `ADMIN` |
| `User.status` | `ACTIVE` `INACTIVE` `SUSPENDED` |
| `Property.property_type` | `HOUSE` `UNIT` `TOWNHOUSE` `APARTMENT` `OTHER` |
| `state` | `NSW` `VIC` `QLD` `WA` `SA` `TAS` `ACT` `NT` |
| `postcode` | exactly 4 digits |
| `abn` | exactly 11 digits |
| `availability_status` | `AVAILABLE` `UNAVAILABLE` |
| Verification `status` | `PENDING` `VERIFIED` `REJECTED` |
| `contractor_status` *(derived)* | `NONE` `PENDING` `ACTION_REQUIRED` `APPROVED` `SUSPENDED` |
| `available_modes` *(derived)* | any of `CUSTOMER` `CONTRACTOR` |
| `Booking.status` | `PENDING` `CONFIRMED` `CANCELLED` *(unreachable)* |
| `DispatchOffer.status` | `PENDING` `ACCEPTED` `DECLINED` `EXPIRED` |
| `Job.status` | `IN_PROGRESS` `AWAITING_CUSTOMER_CONFIRMATION` `COMPLETED` |
| `photo_type` | `BEFORE` `AFTER` |
| `Payment.method` | `CARD` `APPLE_PAY` `GOOGLE_PAY` |
| `Payment.status` / `Payout.status` | `PENDING` `SUCCEEDED` `FAILED` |
| Social `{provider}` | `APPLE` `GOOGLE` (case-insensitive) |

| Constant | Value |
| --- | --- |
| Offer response window | **60 minutes** |
| Business hours | **07:00–19:00** local, inclusive |
| Expiry sweep | every minute (Celery beat) |
| OTP validity | 300 s (5 min) |
| OTP resend cooldown | 60 s |
| OTP max attempts | 5 |
| OTP length | 6 digits |
| Access token | 15 minutes |
| Refresh token | 7 days |
| Commission | **0 %** |
| Money precision | decimal(10,2) |
| Coordinate precision | decimal(9,6) |
| Distance precision | decimal(10,3) |

### Timezone mapping (derived from the property's state)

| State | IANA zone | DST |
| --- | --- | --- |
| NSW, ACT | `Australia/Sydney` | yes |
| VIC | `Australia/Melbourne` | yes |
| TAS | `Australia/Hobart` | yes |
| QLD | `Australia/Brisbane` | **no** |
| SA | `Australia/Adelaide` | yes (+9:30 / +10:30) |
| NT | `Australia/Darwin` | **no** (+9:30) |
| WA | `Australia/Perth` | no (+8:00) |

---

## 13. Deploying and first-run setup

This section exists because the first deployment fails in two predictable ways,
both of which look like application bugs and are not.

### 13.1 Required environment variables

Several settings are **required with no default** — the app refuses to start
without them, deliberately, so that a misconfigured deployment fails loudly
instead of silently running on the wrong settings.

| Variable | Required in | Notes |
| --- | --- | --- |
| `DJANGO_ENV` | **all** | `dev` · `staging` · `production`. No default. |
| `SECRET_KEY` | **all** | long random value |
| `JWT_SIGNING_KEY` | **all** | a **different** random value |
| `DATABASE_URL` | staging, production | **or** the five `DB_*` below — see §13.2 |
| `DB_NAME` | staging, production | not needed if `DATABASE_URL` is set |
| `DB_USER` | staging, production | not needed if `DATABASE_URL` is set |
| `DB_PASSWORD` | staging, production | not needed if `DATABASE_URL` is set |
| `DB_HOST` | optional | defaults to `localhost` |
| `DB_PORT` | optional | defaults to `5432` |
| `ALLOWED_HOSTS` | production | comma-separated; the default is empty, which rejects every request |

Generate the two keys with:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k()); print(k())"
```

> `DJANGO_ENV` has no fallback on purpose. A deployment that quietly loaded dev
> settings would run on **SQLite inside the container** — data would look fine
> and then vanish on the next redeploy.

### 13.2 Two ways to point at the database

`DATABASE_URL` is read first; the discrete `DB_*` variables are the fallback.
Either works, and a `DATABASE_URL` that is empty or whitespace is treated as
absent rather than as broken configuration.

**Preferred — `DATABASE_URL`.** Railway, Heroku and similar platforms inject
this automatically when a database is attached, so usually nothing else is
needed:

```
DATABASE_URL = postgres://USER:PASSWORD@HOST:PORT/NAME
```

Scheme must be `postgres`, `postgresql` or `pgsql`; the port defaults to `5432`
if omitted; and percent-encoded characters in the username or password are
decoded, so a password containing `@` or `:` works.

**Fallback — discrete variables**, exactly as before (`DB_NAME`, `DB_USER`,
`DB_PASSWORD`, `DB_HOST`, `DB_PORT`). Existing deployments keep working
unchanged.

> ### ⚠️ Railway `${{Service.VAR}}` references can resolve to empty strings
>
> On this project they silently did — with both `Postgres` and `postgres`
> casing. The variable exists, but its value is blank, and Django then fails
> in a way that does not mention the variable at all:
>
> ```
> # empty DB_NAME
> ImproperlyConfigured: settings.DATABASES is improperly configured.
> Please supply the NAME or OPTIONS['service'] value.
>
> # empty DB_PASSWORD
> psycopg.OperationalError: connection failed: fe_sendauth: no password supplied
> ```
>
> Each empty variable produces a *different* error, so it looks like several
> unrelated problems. **If a value looks unset, do not debug the reference
> syntax — read the literal value off the database service's own Variables tab
> and paste it in.** Or set `DATABASE_URL` and skip the five references
> entirely.
>
> Diagnose with `env | grep -E "^DB_"`, and never mask the output while
> checking whether a value is empty. For a password, check the length without
> printing it:
> `python -c "import os; print(len(os.environ.get('DB_PASSWORD','')))"`.

**Add a PostgreSQL service first if there isn't one** — otherwise there is no
database at all, and neither method has anything to point at.

### 13.3 First-run checklist, in order

```bash
# 1 — confirm which database you are actually on
python manage.py shell -c "from django.conf import settings; \
print(settings.DATABASES['default']['ENGINE'], settings.DATABASES['default']['NAME'])"

# 2 — create the tables
python manage.py migrate

# 3 — create the baseline services (see §7.2)
python manage.py seed_services

# 4 — verify, and copy the ids for the client
python manage.py list_services
```

**Step 1 must print `django.db.backends.postgresql`.** If it prints `sqlite3`,
stop: `DJANGO_ENV` is not set to `production`, and anything you seed will be
written to a throwaway file inside the container.

**Step 2 is not optional.** Skipping it makes step 3 fail with
`relation "services_servicetype" does not exist`.

### 13.4 Processes

The `Procfile` declares three:

```
web:    gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
worker: celery -A config worker --loglevel=info
beat:   celery -A config beat --loglevel=info
```

`web` alone serves the API. **`beat` is what expires dispatch offers** — without
it, an unanswered offer never moves to `EXPIRED` and the booking never cascades
to the next contractor. `worker` executes the tasks `beat` schedules. Both also
need `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` pointing at Redis.

### 13.5 Two failures that are not bugs

| Symptom | Cause |
| --- | --- |
| `Unknown command: 'seed_services'` | the container is running an older deploy — redeploy the current commit |
| `ImproperlyConfigured: settings.DATABASES …` | `DB_NAME` unset **or empty** — see §13.2 |
| `fe_sendauth: no password supplied` | `DB_PASSWORD` empty. The host resolved and the server answered, so only the credential is missing |
| `relation "…" does not exist` | `migrate` has not been run on this database |
| `GET /api/services` returns `[]` | `seed_services` has not been run — see §7.2 |

None is an application defect; all are configuration or a missing first-run
step.

---

## Importing the API into Postman or Insomnia

```
https://cleaninghouse-production.up.railway.app/api/openapi.json
```

**Postman:** *Import → Link* → paste → *Continue → Import*.
**Insomnia:** *Create → URL* → paste.
Swagger UI is also served at `/api/docs`.

Set collection-level auth to *Bearer Token* = `{{token}}`, and capture it
automatically in the *Tests* tab of the verify request:

```js
if (pm.response.code === 200) {
    pm.environment.set("token", pm.response.json().tokens.access);
}
```

Set the four public endpoints to *No Auth* so the inherited header is not sent.
Remember the token expires after 15 minutes, and that against **production the
login endpoints currently fail** (§1).
