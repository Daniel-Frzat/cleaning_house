# On-Demand Pricing & Payment Refactor — change record

**Branch:** `refactor/on-demand-pricing-payment` · **Base:** `main` @ `fba2868`
**Status: INCOMPLETE — 75 tests failing. Do not merge.**

This documents what changed on the branch, what the changes mean, and what is
still missing. It is a working record, not a finished feature's documentation;
when the refactor lands, its content moves into
[PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md), [API_INTEGRATION_GUIDE.md](API_INTEGRATION_GUIDE.md)
and [DEVELOPER_HANDBOOK.md](DEVELOPER_HANDBOOK.md), and this file is deleted.

---

## Contents

1. [Why](#1-why)
2. [Test status](#2-test-status)
3. [The central reversal](#3-the-central-reversal-accept-no-longer-assigns)
4. [New and changed models](#4-new-and-changed-models)
5. [Migrations](#5-migrations)
6. [New services](#6-new-services)
7. [Settings](#7-settings)
8. [State machines](#8-state-machines)
9. [What is not done](#9-what-is-not-done)
10. [Decisions taken](#10-decisions-taken)
11. [Still open](#11-still-open)

---

## 1. Why

Five product requirements drove this, and each conflicted with something the
backend did:

| Requirement | What conflicted |
|---|---|
| Show "Up to A$152" before searching | No price existed before contractor acceptance |
| Cleaner sees exact earnings before accepting | `OfferOut` deliberately had no price; pricing ran *after* acceptance |
| No cleaner assigned before payment succeeds | Acceptance confirmed the booking, then charged; `CONFIRMED` + `FAILED` was reachable |
| Dispatch by where the cleaner *is* | Dispatch ranked by the contractor's fixed **business address** |
| Street route and ETA | Only straight-line haversine existed |

---

## 2. Test status

```
851 passed · 75 failed        (main: 925 passed, 0 failed)
```

| File | Failures |
|---|---|
| `test_bookings_dispatch.py` | 25 |
| `test_payments.py` | 24 |
| `test_bookings_access_notes.py` | 14 |
| `test_bookings_dispatch_progress.py` | 6 |
| `test_properties_gps.py` | 2 |
| `test_bookings_self_assignment.py` | 2 |
| `test_jobs.py` · `test_bookings_reschedule.py` | 1 each |

**Most of these are not incidental.** They encode the old invariant — *accepting
an offer assigns a contractor* — which §12 deliberately reverses. Each one needs
a judgement about whether it still asserts a rule that holds, not a mechanical
fix. That is the bulk of the remaining work, and it is why the branch is not
merged.

Verified green regardless: `manage.py check`, all five migrations apply to both
an existing and a fresh database.

---

## 3. The central reversal: accept no longer assigns

Everything else follows from this.

**Before** — the booking was confirmed, then charged, and the charge could fail
without undoing anything:

```
accept → booking CONFIRMED + contractor assigned + Job created
       → (after commit) charge attempted
       → on failure: booking stays CONFIRMED, payment FAILED
```

**After** — acceptance only *reserves*; payment confirmation assigns:

```
accept → offer ACCEPTED_PENDING_PAYMENT     (booking reserved, NOT confirmed)
       → Payment PROCESSING
       → charge attempted
          ├─ FAILED          → nothing assigned, no Job, retry available
          ├─ REQUIRES_ACTION → customer completes 3-D Secure, then confirm
          └─ SUCCEEDED       → confirm_payment():
                                 Payment  → SUCCEEDED (paid_at)
                                 Offer    → ACCEPTED
                                 Booking  → CONFIRMED + assigned + price
                                 Job      → ASSIGNED
```

`confirm_payment()` is **idempotent**: it takes `select_for_update()` on both
payment and booking and returns immediately if the booking is already
`CONFIRMED`, so a replayed webhook creates no second job and no second
assignment.

**Consequence for tracking:** live tracking opens only on `Job.ASSIGNED`, and a
job now exists only after payment succeeds. The requirement "no tracking before
payment" therefore holds through the existing window, with no new gate.

---

## 4. New and changed models

### `services.PricingConfig` — extended

Still a singleton. New fields, all dashboard-controlled:

| Field | Default | Meaning |
|---|---|---|
| `included_distance_km` | `0` | Free distance; only the excess is charged |
| `maximum_travel_fee` | `9999.99` | Cap on the travel component |
| `currency` | `AUD` | ISO 4217 |
| `rounding_rule` | `NEAREST_CENT` | `NEAREST_CENT` · `NEAREST_5C` · `NEAREST_10C` · `NEAREST_DOLLAR` |
| `pricing_version` | `1` | Auto-increments on change; frozen onto offers |
| `active_from` | `now` | When this configuration took effect |
| `dispatch_offer_ttl_seconds` | `3600` | Offer answerable window (was hard-coded 60 min) |

> **Defaults are chosen so an upgrade changes no existing price.** Zero included
> distance means every kilometre is charged, exactly as before; the maximum fee
> is high enough never to bind; `NEAREST_CENT` is the existing `quantize(0.01)`.

`pricing_version` **increments automatically** and only when a pricing field
actually changes — a version that relies on an admin remembering to bump it
becomes a silent lie at the first lapse, and frozen snapshots would then point
at rules they were not computed under. `dispatch_offer_ttl_seconds` is
deliberately *excluded* from the bump: a TTL is a dispatch policy, not a price.

### `bookings.BookingQuote` — new

The immutable snapshot the customer approves before any cleaner is found.

| Field | Notes |
|---|---|
| `service_snapshot` | JSON: ids, **display names**, room counts, prices at quote time |
| `services_total` | Frozen; never recomputed from the catalog |
| `maximum_total` | `services_total + maximum_travel_fee` — the approved ceiling |
| `pricing_version`, `currency`, `expires_at` | 30-minute TTL |

**The ceiling cannot be exceeded.** The travel component is capped by
`maximum_travel_fee`, so the final total is mathematically ≤ `maximum_total`
however far the cleaner is. That is what makes "will not exceed this amount" a
guarantee rather than an estimate. It is enforced twice more: dispatch refuses
to create an over-ceiling offer, and `Payment.clean()` refuses the charge.

Single-use: a quote already attached to a booking cannot be reused, or an old
price could be pinned indefinitely.

### `bookings.Booking` — new fields

`requested_at` (on-demand request moment, distinct from `created_at`) ·
`quote` · `max_total` · `pricing_version` · `currency` ·
`payment_method_reference` (opaque provider token — **never card data**) ·
`dispatch_round`.

### `bookings.DispatchOffer` — frozen pricing

New: `total_amount` · `contractor_earnings` · `travel_fee` · `services_total` ·
`currency` · `pricing_version` · `distance_source` · `eta_seconds` ·
`dispatch_round`.

New status `ACCEPTED_PENDING_PAYMENT` (field widened 16 → 32 chars).

All pricing is frozen **at offer creation**, so the cleaner sees exact earnings
before pressing Accept instead of accepting blind. If the contractor moves or an
admin changes prices between offer and acceptance, what they saw is what is
charged and paid.

`contractor_earnings == total_amount` — zero commission, kept as its own field
because it means something different and because any future commission must be
changed here explicitly rather than hidden in a calculation.

**Unique constraint changed:** `(booking, contractor)` → `(booking, contractor,
dispatch_round)`. Previously a contractor offered a booking once was excluded
forever, which made any retry a no-op that fell straight back to
`NO_CONTRACTOR`. Rounds scope the exclusion while keeping the full history of
who declined and when.

### `contractors.ContractorCurrentLocation` — new

The **third** location concept. They are deliberately separate:

| Concept | Question it answers | Used by |
|---|---|---|
| `ContractorProfile.latitude/longitude` | Where is their business? | profile data |
| `ContractorCurrentLocation` | Where are they **now**? | dispatch |
| `jobs.JobLocation` | Where are they on the way to *this* job? | customer tracking |

Freshness window **300 s**, judged on the server's `received_at`, never the
device's `recorded_at`. A stale location is treated exactly like a missing one —
a contractor whose last fix is an hour old gets no offer, because the price would
be computed from a distance that may be entirely wrong. Never registered in the
Django admin; never exposed to customers or other contractors.

### `payments.Payment` — lifecycle

Status now: `NOT_CHARGED` · `PENDING` *(legacy)* · `PROCESSING` ·
`REQUIRES_ACTION` · `SUCCEEDED` · `FAILED` · `REFUNDED`.

New fields: `attempt_number` · `provider_error_code` (admin-only) ·
`method_summary` (safe display JSON) · `action_payload` (3-D Secure) · `paid_at`.

`REFUNDED` **has no code path**, and a test asserts it stays that way — a refund
is a real provider operation whose policy is unsettled, and the enum value must
not be used to colour a state that never refunded anything. `PENDING` is kept
only so existing rows remain valid.

`clean()` now validates against the **frozen accepted-offer total**, because the
charge happens before `computed_price` is written, plus a hard ceiling check
against `booking.max_total`.

---

## 5. Migrations

| App | Migration | Contents |
|---|---|---|
| `services` | `0002_pricingconfig_…` | 7 travel-pricing fields |
| `contractors` | `0003_contractorcurrentlocation` | New model |
| `payments` | `0002_payment_action_payload_…` | 5 fields + status enum |
| `bookings` | `0009_booking_currency_…` | Booking + offer fields, `BookingQuote` |
| `bookings` | `0010_remove_dispatchoffer_unique…` | `dispatch_round` + constraint swap |

All additive with safe defaults; no data is destroyed and no existing booking
changes value. Verified against both the existing dev database and a fresh one.

**Rollback limitation:** reversing `0010` restores the `(booking, contractor)`
constraint, which will fail if any booking has reached round 2 with a repeated
contractor. Reverse only before any retry has run.

---

## 6. New services

| Module | Purpose |
|---|---|
| `apps/services/services/travel_pricing.py` | The §7 formula, rounding, active config |
| `apps/bookings/services/quotes.py` | Create, validate and consume quotes |
| `apps/contractors/services/location.py` | Report and read current phone GPS |
| `adapters/directions/` | Directions contract + dev fake |

### The pricing formula

```
chargeable      = max(0, distance_km − included_distance_km)
uncapped_travel = chargeable × price_per_km
travel_fee      = min(maximum_travel_fee, uncapped_travel)
final_total     = round_once(services_total + travel_fee)
```

`Decimal` throughout; `float` is rejected outright, as in the existing
`calculate_price`. Rounding is applied **once**, to the final total — rounding
components and summing them introduces drift and makes the total disagree with
its parts.

`services_total` comes from the **frozen quote**, not the live catalog, so an
admin price change after the customer approved does not alter their booking.

### Directions and the haversine fallback

The adapter contract returns route distance, duration and polyline. With no
provider configured, behaviour is governed by
`DISPATCH_ALLOW_HAVERSINE_FALLBACK`:

- **False (production default)** — no offer is created. Loud failure, by design.
- **True (dev/test)** — straight-line distance is used, and the offer records
  `distance_source = HAVERSINE`, so the substitution is visible in admin audit
  rather than silent.

**An ETA is never derived from haversine.** `eta_seconds` stays `null`: a
straight-line distance is not a travel time, and presenting one as an arrival
estimate would be a confident lie to the customer.

---

## 7. Settings

| Variable | Default | Purpose |
|---|---|---|
| `DIRECTIONS_PROVIDER_ADAPTER_CLASS` | fake | Directions provider dotted path |
| `DIRECTIONS_ALLOW_FAKE_ADAPTER` | `False` | Fuse — the fake derives ETA from an assumed speed |
| `DISPATCH_ALLOW_HAVERSINE_FALLBACK` | `False` (`True` in dev) | Explicit permission to fall back |

Both fakes refuse to construct when `DEBUG=False` unless their fuse is set —
the same pattern as the payment, payout, storage, SMS and social fakes.

---

## 8. State machines

**DispatchOffer**

```
PENDING ──accept──▶ ACCEPTED_PENDING_PAYMENT ──payment SUCCEEDED──▶ ACCEPTED
   │                          │
   │                          └── payment FAILED → stays pending-payment,
   │                              retryable; booking not assigned
   ├──decline──▶ DECLINED ──▶ cascade to next contractor
   └──ttl──────▶ EXPIRED  ──▶ cascade to next contractor
```

**Payment**

```
NOT_CHARGED ──accept──▶ PROCESSING ──┬──▶ SUCCEEDED ──▶ (assignment)
                            ▲        ├──▶ FAILED ──────┐
                            │        └──▶ REQUIRES_ACTION ─┐
                            └──────────── retry ───────────┘
REFUNDED — defined, unreachable
```

**Booking** is unchanged (`PENDING` / `CONFIRMED` / `CANCELLED`) but now reaches
`CONFIRMED` only through `confirm_payment()`.

---

## 9. What is not done

- **75 failing tests** (§2) — the largest remaining item.
- **No endpoints wired.** Services exist; routers do not. Missing: quote,
  retry-dispatch, payment retry, payment confirm, contractor location,
  route/ETA in tracking.
- **On-demand booking (§3)** — `scheduled_at` is still required by
  `BookingIn`; the reschedule endpoint is still mounted.
- **Schema separation (§22)** — customer/contractor/admin shapes not yet split
  for the new fields.
- **Payment-method setup (§18)** — contract stubs only; deliberately not
  exposed as endpoints (see §10).
- **No new tests, no OpenAPI regeneration, no doc updates** beyond this file.

---

## 10. Decisions taken

**Internal confirm endpoint, not a public webhook.** With no PSP there is no
signature to verify, so a public webhook URL would let anyone forge a payment
success and get a cleaner assigned without paying. `confirm_payment()` is the
single transition function; adding a real provider means adding a signature-
verifying route that calls it — not a redesign.

**§18 payment-method setup is contract-only.** Building
`POST /api/payments/setup` against the fake would return invented tokens that
the app would treat as real. The spec itself says not to claim Apple/Google Pay
support before a provider is configured. The adapter methods exist and raise
`NotImplementedError`; the endpoints are withheld.

**Rounds instead of deleting offers.** Retry could have deleted prior offers —
simpler, but it destroys the record of who declined and when, which contractor
performance reporting will need.

**New `Decimal` fields, not a cents migration.** The spec asked for integer
minor units; converting every existing money column touches recorded money for
no behavioural gain here. The formula is exact in `Decimal` and `float` remains
banned. **This is a deliberate deviation** — flagging it rather than leaving it
to be discovered.

---

## 11. Still open

Unchanged by this branch, and not to be closed by inference:

- What happens when nobody accepts (no auto-cancel, no retry — the customer may
  retry manually).
- Cancellation and refund policy — still no `CANCELLED` job state.
- What happens after the payment-retry window elapses: the state is exposed, the
  booking is **not** silently cancelled.
- Payout failure handling.
- Automatic completion, ratings, GST, commission, re-clean guarantee.

Provider dependencies still blocking production: **payment, payout, SMS/OTP,
social login, photo storage, and now directions.**

---

*Written 2026-09-17 against branch `refactor/on-demand-pricing-payment` @ `7662519`.*
