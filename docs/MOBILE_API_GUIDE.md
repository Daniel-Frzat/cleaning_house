# Cleano mobile app — backend API guide

**For:** the mobile (Flutter) developer, customer app and cleaner app.
**Date:** 2026-09-26.
**Replaces:** every earlier backend reply, including `BACKEND_RESPONSE_TO_APP_PROGRAMMER.md` (2026-09-24).

Part 1 answers your gap report (`FIGMA_VS_API_GAP_REPORT`, section "2026-09-26 — what still needs the backend", items B1–B11 and C1–C40).
Parts 2–4 are the working guide.
Part 5 is the full endpoint and schema reference, generated from `openapi.json`.

Source of truth, in this order:

1. `openapi.json` in the repo (also served live at `/api/openapi.json`, interactive at `/api/docs`).
2. `ERROR_CODES.md`, which lists every error `code`. It is generated from the source, and a test fails if it is out of date.
3. This document.

> **Deployment note.** Production is `https://cleaninghouse-production.up.railway.app`.
> Before wiring a new endpoint, check it is present in the live `/api/openapi.json`.
> The last time we checked, production was one deploy behind: `/arrive`, `/cancel`, `PUT /contractor/location` and the `validation_error` 422 shape were not live yet.
> We will tell you when the deploy is confirmed.

---

## Part 1 — Reply to your gap report

### 1.1 The B-items (2026-09-26)

| # | Your request | Status | What changed / what to do |
| --- | --- | --- | --- |
| **B1** 🔴 | No booking can be created: `scheduled_at` required, 2 h lead time | ✅ **Done.** Product decision: **on-demand and scheduled both exist.** | **On-demand:** omit `scheduled_at` (or send `null`) in `POST /api/bookings`. No lead time applies. The request must still be made between **07:00 and 19:00 property-local time**; otherwise the API returns `400 outside_business_hours`. **Scheduled ahead:** send `scheduled_at`, which must be at least 2 h ahead and inside 07:00–19:00 local. `BookingOut` now has `is_on_demand` and `requested_at`. Stop sending "now + 2 min"; that is rejected as `scheduled_at_too_soon`. |
| **B2** 🔴 | Real payments, payment-method reference, 3-D Secure | ⏳ **Open. Waiting on the payment-provider contract** (Stripe is the candidate). | The retry and confirm-action endpoints already exist (§3.6). With the current test adapter every charge succeeds. `confirm-action` returns `501 payment_action_not_supported` until a real provider is plugged in. Keep the payment-method UI local for now. |
| **B3** 🔴 | Production providers | 🟡 **Partly done.** | **Live:** FCM push (Firebase project `cleano-677af`). **Test mode:** SMS, which uses time-limited test phone numbers (§2.1); the PO gives you the numbers. **Fake, enabled for the test phase only:** payments, payouts, photo storage and directions. Social login is not configured; hide Apple/Google sign-in until we tell you. |
| **B4** 🟠 | Payout account (BSB/account) | ⏳ **Open. Depends on B2.** | With Stripe Connect, onboarding will be a provider link, not raw BSB fields. **Do not send bank details to the backend.** Keep the W11 screen in its "coming soon" state. |
| **B5** 🟠 | Document upload | ⏳ **Open.** | For now keep the ABN, policy reference and expiry fields. File upload needs real storage, which is part of B3. |
| **B6** 🟠 | No "arrived" step | ✅ **Done.** New job status **`ARRIVED`**. | Lifecycle is now `ASSIGNED → ARRIVED → IN_PROGRESS → AWAITING_CUSTOMER_CONFIRMATION → COMPLETED`. Call `POST /api/contractor/jobs/{id}/arrive` with the device's `latitude`, `longitude` and `accuracy`. The server accepts it within **300 m** of the property plus up to 100 m of reported accuracy; otherwise it returns `409 not_at_property`. `/start` now requires `ARRIVED` (`409 invalid_job_status` otherwise). Arrival sets `arrived_at`, sends `job.arrived` to the customer, and closes live tracking. **Remove the 200 m device-side guess.** The server now owns this rule. |
| **B7** 🟠 | Cancel | ✅ **Done.** Product decision: **free cancellation only before payment.** | `POST /api/bookings/{id}/cancel` with body `{"reason": "optional text"}`. It is allowed only while the booking is `PENDING` and nothing is charged (no payment, or payment `FAILED`/`NOT_CHARGED`). **Otherwise:** `409 booking_not_cancellable` (already cancelled or confirmed), or `409 cancellation_requires_support` (a payment is in progress or succeeded; open a support request instead). On success the booking is `CANCELLED` with `cancelled_at` and `cancellation_reason`. A cleaner holding an offer receives `offer.cancelled`, which is the W04 "job cancelled" overlay. Contractors cannot cancel. Refund-based cancellation waits for B2. |
| **B8** 🟡 | Rating, report an issue, invoice, re-clean | ⏳ **Not scheduled.** | Keep the feature flags off. "Report an issue": use `POST /api/support-requests` with `category: "BOOKING_ISSUE"` and `booking_id`. |
| **B9** 🟡 | 422 has no `code` | ✅ **Done.** | Every schema error is now `422 {"code": "validation_error", "detail": "...", "errors": [{"field", "loc", "message", "type"}]}`. See §4.1. |
| **B10** 🟡 | Stable codes, published list | ✅ **Done.** | `ERROR_CODES.md` in the repo lists all 136 codes with their HTTP status and meaning. A test fails if a code is added or renamed without regenerating the file, so every change shows up in review. **Codes are stable.** |
| **B11** 🟡 | `room_count` for add-ons | ✅ **Answered.** | Price = `room_price × room_count + base_price`. Add-ons have `room_price = 0`, so `0` and `1` give the same price. Sending `1` is fine. The rule is also in the schema description. |

### 1.2 The C-items that are still listed as open

Your C0 table ("claimed but not in the deployed API") was right on 2026-09-24: production was behind.
Everything below is in the code on `main`. Check the live `/api/openapi.json` before wiring.

| # | Status | Answer |
| --- | --- | --- |
| C1 pre-request price | ✅ | `POST /api/quotes` returns `QuoteOut` with **`maximum_total`** (the "Up to A$…" figure), `services_total`, `currency` and `expires_at`. Pass the `quote_id` into `POST /api/bookings`. The final price is frozen on the offer and never exceeds the quote. |
| C2 on-demand | ✅ | See B1. |
| C3 payment enum | ✅ | `NOT_CHARGED, PENDING, PROCESSING, REQUIRES_ACTION, SUCCEEDED, FAILED, REFUNDED`. Retryable: `FAILED`, `REQUIRES_ACTION`. |
| C4, C6, C7 payment method, retry, 3DS | 🟡 | `POST /api/bookings/payments/{id}/retry` exists (query `payment_method_reference`, optional). `confirm-action` exists but returns 501 until a real provider is chosen. B2 is still open. |
| C5, C11, C12 invoice, rating, issue | ⏳ | See B8. |
| C8 cancel | ✅ | See B7. |
| C9 customer ETA / route | ⏳ | Not yet. `eta_seconds` exists only on the cleaner's offer, and it needs a directions provider (currently fake or straight-line). The customer tracking map still shows two points. |
| C10 cleaner name for the customer | ⏳ | Not exposed. `assigned_contractor_id` only. Keep it hidden. |
| C13 support replies and attachments | ⏳ | One-way. Admins change the status (`SUBMITTED → UNDER_REVIEW → RESOLVED`) and the user receives `support.updated`. No attachments. |
| C14 add-on flag | ⏳ | Not in `ServicePublicOut`. Keep your rule (first selection = main clean). |
| C15 | ✅ | See B11. |
| C16 lifecycle doc | ✅ | Fixed. The lifecycle is in §3.7. |
| C17 push deep link | ✅ | Push is live. Every push carries `type`, `audience` and `notification_id`, plus ids such as `booking_id`, `job_id`, `offer_id`, `payment_id` and `payout_id`. See §3.10. |
| C18 production | 🟡 | See B3. |
| C19, C21 arrived | ✅ | See B6. |
| C20, C40 enums | ✅ | `contractor_status`: `NONE, PENDING, ACTION_REQUIRED, APPROVED, SUSPENDED`. **Use it as the source** for the application and approval screen. `available_modes`: `CUSTOMER, CONTRACTOR`. |
| C22 receive an offer | ✅ | Push `offer.new` (data `offer_id`, `booking_id`, HIGH priority, Android channel `offers`). Also poll `GET /api/contractor/offers` (open offers; `?include_closed=true` for history) to recover offers after a missed push or a reinstall. |
| C23 offer content | ✅ | `GET /api/contractor/offers/{id}` returns `OfferDetailOut`. It includes `contractor_earnings`, `total_amount`, `currency`, `eta_seconds`, `service_summary`, `services[]`, `suburb`, `state`, `postcode`, `property_type`, `is_on_demand`, `scheduled_at_local`. **Earnings are frozen at offer time**, so show them before accepting. |
| C24, C25, C31 contractor job list and summary | ✅ | `GET /api/contractor/jobs?status=` returns `ContractorJobOut` with `public_reference`, `service_summary`, `property_summary`, `scheduled_at_local`, `earnings`, `payout_status`, `access_notes`, `photos` and all timestamps. Stop storing job ids on the device. |
| C26, C29, C33, C34 earnings | ✅ | `GET /api/contractor/earnings?from_date=&to_date=` returns `EarningsOut`: `total`, `paid_total`, `processing_total`, `completed_jobs`, and `items[]` with `booking_id`, `payout_id`, `public_reference`, `service_summary`, `amount`, `status`. `booking_id` opens W08. |
| C27 payout enum | ✅ | `PENDING, SUCCEEDED, FAILED`. |
| C28, C38 become a cleaner | ✅ | A customer calls `POST /api/contractor/profile`; creating the profile grants contractor capability on the same account. Then submit ABN and insurance. `contractor_status` becomes `PENDING` and then `APPROVED` after admin review. Refresh `GET /api/auth/me` to pick up `roles` and `available_modes`. Only approved (verified) cleaners receive offers. |
| C30, C39 payout account | ⏳ | See B4. |
| C32, C37 documents | 🟡 | Status read works. Re-submit = a new `POST` of the same document. File upload: see B5. |
| C35 background location | Decision for you and product | The server needs **two** location feeds (§3.8): the **online feed** (`PUT /api/contractor/location`) while the cleaner is online, which dispatch needs, and the **job feed** (`POST /api/contractor/jobs/{id}/location`) while a job is `ASSIGNED`. If the app sends neither while backgrounded, the cleaner stops receiving offers after 5 minutes. |
| C36 offer countdown | ✅ | `expires_at` is on every offer (list, detail). The push carries only `offer_id` and `booking_id`, so fetch the detail on open. |
| D3 "customer never confirms" | ⏳ | No auto-confirm yet. This is a product rule still to decide. |

---

## Part 2 — Basics

### 2.1 Authentication (customers and cleaners)

Phone OTP is the only login in production today.

1. `POST /api/auth/otp/request` with `{"phone": "+614XXXXXXXX"}` returns `{detail, expires_in_seconds}`.
   - The code is 6 digits and valid for 5 minutes.
   - Resend cooldown is 60 s (`429` with `retry_after_seconds`).
   - Daily and per-IP limits apply.
2. `POST /api/auth/otp/verify` with `{"phone", "code"}` returns `AuthOut {tokens: {access, refresh}, user: UserOut}`. It creates the account on first login.
3. Send `Authorization: Bearer <access>` on every call.

**Tokens**

| Token | Lifetime | Notes |
| --- | --- | --- |
| access | 15 min | Send as the Bearer token. |
| refresh | 7 days | Rotated on every use. |

- **Refresh:** `POST /api/auth/token/refresh` with `{"refresh"}` returns a **new pair**. The old refresh token is blacklisted immediately, so always store the new one. A second use of the old token returns `401`.
- **On any `401`:** try one refresh. If that also fails, send the user to login.
- **Logout:** `POST /api/auth/logout` with `{"refresh", "device_token"}` returns `204`. It blacklists the refresh token and unregisters that FCM token, so the device stops receiving push.

**Test phase.** No SMS provider is contracted yet.

- The backend has a small list of **test phone numbers**, each with a fixed code. The PO shares them.
- They stop working automatically on a set date.
- Any other number receives no SMS.
- Do not hard-code these numbers in the app.

**Profile**

- `GET /api/auth/me` returns `UserOut`: `roles[]`, `available_modes[]`, `contractor_status`, `full_name`, `email`, `phone`, `status`.
- Call it on app start.
- `PATCH /api/auth/me` changes `full_name` and `email`.
- The phone number cannot be changed.

**Roles**

- One account can be `CUSTOMER` and `CONTRACTOR` at the same time. Use `roles`; the singular `role` is legacy.
- `ADMIN` accounts are exclusive and cannot use the app.

### 2.2 Conventions

- **Ids** are UUID strings. A malformed id in the path returns `422 validation_error`, not a 404.
- **Coordinates:** send raw GPS values as they come. The server rounds `latitude`/`longitude` to 6 decimal places and `accuracy` to 2 before validating. Only impossible values (latitude outside ±90, text, NaN) are rejected.
- **Money** is a decimal **string or number**, for example `"125.00"`. Parse it as a decimal, never as a float. Currency is `AUD`.
- **Times** are ISO-8601 UTC (`...Z` / `+00:00`).
  - `scheduled_at_local` is the same instant in the property's timezone (`customer_timezone`, for example `Australia/Sydney`). Use it for display.
  - Send `recorded_at` as the device time in UTC. Up to 60 s of clock skew is tolerated; later timestamps are rejected.
- **Lists:**
  - `GET /api/bookings` and `GET /api/notifications` take `limit` and `offset`.
  - Other lists return everything that belongs to the caller.
- **Mobile endpoints never return admin fields.** Everything under `/api/admin/*` is refused for app users.

---

## Part 3 — Flows

### 3.1 Properties

- `POST /api/properties` creates a property: label, type, and address with suburb, state and postcode.
  - Postcode and state are cross-checked (`422` on mismatch).
  - GPS coordinates are required for dispatch.
- `PropertyOut.serviceability_warning` is set when the address is outside the served area. Show it; it does not block the request.
- **Address lock (PO decision):** the address cannot be edited while the property has an active booking (`409 property_has_active_bookings`). Label and type can always be edited.
- `DELETE` deactivates the property. It is also blocked while the property has an active booking.

### 3.2 Services and quote

- `GET /api/services` returns the active catalog (`id`, `name`, `description`).
- `POST /api/quotes` with `{property_id, service_selections: [{service_type_id, room_count}]}` returns `QuoteOut`.
  - Show `maximum_total` as "Up to A$…".
  - The quote has an `expires_at`. Request a new quote if it has expired.

### 3.3 Request a cleaner (booking)

`POST /api/bookings`:

```json
{
  "property_id": "…",
  "service_selections": [{"service_type_id": "…", "room_count": 1}],
  "quote_id": "…",
  "access_notes": "Key under the mat",
  "scheduled_at": null
}
```

- `scheduled_at: null` (or omitted) means **on-demand, now**. It must be made 07:00–19:00 property-local; otherwise `400 outside_business_hours`.
- `scheduled_at: "<ISO>"` means **scheduled**. It must be at least 2 h ahead and inside 07:00–19:00 local.
- Returns `201 BookingOut`: `status: PENDING`, `dispatch_status: SEARCHING`, `public_reference` (show this as the booking number).

**Dispatch status** is on `BookingOut.dispatch_status`. Poll `GET /api/bookings/{id}` every 10–15 s on the searching screen, and also react to push.

| `dispatch_status` | Screen |
| --- | --- |
| `SEARCHING` | "Finding your cleaner…" |
| `NO_CONTRACTOR` | "No cleaner available right now". No push is sent for this; you learn it by polling. "Try again" means `POST /api/bookings/{id}/reschedule` with `{}` (re-requests now). |
| `ASSIGNED` | A cleaner accepted and the payment succeeded. The booking is `CONFIRMED`. |

**Reschedule:** `POST /api/bookings/{id}/reschedule` with `{"scheduled_at": "<ISO>"}`, or with `{}` or `null` to re-request now. It is allowed only while the booking is `PENDING`.

**Cancel:** see B7.

### 3.4 What happens after a cleaner accepts

1. The offer becomes `ACCEPTED_PENDING_PAYMENT` and the backend charges the customer (the `payment` object on the booking).
2. **On payment `SUCCEEDED`:**
   - the booking becomes `CONFIRMED` and `dispatch_status` becomes `ASSIGNED`;
   - a job is created with status `ASSIGNED`;
   - the customer receives `booking.confirmed` ("Cleaner confirmed");
   - the cleaner receives `job.confirmed`.
3. **On payment `FAILED`:** the customer receives `payment.failed` (HIGH priority). Offer "Try again" via retry.
4. **On `REQUIRES_ACTION`:** the customer receives `payment.action_required`. This is for 3-D Secure, which is not live yet (B2).

The cleaner must not travel before `job.confirmed`. An offer stuck at `ACCEPTED_PENDING_PAYMENT` means the payment is not done.

### 3.5 Booking screens

| Call | Returns |
| --- | --- |
| `GET /api/bookings` | The customer's bookings, newest first. |
| `GET /api/bookings/{id}` | `BookingOut`, including the inline `payment` summary. |
| `GET /api/bookings/{id}/job` | `JobOut`: status, `arrived_at`, `started_at`, `marked_done_at`, `confirmed_at`, `photos`. Returns 404 until a job exists. |
| `GET /api/bookings/{id}/tracking` | `JobTrackingOut`. |

Stepper mapping:

| Stepper | Condition |
| --- | --- |
| Booked | `BookingOut.status = PENDING` |
| Assigned | job `ASSIGNED` |
| Arrived | job `ARRIVED` |
| Cleaning | job `IN_PROGRESS` |
| Check photos | job `AWAITING_CUSTOMER_CONFIRMATION` |
| Complete | job `COMPLETED` |

### 3.6 Payments (customer)

- `GET /api/bookings/{id}/payment` returns the full payment.
- `POST /api/bookings/payments/{payment_id}/retry?payment_method_reference=` works only when the payment is `FAILED` or `REQUIRES_ACTION` (`409` otherwise).
- `POST /api/bookings/payments/{payment_id}/confirm-action` returns `501` until a real provider exists (B2).

### 3.7 Job lifecycle (cleaner)

| Step | Call | Allowed when | Customer receives |
| --- | --- | --- | --- |
| Arrive | `POST /api/contractor/jobs/{id}/arrive` `{latitude, longitude, accuracy}` | `ASSIGNED`, payment succeeded, within 300 m (+ accuracy up to 100 m) | `job.arrived` |
| Start | `POST /api/contractor/jobs/{id}/start` | `ARRIVED` | `job.started` |
| Photos | `POST /api/contractor/jobs/{id}/photos?photo_type=BEFORE\|AFTER`, multipart field `file` | `IN_PROGRESS` only | — |
| Mark done | `POST /api/contractor/jobs/{id}/mark-done` | `IN_PROGRESS` with at least one BEFORE and one AFTER photo | `job.awaiting_confirmation` |
| Customer confirms | `POST /api/bookings/{booking_id}/job/confirm` (customer) | `AWAITING_CUSTOMER_CONFIRMATION` | The cleaner receives `job.completed`, then `payout.sent`. |

**Photo rules**

- JPEG, PNG or HEIC. The server checks the real file content, not the file extension.
- 10 MB maximum per photo.
- 30 photos maximum per job.
- The photo counter can read "@count added"; more than one photo of each type is allowed.

**Errors to handle:** `not_at_property`, `invalid_job_status`, `payment_not_settled` (payment gate at arrive and start), `job_not_accepting_photos`, `missing_proof_photos`, `photo_too_large`, `too_many_photos`, `unsupported_photo_format`.

### 3.8 Location — two separate feeds

| Feed | Call | When | Why |
| --- | --- | --- | --- |
| **Online** | `PUT /api/contractor/location` `{latitude, longitude, accuracy, recorded_at}` | Every **1–2 min** while `availability_status = AVAILABLE` | Dispatch uses only a location reported in the **last 5 minutes**. Without it the cleaner gets **no offers**. |
| **Job** | `POST /api/contractor/jobs/{id}/location` (same body) | Every ~10 s while the job is `ASSIGNED` (on the way) | Feeds the customer's map. It is refused after arrival. |

`GET /api/contractor/location` shows what the server has, including `is_fresh` and `fresh_for_seconds`. Useful for a "you are visible to customers" indicator. Returns `204` if nothing has been reported.

**Customer map:** `GET /api/bookings/{id}/tracking` every 10–15 s.

- `tracking_active` is true only while the job is `ASSIGNED`.
- `contractor_location.is_stale` is true when the last fix is older than 90 s.
- `property_location` is always present.

### 3.9 Cleaner: profile, verification, offers, jobs, earnings

**Onboarding**

1. `POST /api/contractor/profile` (business name, work address).
2. `POST /api/contractor/business-registration` (ABN, 11 digits, and business name).
3. `POST /api/contractor/insurance` (policy reference and expiry).
4. Poll `GET /api/auth/me` for `contractor_status`, or wait for the `verification.approved` / `verification.rejected` push (data `document_id`, `document_type`).

`GET /api/contractor/business-registration` and `/insurance` return the latest submission, with `status` and `rejection_reason`.

**Go online / offline:** `PATCH /api/contractor/profile/availability` with `{"availability_status": "AVAILABLE" | "UNAVAILABLE"}`. Start the online location feed at the same time.

**Offers**

- **List:** `GET /api/contractor/offers` returns open offers; `?include_closed=true` includes history.
- **Detail:** `GET /api/contractor/offers/{id}`.
- **Accept:** `POST .../accept`. `409 offer_not_actionable` if the offer has expired or was already answered. The expired banner applies.
- **Decline:** `POST .../decline`.
- Offer statuses: `PENDING, ACCEPTED_PENDING_PAYMENT, ACCEPTED, DECLINED, EXPIRED`.
- An offer expires after a server-side TTL (`expires_at`). It never expires later than the booking's scheduled time.
- Dispatch radius: 50 km.

**Jobs:** `GET /api/contractor/jobs?status=ASSIGNED|ARRIVED|IN_PROGRESS|AWAITING_CUSTOMER_CONFIRMATION|COMPLETED`.

**Earnings:**

- `GET /api/contractor/earnings?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD` for W07.
- `GET /api/bookings/{booking_id}/payout` for W08 (the contractor on that booking only).
- A payout is created only after the customer confirms **and** the customer's payment succeeded.

### 3.10 Push and the notification inbox

**Register the device**

- `POST /api/devices` with `{token, platform: "IOS"|"ANDROID", app_version}` after login and whenever FCM rotates the token.
- `POST /api/devices/unregister` with `{token}`, or pass `device_token` to logout.

**Payload**

- FCM `notification` has a title and body, in English.
- `data` values are all strings:
  - `type`, `audience` (`CUSTOMER` | `CONTRACTOR` | `ALL`), `notification_id`;
  - plus the ids listed below.

**Android channels**

| Priority | Channel id |
| --- | --- |
| HIGH | `offers` |
| NORMAL | `general` |

Create both channels in the app. The names can be changed by configuration if you need different ones; tell us.

**Events**

| `type` | To | Data ids | Title |
| --- | --- | --- | --- |
| `offer.new` | cleaner | `offer_id`, `booking_id` | New job offer (HIGH) |
| `offer.cancelled` | cleaner | `offer_id`, `booking_id` | Job cancelled |
| `job.confirmed` | cleaner | `booking_id` | Job confirmed |
| `job.completed` | cleaner | `booking_id`, `job_id` | Job completed |
| `payout.sent` | cleaner | `booking_id`, `payout_id` | Payout sent |
| `verification.approved` | cleaner | `document_id`, `document_type` | Document approved |
| `verification.rejected` | cleaner | `document_id`, `document_type` | Document needs attention |
| `booking.confirmed` | customer | `booking_id` | Cleaner confirmed |
| `payment.failed` | customer | `booking_id`, `payment_id` | Payment failed (HIGH) |
| `payment.action_required` | customer | `booking_id`, `payment_id` | Action needed (HIGH) |
| `job.arrived` | customer | `booking_id`, `job_id` | Your cleaner has arrived |
| `job.started` | customer | `booking_id`, `job_id` | Cleaning started |
| `job.awaiting_confirmation` | customer | `booking_id`, `job_id` | Please confirm |
| `support.updated` | either | `support_request_id` | Support update |
| `broadcast` | per target | `broadcast_id` | Admin announcement |

**In-app inbox.** Every push is also stored for 90 days.

| Call | Purpose |
| --- | --- |
| `GET /api/notifications?unread_only=&audience=&limit=&offset=` | List. |
| `GET /api/notifications/unread-count` | Badge count. |
| `POST /api/notifications/{id}/read` | Mark one read. |
| `POST /api/notifications/read-all` | Mark all read. |

In a dual-role account, filter by `audience` for the current mode.

**Push is a hint, not the source of truth.** Always re-fetch the booking, job or offer on open.

### 3.11 Support

- `POST /api/support-requests` with `{category, message, booking_id?}`.
  - Categories: `BOOKING_ISSUE, PAYMENT_ISSUE, CONTRACTOR_ISSUE, APP_ISSUE, OTHER`.
- `GET /api/support-requests` and `GET /api/support-requests/{id}` return the history.
  - Statuses: `SUBMITTED, UNDER_REVIEW, RESOLVED`.

---

## Part 4 — Errors, enums, checklist

### 4.1 Error format

Every handled error has this body:

```json
{"code": "outside_business_hours", "detail": "Cleaners can be requested between 07:00 and 19:00 …"}
```

- Translate by **`code`**. `detail` is English and may change.
- Auth errors may add `retry_after_seconds`.
- Schema errors:

```json
{"code": "validation_error", "detail": "…", "errors": [{"field": "phone", "loc": ["body","payload","phone"], "message": "Field required", "type": "missing"}]}
```

- `401` from a missing or expired token is `{"detail": "Unauthorized"}` with no `code`. Refresh once, then log in again.
- An account suspended by an admin gets `401` on every call, just like an expired session. After a failed refresh, send the user to login.
- The full list, with status per code, is in `ERROR_CODES.md`.

### 4.2 Enums

| Field | Values |
| --- | --- |
| `BookingOut.status` | `PENDING, CONFIRMED, CANCELLED` |
| `BookingOut.dispatch_status` | `SEARCHING, NO_CONTRACTOR, ASSIGNED` |
| Offer `status` | `PENDING, ACCEPTED_PENDING_PAYMENT, ACCEPTED, DECLINED, EXPIRED` |
| Job `status` | `ASSIGNED, ARRIVED, IN_PROGRESS, AWAITING_CUSTOMER_CONFIRMATION, COMPLETED` |
| Payment `status` | `NOT_CHARGED, PENDING, PROCESSING, REQUIRES_ACTION, SUCCEEDED, FAILED, REFUNDED` |
| Payout `status` | `PENDING, SUCCEEDED, FAILED` |
| `contractor_status` | `NONE, PENDING, ACTION_REQUIRED, APPROVED, SUSPENDED` |
| `available_modes` / `roles` | `CUSTOMER, CONTRACTOR` (`ADMIN` is never an app user) |
| Property type | `HOUSE, UNIT, TOWNHOUSE, APARTMENT, OTHER` |
| State | `NSW, VIC, QLD, WA, SA, TAS, ACT, NT` |
| Photo type | `BEFORE, AFTER` |
| Device platform | `IOS, ANDROID, WEB` |
| Support category / status | see §3.11 |

### 4.3 Changes since the 2026-09-24 reply (app work)

1. Make `scheduled_at` optional; send `null` for "now". Handle `outside_business_hours`.
2. Add the **Arrive** step (`/arrive`) and remove the 200 m local guess. `/start` now needs `ARRIVED`.
3. Add **Cancel** for customers while unpaid. Handle `offer.cancelled` on the cleaner side.
4. Start the **online location feed** (`PUT /api/contractor/location`) while the cleaner is available.
5. Wire `/quotes`, the offers list and detail, contractor jobs, and earnings (C1, C22–C26).
6. Parse the new 422 shape (`validation_error`, `errors[]`).
7. Use `contractor_status` for the application state.
8. Create the Android channels `offers` and `general`. Route push by `data.type`.

---

## Part 5 — Endpoint and schema reference (generated)

Generated from `openapi.json`. Admin endpoints are omitted; they are in `DASHBOARD_API_GUIDE.md`.

### Auth

#### `POST /api/auth/otp/request`

**Request an OTP code** — public (no token).

Request body (`application/json`): `OTPRequestIn`

Responses: `200` → `OTPRequestOut`, `429` → `ErrorOut`, `400` → `ErrorOut`, `503` → `ErrorOut`

#### `POST /api/auth/otp/verify`

**Verify an OTP code and issue JWT (customers and contractors)** — public (no token).

Request body (`application/json`): `OTPVerifyIn`

Responses: `200` → `AuthOut`, `400` → `ErrorOut`, `403` → `ErrorOut`, `429` → `ErrorOut`

#### `POST /api/auth/social/{provider}`

**Log in with Apple or Google** — public (no token).

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `provider` | path | string | yes |  |

Request body (`application/json`): `SocialLoginIn`

Responses: `200` → `AuthOut`, `400` → `ErrorOut`, `401` → `ErrorOut`, `403` → `ErrorOut`

#### `POST /api/auth/token/refresh`

**Refresh the access token** — public (no token).

Request body (`application/json`): `RefreshIn`

Responses: `200` → `TokenPairOut`, `401` → `ErrorOut`

#### `POST /api/auth/logout`

**Log out** — public (no token).

Request body (`application/json`): `LogoutIn`

Responses: `204`

#### `GET /api/auth/me`

**Current authenticated user** — Bearer token.

Responses: `200` → `UserOut`

#### `PATCH /api/auth/me`

**Update own name and email** — Bearer token.

Request body (`application/json`): `UserProfilePatch`

Responses: `200` → `UserOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

### Properties

#### `POST /api/properties`

**Create a property with its address** — Bearer token.

Request body (`application/json`): `PropertyIn`

Responses: `201` → `PropertyOut`, `403` → `ErrorOut`, `422` → `ErrorOut`

#### `GET /api/properties`

**List own properties** — Bearer token.

Responses: `200` → `array of PropertyOut`, `403` → `ErrorOut`

#### `GET /api/properties/{property_id}`

**Retrieve one own property** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `property_id` | path | string (uuid) | yes |  |

Responses: `200` → `PropertyOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `PATCH /api/properties/{property_id}`

**Update own property and/or its address** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `property_id` | path | string (uuid) | yes |  |

Request body (`application/json`): `PropertyPatch`

Responses: `200` → `PropertyOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

#### `DELETE /api/properties/{property_id}`

**Deactivate own property (soft delete)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `property_id` | path | string (uuid) | yes |  |

Responses: `200` → `PropertyOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`

### Services

#### `GET /api/services`

**List the services available for booking** — Bearer token.

Responses: `200` → `array of ServicePublicOut`

#### `GET /api/services/{service_id}`

**Retrieve one available service** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `service_id` | path | string (uuid) | yes |  |

Responses: `200` → `ServicePublicOut`, `404` → `ErrorOut`

### Booking Quotes

#### `POST /api/quotes`

**Create a maximum-price quote (customer only)** — Bearer token.

Request body (`application/json`): `QuoteIn`

Responses: `201` → `QuoteOut`, `400` → `ErrorOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

### Bookings

#### `POST /api/bookings`

**Create a booking with its service selections (customer only)** — Bearer token.

Request body (`application/json`): `BookingIn`

Responses: `201` → `BookingOut`, `400` → `ErrorOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

#### `GET /api/bookings`

**List own bookings** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `limit` | query | integer |  | default `100`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |

Responses: `200` → `array of BookingOut`, `403` → `ErrorOut`

#### `GET /api/bookings/{booking_id}`

**Retrieve one own booking** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `booking_id` | path | string (uuid) | yes |  |

Responses: `200` → `BookingOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `POST /api/bookings/{booking_id}/reschedule`

**Reschedule a booking that found no cleaner (customer only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `booking_id` | path | string (uuid) | yes |  |

Request body (`application/json`): `BookingRescheduleIn`

Responses: `200` → `BookingOut`, `400` → `ErrorOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

#### `POST /api/bookings/{booking_id}/cancel`

**Cancel an unpaid booking (customer only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `booking_id` | path | string (uuid) | yes |  |

Responses: `200` → `BookingOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`

### Payments

#### `POST /api/bookings/payments/{payment_id}/retry`

**Retry a failed payment (booking owner only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `payment_id` | path | string (uuid) | yes |  |
| `payment_method_reference` | query | string |  | default `` |

Responses: `200` → `PaymentActionOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

#### `POST /api/bookings/payments/{payment_id}/confirm-action`

**Confirm a payment authentication action (booking owner only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `payment_id` | path | string (uuid) | yes |  |

Responses: `200` → `PaymentActionOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `501` → `ErrorOut`

#### `GET /api/bookings/{booking_id}/payment`

**Retrieve the payment for a booking (owner customer or admin)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `booking_id` | path | string (uuid) | yes |  |

Responses: `200`, `404` → `ErrorOut`

### Jobs

#### `GET /api/bookings/{booking_id}/job`

**Retrieve the job for a booking (owner customer, admin, or assigned contractor)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `booking_id` | path | string (uuid) | yes |  |

Responses: `200` → `JobOut`, `404` → `ErrorOut`

#### `POST /api/bookings/{booking_id}/job/confirm`

**Confirm job completion (the booking's own customer only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `booking_id` | path | string (uuid) | yes |  |

Responses: `200` → `JobOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`

#### `GET /api/bookings/{booking_id}/tracking`

**Track the assigned contractor on the way (customer, admin or assigned contractor)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `booking_id` | path | string (uuid) | yes |  |

Responses: `200` → `JobTrackingOut`, `404` → `ErrorOut`

### Contractor Profile

#### `POST /api/contractor/profile`

**Create own contractor profile (contractor only)** — Bearer token.

Request body (`application/json`): `ContractorProfileIn`

Responses: `201` → `ContractorProfileOut`, `403` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

#### `GET /api/contractor/profile`

**Retrieve own contractor profile** — Bearer token.

Responses: `200` → `ContractorProfileOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `PATCH /api/contractor/profile`

**Update own contractor profile** — Bearer token.

Request body (`application/json`): `ContractorProfilePatch`

Responses: `200` → `ContractorProfileOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `422` → `ErrorOut`

#### `PATCH /api/contractor/profile/availability`

**Toggle own availability (contractor only)** — Bearer token.

Request body (`application/json`): `AvailabilityPatch`

Responses: `200` → `ContractorProfileOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `422` → `ErrorOut`

#### `POST /api/contractor/business-registration`

**Submit own business registration (contractor only)** — Bearer token.

Request body (`application/json`): `BusinessRegistrationIn`

Responses: `201` → `BusinessRegistrationOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

#### `GET /api/contractor/business-registration`

**List own business registration submissions** — Bearer token.

Responses: `200` → `array of BusinessRegistrationOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `POST /api/contractor/insurance`

**Submit own insurance document (contractor only)** — Bearer token.

Request body (`application/json`): `InsuranceDocumentIn`

Responses: `201` → `InsuranceDocumentOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

#### `GET /api/contractor/insurance`

**List own insurance submissions** — Bearer token.

Responses: `200` → `array of InsuranceDocumentOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `PUT /api/contractor/location`

**Report own current location for dispatch (contractor only)** — Bearer token.

Request body (`application/json`): `CurrentLocationIn`

Responses: `200` → `CurrentLocationOut`, `400` → `ErrorOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `GET /api/contractor/location`

**Own last reported location (contractor only)** — Bearer token.

Responses: `200` → `CurrentLocationOut`, `204`, `403` → `ErrorOut`, `404` → `ErrorOut`

### Contractor Offers

#### `GET /api/contractor/offers`

**List my dispatch offers (contractor only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `include_closed` | query | boolean |  | default `False` |

Responses: `200` → `array of OfferDetailOut`, `403` → `ErrorOut`

#### `GET /api/contractor/offers/{offer_id}`

**Get one of my dispatch offers (contractor only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `offer_id` | path | string (uuid) | yes |  |

Responses: `200` → `OfferDetailOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `POST /api/contractor/offers/{offer_id}/accept`

**Accept a dispatch offer (contractor only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `offer_id` | path | string (uuid) | yes |  |

Responses: `200` → `OfferResponseOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

#### `POST /api/contractor/offers/{offer_id}/decline`

**Decline a dispatch offer (contractor only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `offer_id` | path | string (uuid) | yes |  |

Responses: `200` → `OfferResponseOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

### Contractor Jobs

#### `GET /api/contractor/jobs`

**List assigned contractor jobs** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `status` | query | string \| null |  |  |

Responses: `200` → `array of ContractorJobOut`, `403` → `ErrorOut`

#### `POST /api/contractor/jobs/{job_id}/photos`

**Upload a before/after photo (assigned contractor only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `job_id` | path | string (uuid) | yes |  |
| `photo_type` | query | string | yes |  |

Request body (`multipart/form-data`): `file`

Responses: `201` → `JobPhotoOut`, `400` → `ErrorOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`

#### `POST /api/contractor/jobs/{job_id}/arrive`

**Report arrival at the property (assigned contractor only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `job_id` | path | string (uuid) | yes |  |

Request body (`application/json`): `JobArriveIn`

Responses: `200` → `JobOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

#### `POST /api/contractor/jobs/{job_id}/start`

**Start a job (assigned contractor only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `job_id` | path | string (uuid) | yes |  |

Responses: `200` → `JobOut`, `400` → `ErrorOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`

#### `POST /api/contractor/jobs/{job_id}/mark-done`

**Mark a job done (assigned contractor only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `job_id` | path | string (uuid) | yes |  |

Responses: `200` → `JobOut`, `400` → `ErrorOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`

#### `POST /api/contractor/jobs/{job_id}/location`

**Report the assigned contractor's current location (assigned contractor only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `job_id` | path | string (uuid) | yes |  |

Request body (`application/json`): `JobLocationIn`

Responses: `200` → `JobTrackingOut`, `400` → `ErrorOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

### Payouts

#### `GET /api/bookings/{booking_id}/payout`

**Retrieve the contractor payout for a booking (payee contractor or admin)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `booking_id` | path | string (uuid) | yes |  |

Responses: `200`, `404` → `ErrorOut`

#### `GET /api/contractor/earnings`

**List contractor earnings for a date range** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `from_date` | query | string (date) \| null |  |  |
| `to_date` | query | string (date) \| null |  |  |

Responses: `200` → `EarningsOut`, `400` → `ErrorOut`, `403` → `ErrorOut`

### Notifications

#### `POST /api/devices`

**Register this device for push notifications** — Bearer token.

Request body (`application/json`): `DeviceIn`

Responses: `200` → `DeviceOut`, `201` → `DeviceOut`, `422` → `ErrorOut`

#### `POST /api/devices/unregister`

**Stop push notifications on this device** — Bearer token.

Request body (`application/json`): `DeviceUnregisterIn`

Responses: `204`

#### `GET /api/notifications`

**List my notifications** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `unread_only` | query | boolean |  | default `False` |
| `audience` | query | string |  |  |
| `limit` | query | integer |  | default `50`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |

Responses: `200` → `NotificationListOut`

#### `GET /api/notifications/unread-count`

**Unread notifications count** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `audience` | query | string |  |  |

Responses: `200` → `UnreadCountOut`

#### `POST /api/notifications/read-all`

**Mark all my notifications as read** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `audience` | query | string |  |  |

Responses: `200` → `MarkAllOut`

#### `POST /api/notifications/{notification_id}/read`

**Mark one notification as read** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `notification_id` | path | string (uuid) | yes |  |

Responses: `200` → `NotificationOut`, `404` → `ErrorOut`

### Support

#### `POST /api/support-requests`

**Open a support request (any authenticated user)** — Bearer token.

Request body (`application/json`): `SupportRequestIn`

Responses: `201` → `SupportRequestOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `422` → `ErrorOut`

#### `GET /api/support-requests`

**List own support requests (all of them for an admin)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `limit` | query | integer |  | default `100`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |

Responses: `200` → `array of SupportRequestOut`, `403` → `ErrorOut`

#### `GET /api/support-requests/{request_id}`

**Retrieve one support request (owner or admin)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `request_id` | path | string (uuid) | yes |  |

Responses: `200` → `SupportRequestOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

### System

#### `GET /api/health`

**Service health check** — public (no token).

Responses: `200`

#### `GET /api/health/ready`

**Readiness check (database reachable and fully migrated)** — public (no token).

Responses: `200`, `503`

### Schemas

#### `AuthOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `tokens` | `TokenPairOut` | yes |  |
| `user` | `UserOut` | yes |  |

#### `AvailabilityPatch`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `availability_status` | `AvailabilityStatus` | yes |  |

#### `BookingIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `property_id` | string (uuid) | yes |  |
| `service_selections` | array of `ServiceSelectionIn` |  |  |
| `scheduled_at` | string (date-time) \| null |  |  |
| `access_notes` | string |  | max len 500 |
| `quote_id` | string (uuid) \| null |  |  |
| `payment_method_reference` | string |  | max len 255 |

#### `BookingOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `public_reference` | string | yes |  |
| `customer_id` | string (uuid) | yes |  |
| `property_id` | string (uuid) | yes |  |
| `status` | string | yes |  |
| `computed_price` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `assigned_contractor_id` | string (uuid) \| null |  |  |
| `scheduled_at` | string (date-time) \| null |  |  |
| `scheduled_at_local` | string (date-time) \| null |  |  |
| `is_on_demand` | boolean |  | default `false` |
| `requested_at` | string (date-time) \| null |  |  |
| `cancelled_at` | string (date-time) \| null |  |  |
| `cancellation_reason` | string |  |  |
| `customer_timezone` | string |  |  |
| `dispatch_status` | string |  |  |
| `last_dispatch_attempt_at` | string (date-time) \| null |  |  |
| `access_notes` | string \| null |  |  |
| `payment` | `PaymentSummaryOut` \| null |  |  |
| `service_selections` | array of `ServiceSelectionOut` | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `BookingRescheduleIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `scheduled_at` | string (date-time) \| null |  |  |
| `timezone` | string \| null |  |  |

#### `BusinessRegistrationIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `abn` | string | yes | pattern ^\d{11}$ |
| `business_name` | string | yes | min len 1, max len 255 |

#### `BusinessRegistrationOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `contractor_id` | string (uuid) | yes |  |
| `abn` | string | yes |  |
| `business_name` | string | yes |  |
| `status` | string | yes |  |
| `reviewed_by_id` | string (uuid) \| null |  |  |
| `reviewed_at` | string (date-time) \| null |  |  |
| `rejection_reason` | string \| null |  |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `ContractorJobOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `booking_id` | string (uuid) | yes |  |
| `status` | string | yes |  |
| `arrived_at` | string (date-time) \| null |  |  |
| `started_at` | string (date-time) \| null |  |  |
| `access_notes` | string \| null |  |  |
| `marked_done_at` | string (date-time) \| null |  |  |
| `confirmed_at` | string (date-time) \| null |  |  |
| `photos` | array of `JobPhotoOut` | yes |  |
| `created_at` | string (date-time) | yes |  |
| `public_reference` | string | yes |  |
| `service_summary` | array of string | yes |  |
| `property_summary` | string \| null |  |  |
| `scheduled_at_local` | string (date-time) \| null |  |  |
| `earnings` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `payout_status` | string \| null |  |  |

#### `ContractorProfileIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `business_name` | string |  | max len 255 |
| `street_address` | string |  | max len 255 |
| `suburb` | string |  | max len 120 |
| `state` | `AustralianState` \| null |  |  |
| `postcode` | string |  | pattern ^(\d{4})?$ |
| `latitude` | number \| string \| null |  | ≥ -90.0, ≤ 90.0 |
| `longitude` | number \| string \| null |  | ≥ -180.0, ≤ 180.0 |

#### `ContractorProfileOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `user_id` | string (uuid) | yes |  |
| `business_name` | string | yes |  |
| `street_address` | string | yes |  |
| `suburb` | string | yes |  |
| `state` | string | yes |  |
| `postcode` | string | yes |  |
| `country` | string | yes |  |
| `latitude` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `longitude` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `availability_status` | string | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `ContractorProfilePatch`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `business_name` | string \| null |  | max len 255 |
| `street_address` | string \| null |  | max len 255 |
| `suburb` | string \| null |  | max len 120 |
| `state` | `AustralianState` \| null |  |  |
| `postcode` | string \| null |  | pattern ^(\d{4})?$ |
| `latitude` | number \| string \| null |  | ≥ -90.0, ≤ 90.0 |
| `longitude` | number \| string \| null |  | ≥ -180.0, ≤ 180.0 |

#### `CurrentLocationIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `latitude` | number \| string | yes | ≥ -90.0, ≤ 90.0 |
| `longitude` | number \| string | yes | ≥ -180.0, ≤ 180.0 |
| `accuracy` | number \| string \| null |  | ≥ 0.0 |
| `recorded_at` | string (date-time) | yes |  |

#### `CurrentLocationOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `latitude` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `longitude` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `accuracy` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `recorded_at` | string (date-time) | yes |  |
| `received_at` | string (date-time) | yes |  |
| `is_fresh` | boolean | yes |  |
| `fresh_for_seconds` | integer | yes |  |

#### `DeviceIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `token` | string | yes | min len 10, max len 512 |
| `platform` | string | yes | IOS | ANDROID | WEB |
| `app_version` | string |  | max len 32 |

#### `DeviceOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `platform` | string | yes |  |
| `app_version` | string |  |  |
| `created_at` | string (date-time) | yes |  |
| `last_seen_at` | string (date-time) | yes |  |

#### `DeviceUnregisterIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `token` | string | yes | min len 10, max len 512 |

#### `EarningsOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `from_date` | string (date) | yes |  |
| `to_date` | string (date) | yes |  |
| `total` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `paid_total` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `processing_total` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `completed_jobs` | integer | yes |  |
| `items` | array of `EarningsItemOut` | yes |  |

#### `ErrorOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `code` | string | yes |  |
| `detail` | string | yes |  |

#### `InsuranceDocumentIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `document_reference` | string | yes | min len 1, max len 255 |
| `expiry_date` | string (date) | yes |  |

#### `InsuranceDocumentOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `contractor_id` | string (uuid) | yes |  |
| `document_reference` | string | yes |  |
| `expiry_date` | string (date) | yes |  |
| `status` | string | yes |  |
| `reviewed_by_id` | string (uuid) \| null |  |  |
| `reviewed_at` | string (date-time) \| null |  |  |
| `rejection_reason` | string \| null |  |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `JobArriveIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `latitude` | number \| string | yes | ≥ -90.0, ≤ 90.0 |
| `longitude` | number \| string | yes | ≥ -180.0, ≤ 180.0 |
| `accuracy` | number \| string \| null |  | ≥ 0.0 |

#### `JobLocationIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `latitude` | number \| string | yes | ≥ -90.0, ≤ 90.0 |
| `longitude` | number \| string | yes | ≥ -180.0, ≤ 180.0 |
| `accuracy` | number \| string \| null |  | ≥ 0.0 |
| `recorded_at` | string (date-time) | yes |  |

#### `JobOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `booking_id` | string (uuid) | yes |  |
| `status` | string | yes |  |
| `arrived_at` | string (date-time) \| null |  |  |
| `started_at` | string (date-time) \| null |  |  |
| `access_notes` | string \| null |  |  |
| `marked_done_at` | string (date-time) \| null |  |  |
| `confirmed_at` | string (date-time) \| null |  |  |
| `photos` | array of `JobPhotoOut` | yes |  |
| `created_at` | string (date-time) | yes |  |

#### `JobPhotoOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `photo_type` | string | yes |  |
| `signed_url` | string | yes |  |
| `storage_key` | string \| null |  |  |
| `uploaded_at` | string (date-time) | yes |  |

#### `JobTrackingOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `booking_id` | string (uuid) | yes |  |
| `job_id` | string (uuid) | yes |  |
| `job_status` | string | yes |  |
| `tracking_active` | boolean | yes |  |
| `contractor_location` | `ContractorLocationOut` \| null |  |  |
| `property_location` | `PropertyLocationOut` | yes |  |

#### `LogoutIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `refresh` | string | yes | min len 1 |
| `device_token` | string \| null |  | max len 512 |

#### `MarkAllOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `marked` | integer | yes |  |

#### `NotificationListOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `count` | integer | yes |  |
| `unread` | integer | yes |  |
| `items` | array of `NotificationOut` | yes |  |

#### `NotificationOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `type` | string | yes |  |
| `audience` | string | yes |  |
| `title` | string | yes |  |
| `body` | string | yes |  |
| `data` | object |  |  |
| `priority` | string | yes |  |
| `read` | boolean | yes |  |
| `read_at` | string (date-time) \| null |  |  |
| `created_at` | string (date-time) | yes |  |

#### `OTPRequestIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `phone` | string | yes | min len 4, max len 32 |

#### `OTPRequestOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `detail` | string | yes |  |
| `expires_in_seconds` | integer | yes |  |

#### `OTPVerifyIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `phone` | string | yes | min len 4, max len 32 |
| `code` | string | yes | min len 4, max len 12 |

#### `OfferDetailOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `booking_id` | string (uuid) | yes |  |
| `contractor_id` | string (uuid) | yes |  |
| `status` | string | yes |  |
| `distance_km` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `offered_at` | string (date-time) | yes |  |
| `responded_at` | string (date-time) \| null |  |  |
| `expires_at` | string (date-time) | yes |  |
| `total_amount` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `contractor_earnings` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `currency` | string |  | default `"AUD"` |
| `eta_seconds` | integer \| null |  |  |
| `service_summary` | array of string |  |  |
| `property_summary` | string \| null |  |  |
| `scheduled_at` | string (date-time) \| null |  |  |
| `scheduled_at_local` | string (date-time) \| null |  |  |
| `is_on_demand` | boolean |  | default `false` |
| `customer_timezone` | string |  |  |
| `suburb` | string |  |  |
| `state` | string |  |  |
| `postcode` | string |  |  |
| `property_type` | string |  |  |
| `services` | array of `OfferServiceOut` |  |  |

#### `OfferResponseOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `offer` | `OfferOut` | yes |  |
| `next_offer` | string \| null |  |  |

#### `PaymentActionOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `booking_id` | string (uuid) | yes |  |
| `amount` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `method` | string | yes |  |
| `status` | string | yes |  |
| `failure_reason` | string \| null |  |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |
| `attempt_number` | integer | yes |  |
| `action_payload` | object \| null |  |  |

#### `PropertyIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `label` | string |  | max len 100 |
| `property_type` | any |  | default `"HOUSE"` |
| `address` | `AddressIn` | yes |  |

#### `PropertyOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `owner_id` | string (uuid) | yes |  |
| `label` | string | yes |  |
| `property_type` | string | yes |  |
| `is_active` | boolean | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |
| `address` | `AddressOut` \| null |  |  |
| `serviceability_warning` | `ServiceabilityWarningOut` \| null |  |  |

#### `PropertyPatch`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `label` | string \| null |  | max len 100 |
| `property_type` | `PropertyType` \| null |  |  |
| `is_active` | boolean \| null |  |  |
| `address` | `AddressPatch` \| null |  |  |

#### `QuoteIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `property_id` | string (uuid) | yes |  |
| `service_selections` | array of `ServiceSelectionIn` | yes |  |

#### `QuoteOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `property_id` | string (uuid) | yes |  |
| `maximum_total` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `services_total` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `currency` | string | yes |  |
| `pricing_version` | integer | yes |  |
| `expires_at` | string (date-time) | yes |  |
| `created_at` | string (date-time) | yes |  |

#### `RefreshIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `refresh` | string | yes | min len 1 |

#### `ServicePublicOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `name` | string | yes |  |
| `description` | string | yes |  |

#### `SocialLoginIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `token` | string | yes | min len 1 |

#### `SupportRequestIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `category` | `SupportCategory` | yes |  |
| `message` | string | yes | min len 1, max len 2000 |
| `booking_id` | string (uuid) \| null |  |  |

#### `SupportRequestOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `user_id` | string (uuid) | yes |  |
| `booking_id` | string (uuid) \| null |  |  |
| `category` | string | yes |  |
| `message` | string | yes |  |
| `status` | string | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `TokenPairOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `access` | string | yes |  |
| `refresh` | string | yes |  |

#### `UnreadCountOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `unread` | integer | yes |  |

#### `UserOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `role` | string | yes |  |
| `phone` | string | yes |  |
| `status` | string | yes |  |
| `full_name` | string |  |  |
| `email` | string \| null |  |  |
| `roles` | array of string |  |  |
| `contractor_status` | string |  | default `"NONE"` |
| `available_modes` | array of string |  |  |

#### `UserProfilePatch`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `full_name` | string \| null |  | max len 255 |
| `email` | string \| null |  | max len 254 |

#### `AddressIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `street_address` | string | yes | min len 1, max len 255 |
| `suburb` | string | yes | min len 1, max len 120 |
| `state` | `AustralianState` | yes |  |
| `postcode` | string | yes | pattern ^\d{4}$ |
| `latitude` | number \| string \| null |  | ≥ -90.0, ≤ 90.0 |
| `longitude` | number \| string \| null |  | ≥ -180.0, ≤ 180.0 |
| `raw_input` | string \| null |  |  |

#### `AddressOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `street_address` | string | yes |  |
| `suburb` | string | yes |  |
| `state` | string | yes |  |
| `postcode` | string | yes |  |
| `country` | string | yes |  |
| `latitude` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `longitude` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `raw_input` | string \| null |  |  |

#### `AddressPatch`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `street_address` | string \| null |  | min len 1, max len 255 |
| `suburb` | string \| null |  | min len 1, max len 120 |
| `state` | `AustralianState` \| null |  |  |
| `postcode` | string \| null |  | pattern ^\d{4}$ |
| `latitude` | number \| string \| null |  | ≥ -90.0, ≤ 90.0 |
| `longitude` | number \| string \| null |  | ≥ -180.0, ≤ 180.0 |
| `raw_input` | string \| null |  |  |

#### `AustralianState`

One of: `NSW`, `VIC`, `QLD`, `WA`, `SA`, `TAS`, `ACT`, `NT`

#### `AvailabilityStatus`

One of: `AVAILABLE`, `UNAVAILABLE`

#### `ContractorLocationOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `latitude` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `longitude` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `accuracy` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `recorded_at` | string (date-time) | yes |  |
| `received_at` | string (date-time) | yes |  |
| `age_seconds` | integer | yes |  |
| `is_stale` | boolean | yes |  |

#### `EarningsItemOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `payout_id` | string (uuid) | yes |  |
| `booking_id` | string (uuid) | yes |  |
| `public_reference` | string | yes |  |
| `service_summary` | array of string | yes |  |
| `completed_at` | string (date-time) \| null |  |  |
| `amount` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `currency` | string |  | default `"AUD"` |
| `status` | string | yes |  |

#### `OfferOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `booking_id` | string (uuid) | yes |  |
| `contractor_id` | string (uuid) | yes |  |
| `status` | string | yes |  |
| `distance_km` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `offered_at` | string (date-time) | yes |  |
| `responded_at` | string (date-time) \| null |  |  |
| `expires_at` | string (date-time) | yes |  |
| `total_amount` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `contractor_earnings` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `currency` | string |  | default `"AUD"` |
| `eta_seconds` | integer \| null |  |  |
| `service_summary` | array of string |  |  |
| `property_summary` | string \| null |  |  |

#### `OfferServiceOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `service_type_id` | string (uuid) | yes |  |
| `service_name` | string | yes |  |
| `room_count` | integer | yes |  |

#### `PaymentSummaryOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `status` | string | yes |  |
| `amount` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `method` | string | yes |  |
| `attempt_number` | integer | yes |  |
| `provider_reference` | string \| null |  |  |
| `provider_error_code` | string | yes |  |
| `failure_reason` | string \| null |  |  |
| `paid_at` | string (date-time) \| null |  |  |
| `created_at` | string (date-time) | yes |  |

#### `PropertyLocationOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `latitude` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `longitude` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |

#### `PropertyType`

One of: `HOUSE`, `UNIT`, `TOWNHOUSE`, `APARTMENT`, `OTHER`

#### `ServiceSelectionIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `service_type_id` | string (uuid) | yes |  |
| `room_count` | integer | yes | ≥ 0; Rooms for this service: price = room_price × room_count + base_price. Add-ons have room_price 0, so any value (0 or 1) prices the same. |

#### `ServiceSelectionOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `service_type_id` | string (uuid) | yes |  |
| `service_type_name` | string | yes |  |
| `room_count` | integer | yes |  |

#### `ServiceabilityWarningOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `code` | string | yes |  |
| `message` | string | yes |  |

#### `SupportCategory`

One of: `BOOKING_ISSUE`, `PAYMENT_ISSUE`, `CONTRACTOR_ISSUE`, `APP_ISSUE`, `OTHER`

