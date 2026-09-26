# Cleano admin dashboard — backend API guide

**For:** the developer of the admin dashboard website.
**Date:** 2026-09-26.

Everything the dashboard needs is under `/api/admin/*`. The built-in Django admin (`/admin/`) is a backup tool, not the product.

Part 3 is the full endpoint and schema reference, generated from `openapi.json`.

Source of truth, in this order:

1. `openapi.json` in the repo (live at `/api/openapi.json`, interactive at `/api/docs`).
2. `ERROR_CODES.md`, which lists every error `code`.
3. This document.

- **Production:** `https://cleaninghouse-production.up.railway.app`
- **Local:** `http://localhost:8000`

> **Before you start — CORS.** The backend does not send CORS headers yet.
> If the dashboard is served from a different origin than the API (for example `https://admin.cleano.com.au` or `http://localhost:5173`), browser calls will be blocked.
> Send us the exact origins (production, staging, local dev) and we will allow them.
> Tokens are sent in the `Authorization` header, not in cookies, so no credentials mode is needed.

---

## Part 1 — Authentication and accounts

### 1.1 Login (two steps)

Admins log in with **email or phone plus a password**, then an **SMS code**.
A device can be trusted for 30 days, which skips the SMS step on that device.

**Step 1.** `POST /api/admin/auth/login`

```json
{"identifier": "admin@example.com", "password": "…", "device_token": "<saved trusted-device token or null>"}
```

The response is `AdminLoginOut`. It has one of two `state` values:

- `"authenticated"`: `tokens: {access, refresh}` is present. This happens when `device_token` was valid, so no SMS is needed.
- `"otp_required"`: the response has `challenge_id`, `phone_hint` (for example `+614•••123`) and `expires_in_seconds`. An SMS was sent.

**Step 2.** `POST /api/admin/auth/verify`

```json
{"challenge_id": "…", "code": "123456", "remember_device": true, "device_label": "Chrome on office PC"}
```

The response is `AdminVerifyOut`:

- `tokens`
- `must_change_password`
- with `remember_device: true`: `device_token` and `device_expires_in_days`

Store `device_token` (localStorage is fine) and send it on every future login from this browser.

**Resend the code:** `POST /api/admin/auth/resend` with `{challenge_id}`. A cooldown applies (`429` with `retry_after_seconds`).

**Security rules to reflect in the UI**

- **Lockout:** 5 wrong passwords lock the account for 15 minutes (`429 account_locked` with `retry_after_seconds`).
- **One error for all login failures:** a wrong identifier and a wrong password return the same error. Do not try to tell them apart.
- **Not an admin:** a non-admin account cannot log in here, even with a correct password.

### 1.2 Tokens and session

Tokens are the same JWT pair as the app.

| Token | Lifetime | Notes |
| --- | --- | --- |
| access | 15 min | Send as `Authorization: Bearer <access>`. |
| refresh | 7 days | Rotated on every use. |

- **Refresh:** `POST /api/auth/token/refresh` with `{"refresh"}` returns a **new pair**. The old refresh token is invalid immediately. Serialise refreshes so that two tabs do not both use the same refresh token.
- **Logout:** `POST /api/auth/logout` with `{"refresh"}` returns `204`.
- **On `401`:** refresh once. If that fails, go to login.
- **Suspended admin:** a suspended admin, or one whose sessions were revoked by a superuser, gets `401` on the next call.

### 1.3 Forced password change

New admins and admins whose password was reset receive a **temporary password**. Until they change it:

- `AdminVerifyOut.must_change_password` and `GET /api/admin/auth/me` → `must_change_password` are `true`.
- **Every** other endpoint answers `403 password_change_required`. Only `/api/admin/auth/*` and `/api/auth/me` work.

When you see that code, route to a "set a new password" screen that calls `POST /api/admin/auth/password`:

```json
{"current_password": "<temporary>", "new_password": "…"}
```

**Password policy:**

- at least 12 characters;
- not too common;
- not all digits;
- not too similar to the user's details.

**Errors:**

- `422 password_invalid`: the new password fails the policy. Show `detail`; it lists the reasons.
- `400 wrong_password`: the current password is wrong.

The same endpoint is the normal "change my password" screen.

### 1.4 My account and trusted devices

- `GET /api/admin/auth/me` returns `AdminMeOut`: `email`, `phone`, `full_name`, `is_superuser`, `must_change_password`, `last_login`. Use `is_superuser` to show or hide superuser-only screens.
- `GET /api/admin/auth/devices` returns the trusted browsers, with `label`, `ip_address`, `last_used_at` and `expires_at`.
- `DELETE /api/admin/auth/devices/{id}` revokes one; the next login from it asks for SMS again.

### 1.5 Admin accounts (superuser only)

Non-superusers get `403` on all of these. Hide the menu for them.

| Action | Call | Notes |
| --- | --- | --- |
| List | `GET /api/admin/admins?status=&limit=&offset=` | Shows `failed_login_attempts`, `locked_until`, `last_login`. |
| Create | `POST /api/admin/admins` `{email, phone, full_name, is_superuser}` | Returns `{admin, temporary_password}`. **Show the temporary password once** and hand it over out of band. The new admin must change it at first login. Email and phone are both required; the phone receives the SMS codes. |
| View | `GET /api/admin/admins/{id}` | |
| Edit | `PATCH /api/admin/admins/{id}` `{full_name, email, phone, status, is_superuser}` | `status: SUSPENDED` blocks the admin. A superuser cannot demote or deactivate themselves, and at least one active superuser must remain (`409 admin_management_error`). |
| Reset password | `POST /api/admin/admins/{id}/reset-password` | This is how forgotten passwords are handled; there is no self-service reset. Returns a new temporary password, signs the admin out everywhere, and clears the lockout. |
| Sign out everywhere | `POST /api/admin/admins/{id}/revoke-sessions` | Also drops their trusted devices. |

All of these are written to the audit log.

### 1.6 Test phase (no SMS provider yet)

- Until an SMS provider is contracted, the backend accepts a fixed code for a short list of **test phone numbers**. Admin logins use them only when explicitly enabled.
- The PO has the numbers and the codes. Test mode switches itself off on a set date.
- Do not build anything that depends on it.

---

## Part 2 — Using the admin API

### 2.1 Conventions

**Lists** return `{"count": <total>, "items": [...]}`.

- Paging: `limit` (default and maximum vary per endpoint; see the reference) and `offset`.
- Build the pager from `count`.
- Date filters (`created_from`, `created_to` and similar) take ISO dates or datetimes.
- `q` is a free-text search where offered.

**Values**

- Ids are UUIDs. A malformed id returns `422 validation_error`.
- Money is a decimal string or number in AUD. Never use floating point for totals.
- Times are ISO-8601 UTC. Show them in the viewer's timezone. For bookings, `scheduled_at_local` / `customer_timezone` give the property's local time.

**Errors**

- Errors are `{"code", "detail"}`. Show `detail`, or map `code` to your own text.
- `422` adds `errors[]` with `field`, `loc`, `message` and `type`. Use them to mark form fields.

**Permissions**

- Every `/api/admin/*` endpoint needs an ADMIN account.
- Superuser-only endpoints are marked below and in the reference ("superuser only"). Others get `403`.

**Audit.** Every write (suspend, cancel, verify, reconcile, catalog change, broadcast, admin management) is recorded with actor, target, IP and before/after values.

### 2.2 Screens and endpoints

**Dashboard**

`GET /api/admin/dashboard/summary` returns:

- booking counts by status and dispatch state;
- pending verifications;
- open support requests;
- payment and payout counts, including those that need reconciliation;
- gross revenue;
- contractors available now;
- customer and contractor totals.

Refresh it every 30–60 s.

**Users**

- `GET /api/admin/users?role=&status=&is_contractor=&q=&joined_from=&joined_to=` lists users.
- `GET /api/admin/users/{id}` returns detail.
- `POST /api/admin/users/{id}/suspend` with `{reason}` suspends a user.
  - The user is signed out on their next call and receives no offers.
  - Admin accounts are managed in §1.5, not here.
- `POST /api/admin/users/{id}/reactivate` reactivates a user.
- `GET /api/admin/users/{id}/notifications` shows what was sent to that user, with push status. Useful for support.

**Properties**

- `GET /api/admin/properties?owner_id=&state=&is_active=&q=` and `/{id}`.
- Read-only.

**Bookings**

- `GET /api/admin/bookings?status=&dispatch_status=&customer_id=&contractor_id=&created_from=&created_to=&scheduled_from=&scheduled_to=&q=` lists bookings. `q` also matches `public_reference`.
- `GET /api/admin/bookings/{id}` returns full detail: customer, property, services with frozen prices, quote, dispatch offers with distances, payment, job and payout.
- `POST /api/admin/bookings/{id}/cancel` with `{"reason": "…"}` (required). It follows the same rule as the customer:
  - allowed only while the booking is `PENDING` and nothing is charged;
  - otherwise `409 booking_not_cancellable` or `409 cancellation_requires_support`;
  - refunds are not implemented yet.

**Booking statuses**

| Field | Values |
| --- | --- |
| `status` | `PENDING, CONFIRMED, CANCELLED` |
| `dispatch_status` | `SEARCHING, NO_CONTRACTOR, ASSIGNED` |
| Offer `status` | `PENDING, ACCEPTED_PENDING_PAYMENT, ACCEPTED, DECLINED, EXPIRED` |

`NO_CONTRACTOR` bookings are worth a filter on the dashboard: nobody was found within 50 km.

**Jobs**

- `GET /api/admin/jobs?status=&contractor_id=&created_from=&created_to=` and `/{id}`. The detail includes the photos.
- Statuses: `ASSIGNED, ARRIVED, IN_PROGRESS, AWAITING_CUSTOMER_CONFIRMATION, COMPLETED`.

**Payments**

- `GET /api/admin/payments?status=&booking_id=&needs_reconciliation=&created_from=&created_to=` and `/{id}`.
- Statuses: `NOT_CHARGED, PENDING, PROCESSING, REQUIRES_ACTION, SUCCEEDED, FAILED, REFUNDED`.
- `needs_reconciliation=true` lists payments whose provider call failed with an unknown outcome.
- **Superuser only:** `POST /api/admin/payments/{id}/reconcile`

  ```json
  {"outcome": "SUCCEEDED" | "FAILED", "provider_reference": "…", "note": "what you checked"}
  ```

  - `SUCCEEDED` needs `provider_reference`, marks the payment paid, and confirms the booking. If the offer is no longer reserved, `booking_confirmed` is `false`.
  - `FAILED` lets the customer retry.
  - The provider is **not** called. You record what you verified in the provider's own dashboard.

**Payouts**

- `GET /api/admin/payouts?status=&contractor_id=&needs_reconciliation=&created_from=&created_to=` and `/{id}`.
- Statuses: `PENDING, SUCCEEDED, FAILED`.
- **Superuser only:** `POST /api/admin/payouts/{id}/reconcile`, same body and meaning as for payments.
- A payout is created only after the customer confirms the job **and** the customer's payment succeeded.

**Contractors and verification**

| Call | Purpose |
| --- | --- |
| `GET /api/admin/contractors` | List contractors. |
| `GET /api/admin/contractors/{profile_id}` | Profile. |
| `GET /api/admin/contractors/{profile_id}/verifications` | All submissions. |
| `GET /api/admin/verifications/pending` | The review queue. |
| `PATCH /api/admin/business-registration/{id}` | Review an ABN submission. |
| `PATCH /api/admin/insurance/{id}` | Review an insurance submission. |

Review body: `{"status": "VERIFIED" | "REJECTED", "rejection_reason": "…"}`. A reason is required when rejecting.

- The contractor receives a push (`verification.approved` / `verification.rejected`).
- A contractor receives offers only with a verified ABN **and** valid, unexpired insurance.

**Service catalog and pricing**

- `GET/POST /api/admin/services`, `GET/PATCH/DELETE /api/admin/services/{id}`.
  - Body: `{name, description, room_price, base_price, is_active}`.
  - Price per selection = `room_price × room_count + base_price`.
  - An add-on is a service with `room_price = 0`.
  - `DELETE` is a soft delete (`is_active = false`). Existing bookings keep their frozen prices.
- `GET/PATCH /api/admin/pricing-config` with `{"price_per_km": "…"}` sets the travel price.
  - Changes apply to **new** quotes and offers only.
  - Prices already offered stay frozen.

**Support**

- `GET /api/admin/support-requests?status=&category=&user_id=&booking_id=&created_from=&created_to=&q=` and `/{id}`.
- `PATCH /api/admin/support-requests/{id}` with `{"status": "UNDER_REVIEW" | "RESOLVED"}`.
- Allowed transitions: `SUBMITTED → UNDER_REVIEW`, `SUBMITTED → RESOLVED`, `UNDER_REVIEW → RESOLVED`. `RESOLVED` is final.
- The user receives a `support.updated` push.
- There is no reply thread yet. Contact the user by phone or email from their profile.
- Categories: `BOOKING_ISSUE, PAYMENT_ISSUE, CONTRACTOR_ISSUE, APP_ISSUE, OTHER`.

**Broadcasts**

- `POST /api/admin/broadcasts` with `{target, title, body}`.
  - `target`: `CUSTOMERS | CONTRACTORS | ALL_USERS`.
  - Title: 120 characters maximum. Body: 500 characters maximum.
  - Sent as push and stored in each user's in-app inbox, in English.
  - It cannot be recalled, so ask for confirmation before sending.
- `GET /api/admin/broadcasts` and `/{id}` show history and delivery counts.

**Audit log (superuser only)**

- `GET /api/admin/audit-log?actor_id=&action=&target_type=&target_id=&from=&to=&limit=&offset=`.
- Read-only.

### 2.3 Operational notes

- **Periodic jobs.** A scheduler runs every 5 minutes. It expires offers, retries dispatch, repairs confirmations, retries push, and purges notifications older than 90 days.
  - Expect up to about 5 minutes of delay on anything time-based.
  - A booking in `SEARCHING` is not stuck unless it stays there well beyond that.
- **Test-phase providers.** Payments, payouts, photo storage and directions run on test adapters, so every charge succeeds and no real money moves. `method_summary` and `provider_reference` hold test values.
- **Health.** `GET /api/health` (liveness) and `/api/health/ready` (database and migrations) need no token.

---

## Part 3 — Endpoint and schema reference (generated)

Generated from `openapi.json`, admin endpoints only. Token refresh and logout are the shared `/api/auth/token/refresh` and `/api/auth/logout` (§1.2).

### Admin — Auth

#### `POST /api/admin/auth/login`

**Admin login — step 1: email (or phone) and password** — public (no token).

Request body (`application/json`): `AdminLoginIn`

Responses: `200` → `AdminLoginOut`, `400` → `ErrorOut`, `401` → `ErrorOut`, `403` → `ErrorOut`, `409` → `ErrorOut`, `429` → `ErrorOut`, `503` → `ErrorOut`

#### `POST /api/admin/auth/verify`

**Admin login — step 2: SMS code** — public (no token).

Request body (`application/json`): `AdminVerifyIn`

Responses: `200` → `AdminVerifyOut`, `400` → `ErrorOut`, `401` → `ErrorOut`, `403` → `ErrorOut`, `409` → `ErrorOut`, `429` → `ErrorOut`, `503` → `ErrorOut`

#### `POST /api/admin/auth/resend`

**Admin login — resend the SMS code** — public (no token).

Request body (`application/json`): `AdminResendIn`

Responses: `200` → `AdminLoginOut`, `400` → `ErrorOut`, `401` → `ErrorOut`, `403` → `ErrorOut`, `409` → `ErrorOut`, `429` → `ErrorOut`, `503` → `ErrorOut`

#### `GET /api/admin/auth/me`

**Current administrator** — Bearer token.

Responses: `200` → `AdminMeOut`

#### `POST /api/admin/auth/password`

**Change own password** — Bearer token.

Request body (`application/json`): `PasswordChangeIn`

Responses: `200` → `TokenPairOut`, `400` → `ErrorOut`, `422` → `ErrorOut`

#### `GET /api/admin/auth/devices`

**List own trusted devices** — Bearer token.

Responses: `200` → `array of TrustedDeviceOut`

#### `DELETE /api/admin/auth/devices/{device_id}`

**Revoke one of own trusted devices** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `device_id` | path | string (uuid) | yes |  |

Responses: `204`, `404` → `ErrorOut`

### Admin — Accounts

#### `GET /api/admin/admins`

**List administrator accounts (superuser only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `status` | query | string |  |  |
| `limit` | query | integer |  | default `50`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |

Responses: `200` → `AdminAccountListOut`

#### `POST /api/admin/admins`

**Create an administrator (superuser only)** — Bearer token.

Request body (`application/json`): `AdminAccountCreateIn`

Responses: `201` → `AdminAccountCreatedOut`, `409` → `ErrorOut`

#### `GET /api/admin/admins/{admin_id}`

**Retrieve an administrator (superuser only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `admin_id` | path | string (uuid) | yes |  |

Responses: `200` → `AdminAccountOut`, `404` → `ErrorOut`

#### `PATCH /api/admin/admins/{admin_id}`

**Update an administrator (superuser only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `admin_id` | path | string (uuid) | yes |  |

Request body (`application/json`): `AdminAccountPatch`

Responses: `200` → `AdminAccountOut`, `404` → `ErrorOut`, `409` → `ErrorOut`

#### `POST /api/admin/admins/{admin_id}/reset-password`

**Reset an administrator's password (superuser only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `admin_id` | path | string (uuid) | yes |  |

Responses: `200` → `AdminAccountCreatedOut`, `404` → `ErrorOut`

#### `POST /api/admin/admins/{admin_id}/revoke-sessions`

**Sign an administrator out everywhere (superuser only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `admin_id` | path | string (uuid) | yes |  |

Responses: `200` → `AdminAccountOut`, `404` → `ErrorOut`

### Admin — Dashboard

#### `GET /api/admin/dashboard/summary`

**Back-office dashboard counters (admin only)** — Bearer token.

Responses: `200` → `DashboardSummaryOut`, `403` → `ErrorOut`

### Admin — Users

#### `GET /api/admin/users`

**List user accounts (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `limit` | query | integer |  | default `50`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |
| `role` | query | `ConfirmedRole` \| null |  |  |
| `status` | query | `UserStatus` \| null |  |  |
| `is_contractor` | query | boolean \| null |  |  |
| `q` | query | string \| null |  | max len 255 |
| `joined_from` | query | string (date) \| null |  |  |
| `joined_to` | query | string (date) \| null |  |  |

Responses: `200` → `AdminUserListOut`, `403` → `ErrorOut`

#### `GET /api/admin/users/{user_id}`

**Retrieve a user account (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `user_id` | path | string (uuid) | yes |  |

Responses: `200` → `AdminUserDetailOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `POST /api/admin/users/{user_id}/suspend`

**Suspend a user account (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `user_id` | path | string (uuid) | yes |  |

Request body (`application/json`): `SuspendIn`

Responses: `200` → `AdminUserDetailOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`

#### `POST /api/admin/users/{user_id}/reactivate`

**Reactivate a suspended user account (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `user_id` | path | string (uuid) | yes |  |

Responses: `200` → `AdminUserDetailOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`

### Admin — Properties

#### `GET /api/admin/properties`

**List properties (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `limit` | query | integer |  | default `50`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |
| `owner_id` | query | string (uuid) \| null |  |  |
| `state` | query | `AustralianState` \| null |  |  |
| `is_active` | query | boolean \| null |  |  |
| `q` | query | string \| null |  | max len 255 |

Responses: `200` → `AdminPropertyListOut`, `403` → `ErrorOut`

#### `GET /api/admin/properties/{property_id}`

**Retrieve a property (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `property_id` | path | string (uuid) | yes |  |

Responses: `200` → `AdminPropertyDetailOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

### Admin — Bookings

#### `GET /api/admin/bookings`

**List bookings (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `limit` | query | integer |  | default `50`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |
| `created_from` | query | string (date) \| null |  |  |
| `created_to` | query | string (date) \| null |  |  |
| `status` | query | `BookingStatus` \| null |  |  |
| `dispatch_status` | query | `DispatchStatus` \| null |  |  |
| `customer_id` | query | string (uuid) \| null |  |  |
| `contractor_id` | query | string (uuid) \| null |  |  |
| `scheduled_from` | query | string (date) \| null |  |  |
| `scheduled_to` | query | string (date) \| null |  |  |
| `q` | query | string \| null |  | max len 32 |

Responses: `200` → `AdminBookingListOut`, `403` → `ErrorOut`

#### `GET /api/admin/bookings/{booking_id}`

**Retrieve a booking with its full history (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `booking_id` | path | string (uuid) | yes |  |

Responses: `200` → `AdminBookingDetailOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `POST /api/admin/bookings/{booking_id}/cancel`

**Cancel an unpaid booking on the customer's behalf (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `booking_id` | path | string (uuid) | yes |  |

Request body (`application/json`): `AdminCancelIn`

Responses: `200` → `AdminBookingDetailOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`

### Admin — Jobs

#### `GET /api/admin/jobs`

**List jobs (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `limit` | query | integer |  | default `50`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |
| `created_from` | query | string (date) \| null |  |  |
| `created_to` | query | string (date) \| null |  |  |
| `status` | query | `JobStatus` \| null |  |  |
| `contractor_id` | query | string (uuid) \| null |  |  |

Responses: `200` → `AdminJobListOut`, `403` → `ErrorOut`

#### `GET /api/admin/jobs/{job_id}`

**Retrieve a job with its photos (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `job_id` | path | string (uuid) | yes |  |

Responses: `200` → `AdminJobDetailOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

### Admin — Payments

#### `GET /api/admin/payments`

**List customer payments (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `limit` | query | integer |  | default `50`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |
| `created_from` | query | string (date) \| null |  |  |
| `created_to` | query | string (date) \| null |  |  |
| `status` | query | `PaymentStatus` \| null |  |  |
| `booking_id` | query | string (uuid) \| null |  |  |
| `needs_reconciliation` | query | boolean \| null |  |  |

Responses: `200` → `AdminPaymentListOut`, `403` → `ErrorOut`

#### `GET /api/admin/payments/{payment_id}`

**Retrieve a customer payment (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `payment_id` | path | string (uuid) | yes |  |

Responses: `200` → `AdminPaymentDetailOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `POST /api/admin/payments/{payment_id}/reconcile`

**Record the provider outcome of an unknown-outcome payment (superuser only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `payment_id` | path | string (uuid) | yes |  |

Request body (`application/json`): `ReconcileIn`

Responses: `200` → `PaymentReconcileOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

### Admin — Payouts

#### `GET /api/admin/payouts`

**List contractor payouts (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `limit` | query | integer |  | default `50`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |
| `created_from` | query | string (date) \| null |  |  |
| `created_to` | query | string (date) \| null |  |  |
| `status` | query | `PayoutStatus` \| null |  |  |
| `contractor_id` | query | string (uuid) \| null |  |  |
| `needs_reconciliation` | query | boolean \| null |  |  |

Responses: `200` → `AdminPayoutListOut`, `403` → `ErrorOut`

#### `GET /api/admin/payouts/{payout_id}`

**Retrieve a contractor payout (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `payout_id` | path | string (uuid) | yes |  |

Responses: `200` → `AdminPayoutOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `POST /api/admin/payouts/{payout_id}/reconcile`

**Record the provider outcome of an unknown-outcome payout (superuser only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `payout_id` | path | string (uuid) | yes |  |

Request body (`application/json`): `ReconcileIn`

Responses: `200` → `AdminPayoutOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

### Admin — Contractors

#### `GET /api/admin/contractors`

**List all contractor profiles (admin only)** — Bearer token.

Responses: `200` → `array of ContractorProfileOut`, `403` → `ErrorOut`

#### `GET /api/admin/contractors/{profile_id}`

**Retrieve any contractor profile (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `profile_id` | path | string (uuid) | yes |  |

Responses: `200` → `ContractorProfileOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `GET /api/admin/contractors/{profile_id}/verifications`

**Full verification history of one contractor (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `profile_id` | path | string (uuid) | yes |  |

Responses: `200` → `ContractorVerificationsOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `GET /api/admin/verifications/pending`

**List all pending verifications (admin only)** — Bearer token.

Responses: `200` → `PendingVerificationsOut`, `403` → `ErrorOut`

#### `PATCH /api/admin/business-registration/{registration_id}`

**Approve or reject a business registration (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `registration_id` | path | string (uuid) | yes |  |

Request body (`application/json`): `ReviewPatch`

Responses: `200` → `BusinessRegistrationOut`, `400` → `ErrorOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

#### `PATCH /api/admin/insurance/{document_id}`

**Approve or reject an insurance document (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `document_id` | path | string (uuid) | yes |  |

Request body (`application/json`): `ReviewPatch`

Responses: `200` → `InsuranceDocumentOut`, `400` → `ErrorOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`, `422` → `ErrorOut`

### Admin — Service Catalog

#### `POST /api/admin/services`

**Create a service type (admin only)** — Bearer token.

Request body (`application/json`): `ServiceTypeIn`

Responses: `201` → `ServiceTypeOut`, `403` → `ErrorOut`, `422` → `ErrorOut`

#### `GET /api/admin/services`

**List all service types, active and inactive (admin only)** — Bearer token.

Responses: `200` → `array of ServiceTypeOut`, `403` → `ErrorOut`

#### `GET /api/admin/services/{service_id}`

**Retrieve one service type (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `service_id` | path | string (uuid) | yes |  |

Responses: `200` → `ServiceTypeOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `PATCH /api/admin/services/{service_id}`

**Update a service type, prices included (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `service_id` | path | string (uuid) | yes |  |

Request body (`application/json`): `ServiceTypePatch`

Responses: `200` → `ServiceTypeOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `422` → `ErrorOut`

#### `DELETE /api/admin/services/{service_id}`

**Soft-delete a service type (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `service_id` | path | string (uuid) | yes |  |

Responses: `200` → `ServiceTypeOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `GET /api/admin/pricing-config`

**Retrieve the global price_per_km (admin only)** — Bearer token.

Responses: `200` → `PricingConfigOut`, `403` → `ErrorOut`

#### `PATCH /api/admin/pricing-config`

**Update the global price_per_km (admin only)** — Bearer token.

Request body (`application/json`): `PricingConfigPatch`

Responses: `200` → `PricingConfigOut`, `403` → `ErrorOut`, `422` → `ErrorOut`

### Admin — Support

#### `GET /api/admin/support-requests`

**List support requests (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `limit` | query | integer |  | default `50`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |
| `created_from` | query | string (date) \| null |  |  |
| `created_to` | query | string (date) \| null |  |  |
| `status` | query | `SupportStatus` \| null |  |  |
| `category` | query | `SupportCategory` \| null |  |  |
| `user_id` | query | string (uuid) \| null |  |  |
| `booking_id` | query | string (uuid) \| null |  |  |
| `q` | query | string \| null |  | max len 255 |

Responses: `200` → `AdminSupportRequestListOut`, `403` → `ErrorOut`

#### `GET /api/admin/support-requests/{request_id}`

**Retrieve a support request (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `request_id` | path | string (uuid) | yes |  |

Responses: `200` → `AdminSupportRequestOut`, `403` → `ErrorOut`, `404` → `ErrorOut`

#### `PATCH /api/admin/support-requests/{request_id}`

**Change a support request's status (admin only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `request_id` | path | string (uuid) | yes |  |

Request body (`application/json`): `SupportStatusPatch`

Responses: `200` → `AdminSupportRequestOut`, `403` → `ErrorOut`, `404` → `ErrorOut`, `409` → `ErrorOut`

### Admin — Notifications

#### `POST /api/admin/broadcasts`

**Send a broadcast notification** — Bearer token.

Request body (`application/json`): `BroadcastIn`

Responses: `201` → `BroadcastOut`, `422` → `ErrorOut`

#### `GET /api/admin/broadcasts`

**List broadcasts** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `limit` | query | integer |  | default `50`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |

Responses: `200` → `BroadcastListOut`

#### `GET /api/admin/broadcasts/{broadcast_id}`

**Broadcast detail with delivery stats** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `broadcast_id` | path | string (uuid) | yes |  |

Responses: `200` → `BroadcastOut`, `404` → `ErrorOut`

#### `GET /api/admin/users/{user_id}/notifications`

**A user's notifications with push delivery status** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `user_id` | path | string (uuid) | yes |  |
| `limit` | query | integer |  | default `50`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |

Responses: `200` → `AdminNotificationListOut`

### Admin — Audit log

#### `GET /api/admin/audit-log`

**Read the admin audit log (superuser only)** — Bearer token.

| Parameter | In | Type | Required | Notes |
| --- | --- | --- | --- | --- |
| `limit` | query | integer |  | default `50`; ≥ 1, ≤ 200 |
| `offset` | query | integer |  | default `0`; ≥ 0 |
| `actor_id` | query | string (uuid) \| null |  |  |
| `action` | query | string \| null |  | max len 64 |
| `target_type` | query | string \| null |  | max len 64 |
| `target_id` | query | string \| null |  | max len 64 |
| `from` | query | string (date) \| null |  |  |
| `to` | query | string (date) \| null |  |  |

Responses: `200` → `AuditEntryListOut`, `403` → `ErrorOut`

### Schemas

#### `AdminAccountCreateIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `email` | string | yes | max len 254 |
| `phone` | string | yes | min len 8, max len 32 |
| `full_name` | string |  | max len 255 |
| `is_superuser` | boolean |  | default `false` |

#### `AdminAccountCreatedOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `admin` | `AdminAccountOut` | yes |  |
| `temporary_password` | string | yes |  |

#### `AdminAccountListOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `count` | integer | yes |  |
| `items` | array of `AdminAccountOut` | yes |  |

#### `AdminAccountOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `email` | string \| null |  |  |
| `phone` | string | yes |  |
| `full_name` | string |  |  |
| `status` | string | yes |  |
| `is_superuser` | boolean | yes |  |
| `must_change_password` | boolean | yes |  |
| `password_changed_at` | string (date-time) \| null |  |  |
| `last_login` | string (date-time) \| null |  |  |
| `failed_login_attempts` | integer |  | default `0` |
| `locked_until` | string (date-time) \| null |  |  |
| `date_joined` | string (date-time) | yes |  |

#### `AdminAccountPatch`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `full_name` | string \| null |  | max len 255 |
| `email` | string \| null |  | max len 254 |
| `phone` | string \| null |  | max len 32 |
| `status` | string \| null |  |  |
| `is_superuser` | boolean \| null |  |  |

#### `AdminBookingDetailOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `public_reference` | string | yes |  |
| `status` | string | yes |  |
| `dispatch_status` | string | yes |  |
| `dispatch_round` | integer | yes |  |
| `last_dispatch_attempt_at` | string (date-time) \| null |  |  |
| `customer` | `PersonSummaryOut` | yes |  |
| `property` | `PropertySummaryOut` | yes |  |
| `service_lines` | array of `ServiceLineOut` | yes |  |
| `quote` | `QuoteSummaryOut` \| null |  |  |
| `max_total` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `computed_price` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `pricing_version` | integer \| null |  |  |
| `currency` | string | yes |  |
| `access_notes` | string | yes |  |
| `scheduled_at` | string (date-time) \| null |  |  |
| `customer_timezone` | string | yes |  |
| `requested_at` | string (date-time) | yes |  |
| `assigned_contractor` | `ContractorSummaryOut` \| null |  |  |
| `dispatch_offers` | array of `DispatchOfferOut` | yes |  |
| `payment` | `PaymentSummaryOut` \| null |  |  |
| `job` | `JobSummaryOut` \| null |  |  |
| `payout` | `PayoutSummaryOut` \| null |  |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `AdminBookingListOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `count` | integer | yes |  |
| `items` | array of `AdminBookingOut` | yes |  |

#### `AdminCancelIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `reason` | string | yes | min len 1, max len 255 |

#### `AdminJobDetailOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `booking_id` | string (uuid) | yes |  |
| `public_reference` | string | yes |  |
| `customer_id` | string (uuid) | yes |  |
| `contractor` | `ContractorSummaryOut` \| null |  |  |
| `status` | string | yes |  |
| `arrived_at` | string (date-time) \| null |  |  |
| `started_at` | string (date-time) \| null |  |  |
| `marked_done_at` | string (date-time) \| null |  |  |
| `confirmed_at` | string (date-time) \| null |  |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |
| `photos` | array of `AdminJobPhotoOut` | yes |  |

#### `AdminJobListOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `count` | integer | yes |  |
| `items` | array of `AdminJobOut` | yes |  |

#### `AdminLoginIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `identifier` | string | yes | min len 3, max len 254 |
| `password` | string | yes | min len 1, max len 256 |
| `device_token` | string \| null |  | max len 128 |

#### `AdminLoginOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `state` | string | yes |  |
| `tokens` | `TokenPairOut` \| null |  |  |
| `challenge_id` | string (uuid) \| null |  |  |
| `phone_hint` | string |  |  |
| `expires_in_seconds` | integer \| null |  |  |

#### `AdminMeOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `email` | string \| null |  |  |
| `phone` | string | yes |  |
| `full_name` | string |  |  |
| `status` | string | yes |  |
| `is_superuser` | boolean | yes |  |
| `must_change_password` | boolean | yes |  |
| `password_changed_at` | string (date-time) \| null |  |  |
| `last_login` | string (date-time) \| null |  |  |

#### `AdminNotificationListOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `count` | integer | yes |  |
| `items` | array of `AdminNotificationOut` | yes |  |

#### `AdminPaymentDetailOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `booking_id` | string (uuid) | yes |  |
| `public_reference` | string | yes |  |
| `customer_id` | string (uuid) | yes |  |
| `amount` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `method` | string | yes |  |
| `status` | string | yes |  |
| `attempt_number` | integer | yes |  |
| `provider_reference` | string \| null |  |  |
| `provider_error_code` | string | yes |  |
| `failure_reason` | string \| null |  |  |
| `needs_reconciliation` | boolean | yes |  |
| `paid_at` | string (date-time) \| null |  |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |
| `method_summary` | object \| null |  |  |
| `has_pending_action` | boolean | yes |  |

#### `AdminPaymentListOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `count` | integer | yes |  |
| `items` | array of `AdminPaymentOut` | yes |  |

#### `AdminPayoutListOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `count` | integer | yes |  |
| `items` | array of `AdminPayoutOut` | yes |  |

#### `AdminPayoutOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `booking_id` | string (uuid) | yes |  |
| `public_reference` | string | yes |  |
| `contractor_user_id` | string (uuid) | yes |  |
| `contractor_phone` | string | yes |  |
| `amount` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `status` | string | yes |  |
| `provider_reference` | string \| null |  |  |
| `failure_reason` | string \| null |  |  |
| `needs_reconciliation` | boolean | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `AdminPropertyDetailOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `owner_id` | string (uuid) | yes |  |
| `owner_phone` | string | yes |  |
| `owner_name` | string | yes |  |
| `label` | string | yes |  |
| `property_type` | string | yes |  |
| `is_active` | boolean | yes |  |
| `address` | `AdminAddressOut` \| null |  |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |
| `bookings_count` | integer | yes |  |

#### `AdminPropertyListOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `count` | integer | yes |  |
| `items` | array of `AdminPropertyOut` | yes |  |

#### `AdminResendIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `challenge_id` | string (uuid) | yes |  |

#### `AdminSupportRequestListOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `count` | integer | yes |  |
| `items` | array of `AdminSupportRequestOut` | yes |  |

#### `AdminSupportRequestOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `user_id` | string (uuid) | yes |  |
| `user_phone` | string | yes |  |
| `user_name` | string | yes |  |
| `booking_id` | string (uuid) \| null |  |  |
| `booking_reference` | string \| null |  |  |
| `category` | string | yes |  |
| `message` | string | yes |  |
| `status` | string | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `AdminUserDetailOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `phone` | string | yes |  |
| `email` | string \| null |  |  |
| `email_verified` | boolean | yes |  |
| `full_name` | string | yes |  |
| `role` | string | yes |  |
| `roles` | array of string | yes |  |
| `is_contractor` | boolean | yes |  |
| `status` | string | yes |  |
| `is_active` | boolean | yes |  |
| `is_superuser` | boolean | yes |  |
| `date_joined` | string (date-time) | yes |  |
| `last_login` | string (date-time) \| null |  |  |
| `contractor_status` | string | yes |  |
| `contractor_profile_id` | string (uuid) \| null |  |  |
| `bookings_count` | integer | yes |  |
| `properties_count` | integer | yes |  |
| `social_providers` | array of string | yes |  |
| `must_change_password` | boolean | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `AdminUserListOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `count` | integer | yes |  |
| `items` | array of `AdminUserOut` | yes |  |

#### `AdminVerifyIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `challenge_id` | string (uuid) | yes |  |
| `code` | string | yes | min len 4, max len 12 |
| `remember_device` | boolean |  | default `false` |
| `device_label` | string |  | max len 255 |

#### `AdminVerifyOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `tokens` | `TokenPairOut` | yes |  |
| `device_token` | string \| null |  |  |
| `device_expires_in_days` | integer \| null |  |  |
| `must_change_password` | boolean |  | default `false` |

#### `AuditEntryListOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `count` | integer | yes |  |
| `items` | array of `AuditEntryOut` | yes |  |

#### `BroadcastIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `target` | string | yes | CUSTOMERS | CONTRACTORS | ALL_USERS |
| `title` | string | yes | min len 1, max len 120 |
| `body` | string | yes | min len 1, max len 500 |

#### `BroadcastListOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `count` | integer | yes |  |
| `items` | array of `BroadcastOut` | yes |  |

#### `BroadcastOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `target` | string | yes |  |
| `title` | string | yes |  |
| `body` | string | yes |  |
| `recipients_count` | integer | yes |  |
| `created_by_id` | string (uuid) \| null |  |  |
| `created_at` | string (date-time) | yes |  |
| `delivery` | object |  |  |

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

#### `ContractorVerificationsOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `contractor_id` | string (uuid) | yes |  |
| `eligible` | boolean | yes |  |
| `business_registrations` | array of `BusinessRegistrationOut` | yes |  |
| `insurance_documents` | array of `InsuranceDocumentOut` | yes |  |

#### `DashboardSummaryOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `generated_at` | string (date-time) | yes |  |
| `bookings` | `BookingCountsOut` | yes |  |
| `pending_verifications` | `PendingVerificationsCountOut` | yes |  |
| `open_support_requests` | integer | yes |  |
| `payments` | `PaymentCountsOut` | yes |  |
| `payouts` | `PayoutCountsOut` | yes |  |
| `gross_revenue` | `RevenueOut` | yes |  |
| `contractors_available_now` | integer | yes |  |
| `total_customers` | integer | yes |  |
| `total_contractors` | integer | yes |  |

#### `ErrorOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `code` | string | yes |  |
| `detail` | string | yes |  |

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

#### `PasswordChangeIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `current_password` | string | yes | min len 1, max len 256 |
| `new_password` | string | yes | min len 1, max len 256 |

#### `PaymentReconcileOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `booking_id` | string (uuid) | yes |  |
| `public_reference` | string | yes |  |
| `customer_id` | string (uuid) | yes |  |
| `amount` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `method` | string | yes |  |
| `status` | string | yes |  |
| `attempt_number` | integer | yes |  |
| `provider_reference` | string \| null |  |  |
| `provider_error_code` | string | yes |  |
| `failure_reason` | string \| null |  |  |
| `needs_reconciliation` | boolean | yes |  |
| `paid_at` | string (date-time) \| null |  |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |
| `method_summary` | object \| null |  |  |
| `has_pending_action` | boolean | yes |  |
| `booking_confirmed` | boolean | yes |  |

#### `PendingVerificationsOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `business_registrations` | array of `BusinessRegistrationOut` | yes |  |
| `insurance_documents` | array of `InsuranceDocumentOut` | yes |  |

#### `PricingConfigOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `price_per_km` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `updated_at` | string (date-time) | yes |  |

#### `PricingConfigPatch`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `price_per_km` | number \| string | yes | ≥ 0.0 |

#### `ReconcileIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `outcome` | `ReconcileOutcome` | yes |  |
| `provider_reference` | string \| null |  | max len 255 |
| `note` | string | yes | min len 1, max len 1000, pattern \S |

#### `ReviewPatch`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `status` | `VerificationStatus` | yes |  |
| `rejection_reason` | string \| null |  |  |

#### `ServiceTypeIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | yes | min len 1, max len 120 |
| `description` | string |  |  |
| `room_price` | number \| string | yes | ≥ 0.0 |
| `base_price` | number \| string | yes | ≥ 0.0 |
| `is_active` | boolean |  | default `true` |

#### `ServiceTypeOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `name` | string | yes |  |
| `description` | string | yes |  |
| `room_price` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `base_price` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `is_active` | boolean | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `ServiceTypePatch`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string \| null |  | min len 1, max len 120 |
| `description` | string \| null |  |  |
| `room_price` | number \| string \| null |  | ≥ 0.0 |
| `base_price` | number \| string \| null |  | ≥ 0.0 |
| `is_active` | boolean \| null |  |  |

#### `SupportStatusPatch`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `status` | `SupportStatus` | yes |  |

#### `SuspendIn`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `reason` | string | yes | min len 1, max len 1000, pattern \S |

#### `TokenPairOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `access` | string | yes |  |
| `refresh` | string | yes |  |

#### `TrustedDeviceOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `label` | string |  |  |
| `ip_address` | string \| null |  |  |
| `created_at` | string (date-time) | yes |  |
| `last_used_at` | string (date-time) \| null |  |  |
| `expires_at` | string (date-time) | yes |  |

#### `AdminAddressOut`

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

#### `AdminBookingOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `public_reference` | string | yes |  |
| `status` | string | yes |  |
| `dispatch_status` | string | yes |  |
| `customer_id` | string (uuid) | yes |  |
| `customer_phone` | string | yes |  |
| `property_id` | string (uuid) | yes |  |
| `suburb` | string \| null |  |  |
| `state` | string \| null |  |  |
| `assigned_contractor` | `ContractorSummaryOut` \| null |  |  |
| `max_total` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `computed_price` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `currency` | string | yes |  |
| `scheduled_at` | string (date-time) \| null |  |  |
| `requested_at` | string (date-time) | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `AdminJobOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `booking_id` | string (uuid) | yes |  |
| `public_reference` | string | yes |  |
| `customer_id` | string (uuid) | yes |  |
| `contractor` | `ContractorSummaryOut` \| null |  |  |
| `status` | string | yes |  |
| `arrived_at` | string (date-time) \| null |  |  |
| `started_at` | string (date-time) \| null |  |  |
| `marked_done_at` | string (date-time) \| null |  |  |
| `confirmed_at` | string (date-time) \| null |  |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `AdminJobPhotoOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `photo_type` | string | yes |  |
| `url` | string | yes |  |
| `storage_key` | string | yes |  |
| `uploaded_by_id` | string (uuid) | yes |  |
| `uploaded_at` | string (date-time) | yes |  |

#### `AdminNotificationOut`

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
| `push_status` | string | yes |  |
| `push_attempts` | integer | yes |  |
| `pushed_at` | string (date-time) \| null |  |  |
| `push_error` | string |  |  |

#### `AdminPaymentOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `booking_id` | string (uuid) | yes |  |
| `public_reference` | string | yes |  |
| `customer_id` | string (uuid) | yes |  |
| `amount` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `method` | string | yes |  |
| `status` | string | yes |  |
| `attempt_number` | integer | yes |  |
| `provider_reference` | string \| null |  |  |
| `provider_error_code` | string | yes |  |
| `failure_reason` | string \| null |  |  |
| `needs_reconciliation` | boolean | yes |  |
| `paid_at` | string (date-time) \| null |  |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `AdminPropertyOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `owner_id` | string (uuid) | yes |  |
| `owner_phone` | string | yes |  |
| `owner_name` | string | yes |  |
| `label` | string | yes |  |
| `property_type` | string | yes |  |
| `is_active` | boolean | yes |  |
| `address` | `AdminAddressOut` \| null |  |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

#### `AdminUserOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `phone` | string | yes |  |
| `email` | string \| null |  |  |
| `email_verified` | boolean | yes |  |
| `full_name` | string | yes |  |
| `role` | string | yes |  |
| `roles` | array of string | yes |  |
| `is_contractor` | boolean | yes |  |
| `status` | string | yes |  |
| `is_active` | boolean | yes |  |
| `is_superuser` | boolean | yes |  |
| `date_joined` | string (date-time) | yes |  |
| `last_login` | string (date-time) \| null |  |  |

#### `AuditEntryOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `actor_id` | string (uuid) \| null |  |  |
| `actor_label` | string | yes |  |
| `action` | string | yes |  |
| `target_type` | string | yes |  |
| `target_id` | string \| null |  |  |
| `details` | any |  |  |
| `ip_address` | string \| null |  |  |
| `created_at` | string (date-time) | yes |  |

#### `BookingCountsOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `by_status` | object | yes |  |
| `by_dispatch_status` | object | yes |  |
| `created_today` | integer | yes |  |
| `created_last_7_days` | integer | yes |  |

#### `ContractorSummaryOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `profile_id` | string (uuid) | yes |  |
| `user_id` | string (uuid) | yes |  |
| `business_name` | string | yes |  |

#### `DispatchOfferOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `dispatch_round` | integer | yes |  |
| `contractor` | `ContractorSummaryOut` | yes |  |
| `status` | string | yes |  |
| `distance_km` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `distance_source` | string | yes |  |
| `eta_seconds` | integer \| null |  |  |
| `total_amount` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `contractor_earnings` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `travel_fee` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `services_total` | number \| string \| null |  | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `currency` | string | yes |  |
| `pricing_version` | integer \| null |  |  |
| `offered_at` | string (date-time) | yes |  |
| `responded_at` | string (date-time) \| null |  |  |
| `expires_at` | string (date-time) | yes |  |

#### `JobSummaryOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `status` | string | yes |  |
| `started_at` | string (date-time) \| null |  |  |
| `marked_done_at` | string (date-time) \| null |  |  |
| `confirmed_at` | string (date-time) \| null |  |  |
| `created_at` | string (date-time) | yes |  |

#### `PaymentCountsOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `needs_reconciliation` | integer | yes |  |
| `failed_last_30_days` | integer | yes |  |

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

#### `PayoutCountsOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `pending` | integer | yes |  |
| `failed` | integer | yes |  |
| `needs_reconciliation` | integer | yes |  |

#### `PayoutSummaryOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `status` | string | yes |  |
| `amount` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `contractor_user_id` | string (uuid) | yes |  |
| `provider_reference` | string \| null |  |  |
| `failure_reason` | string \| null |  |  |
| `created_at` | string (date-time) | yes |  |

#### `PendingVerificationsCountOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `business_registrations` | integer | yes |  |
| `insurance_documents` | integer | yes |  |
| `total` | integer | yes |  |

#### `PersonSummaryOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `phone` | string | yes |  |
| `email` | string \| null |  |  |
| `full_name` | string | yes |  |
| `status` | string | yes |  |

#### `PropertySummaryOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `label` | string | yes |  |
| `property_type` | string | yes |  |
| `is_active` | boolean | yes |  |
| `address` | `AdminAddressOut` \| null |  |  |

#### `QuoteSummaryOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string (uuid) | yes |  |
| `services_total` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `maximum_total` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `currency` | string | yes |  |
| `pricing_version` | integer | yes |  |
| `service_snapshot` | any |  |  |
| `expires_at` | string (date-time) | yes |  |
| `created_at` | string (date-time) | yes |  |

#### `ReconcileOutcome`

One of: `SUCCEEDED`, `FAILED`

#### `RevenueOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `today` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `last_7_days` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |
| `last_30_days` | number \| string | yes | pattern ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ |

#### `ServiceLineOut`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `service_type_id` | string (uuid) | yes |  |
| `service_name` | string | yes |  |
| `room_count` | integer | yes |  |

#### `SupportStatus`

One of: `SUBMITTED`, `UNDER_REVIEW`, `RESOLVED`

#### `VerificationStatus`

One of: `PENDING`, `VERIFIED`, `REJECTED`

