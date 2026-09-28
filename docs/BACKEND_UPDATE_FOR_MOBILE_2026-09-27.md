# Backend update for the mobile app — 2026-09-27

**For:** the mobile developer (customer and cleaner apps).
**Replies to:** "Cleano — open backend issues" (updated 2026-09-27).
**Full reference:** `docs/MOBILE_API_GUIDE.md`. It is updated with everything below, including the generated endpoint and schema reference. `ERROR_CODES.md` lists every error `code`.

> **What is live on production right now.** Production runs commit `1bd2240`. It includes:
> - the quote-booking fix (B16);
> - `close_reason` (B15);
> - `/arrive`, `/cancel` and `PUT /contractor/location`;
> - GPS rounding;
> - the 422 `validation_error` shape.
>
> **Coming with the next deploy** (commits `f7976ac` and `213b287`, already on `main`), marked 🚀 below:
> - account deletion;
> - dashboard-controlled service hours;
> - photo removal;
> - auto-confirm;
> - refunds;
> - ratings;
> - re-clean;
> - invoices.
>
> Check `/api/openapi.json` before wiring a 🚀 item; we will tell you when it is deployed.

---

## 1. Reply to your list

### 🔴 Blocking

| # | Status | Answer |
| --- | --- | --- |
| **B22** Photo upload | 🟡 **Test phase: switch it on. Launch: storage provider.** | Uploads work on production once the test switch `JOBS_ALLOW_FAKE_STORAGE_ADAPTER` is on. The PO is setting it. With the switch on, `mark-done` and the whole job flow can be tested. The fake storage **discards the bytes** and returns a placeholder `signed_url` (`https://fake-storage.local/…`), so photos do not display. Real storage (S3 or R2) is a provider decision before launch. The misleading note in `openapi.json` is fixed 🚀. |
| **B16** Booking from a quote | ✅ **Fixed and live** | Please re-test on a device: `POST /api/quotes`, then `POST /api/bookings` with `quote_id`. |
| **B23** Account deletion | ✅ 🚀 **Done** | See §2.1. |
| **B2** Real payment | ⏳ Provider not chosen | Keep sending Stripe's `pm_…` as `payment_method_reference`; the backend will charge it once the Stripe adapter exists. Nothing changes in the app contract. |
| **B20** 3-D Secure | ⏳ With B2 | `confirm-action` stays `501` until then. `PaymentActionOut.action_payload` is where the provider's next-action data (for example a `client_secret`) will arrive. |

### 🟠 Needed for launch

| # | Status | Answer |
| --- | --- | --- |
| **B4** Payout account | ⏳ With Stripe Connect | Keep W11 and step 5 as "not set up yet". |
| **B5** Document upload | ⏳ With storage | Keep the ABN, policy reference and expiry fields. |
| **D3** Customer never confirms | ✅ 🚀 **Done, exactly as the PO rule** | See §2.2. |
| **B19** Refund | ✅ 🚀 **Done** | See §2.3. There is no separate "processing" status: a refund is recorded once the provider accepts it. |
| **B18** Service hours | ✅ 🚀 **Done, and the rule changed** | The fixed 07:00–19:00 rule is **gone**. See §2.4. |
| **B3** Providers | 🟡 🚀 **Google done, Apple not yet** | **Google:** the server now verifies the Google ID token. Set `serverClientId` in `google_sign_in` to `461684012123-23e09b4q707qbjoknhc40jsvosojda1r.apps.googleusercontent.com` and send the **ID token** to `POST /api/auth/social/google` (details in `MOBILE_API_GUIDE.md` §2.1). **Apple:** not configured; `POST /api/auth/social/apple` returns `401 provider_not_configured`, so keep the Apple button hidden. SMS works for the test numbers only. |
| **B12** Price before an address | ⏳ PO decision pending | Unchanged: `POST /api/quotes` needs a saved property. |

### 🟡 Can follow

| # | Status | Answer |
| --- | --- | --- |
| **B8b / B25** Rating | ✅ 🚀 **Done, mandatory** | See §2.5. |
| **B8a** Re-clean | ✅ 🚀 **Done (72 h, admin decides, no limit)** | See §2.6. Photos on the request come with storage. |
| **B8c** Invoice | ✅ 🚀 **Done (JSON)** | See §2.7. PDF download comes later; render the screen from the JSON. No GST line. |
| **B21** Remove a photo | ✅ 🚀 **Done** | See §2.8. |
| **B8d** Support attachments | ⏳ With storage | |
| **C9** Customer ETA | ⏳ Needs a directions provider | |
| **C10** Cleaner name for the customer | ⏳ PO decision pending | |
| **B14** Unit / apartment | ✅ **Confirmed** | Keep prefixing it to `street_address` ("Apt 4, 24 Park St"). No separate field. |
| **B15** `close_reason` | ✅ **Live** | The field name is `close_reason`. It appears on every offer in `GET /api/contractor/offers` (with or without `include_closed`) and in `GET /api/contractor/offers/{id}`. Values: `DECLINED`, `TIMED_OUT`, `BOOKING_CANCELLED`, or `null` while open or accepted. Offers closed before the field existed were back-filled. |

### Admin dashboard

| # | Status | Answer |
| --- | --- | --- |
| **B17** "Up to" ≈ A$10,000 | 🟡 **Action on the PO** | The dashboard API can now set `maximum_travel_fee` and the other pricing values. The PO will set real values after the deploy. No app change is needed. |

---

## 2. New and changed API (🚀 unless noted)

### 2.1 Delete my account (B23)

1. **Confirmation screen:** `GET /api/auth/me/deletion` returns:

   ```json
   {"can_delete": false, "blockers": [{"reason": "active_booking", "booking_id": "…"}]}
   ```

   | `reason` | Meaning |
   | --- | --- |
   | `active_booking` | A confirmed clean is not finished. |
   | `payment_in_progress` | A payment is processing or succeeded on a request not yet assigned. |
   | `active_job` | The cleaner accepted or is doing a job. |
   | `payout_pending` | The cleaner has earnings not yet paid out. |

   **Unpaid requests do not block**; deleting cancels them.

2. **After the user confirms:** `POST /api/auth/me/delete` with `{"confirmation": "DELETE"}` returns **`204`**.

   | Error | When |
   | --- | --- |
   | `400 deletion_confirmation_required` | The phrase is missing or wrong. |
   | `409 account_deletion_blocked` | Something still blocks it. Show the blockers from step 1. |

3. **Effects:** permanent, and applied immediately.
   - Name, phone and email are erased.
   - Unpaid requests are cancelled.
   - A cleaner goes offline, and their open offers move to other cleaners.
   - Saved properties, devices, notifications and Apple/Google links are removed.
   - **All tokens stop working at once.**

   **In the app:** clear local storage and return to the welcome screen. The same phone number can later sign up as a brand-new account.

### 2.2 Automatic confirmation (D3)

| After `marked_done_at` | What happens |
| --- | --- |
| **6 hours** | One reminder push, `job.confirmation_reminder`. |
| **12 hours** | The job is confirmed automatically: status `COMPLETED`, `auto_confirmed: true`, payout released. The customer receives `job.auto_confirmed`. |

- `JobOut.auto_confirm_at` is the exact deadline while the job is `AWAITING_CUSTOMER_CONFIRMATION`, so the 12 h countdown comes from the server.
- It is `null` otherwise.

### 2.3 Refunds (B19)

- An admin refunds in full or in part.

  | Refund | `status` | `refunded_amount` |
  | --- | --- | --- |
  | Full | `REFUNDED` | The whole amount paid. |
  | Partial | stays `SUCCEEDED` | The total refunded so far. |

- `refunded_amount` and `refunded_at` are on `GET /api/bookings/{id}/payment`. `refunded_amount` is also on `BookingOut.payment`.
- Push `payment.refunded`, with data `booking_id`, `payment_id` and `amount`.
- Show "Your refund of A$X has been sent" whenever `refunded_amount > 0`, not only when the status is `REFUNDED`.

### 2.4 Service hours (B18) — rule changed

- **Default: no hours at all.** Cleaners can be requested at any time.
- The admin can switch hours on and set the window in the dashboard. The window is in property-local time and may run past midnight.
- `QuoteOut.service_hours` tells you the current rule for that property:

  ```json
  {"timezone": "Australia/Sydney", "enabled": true, "opens_at": "08:00", "closes_at": "17:30",
   "is_open_now": false, "next_open_at": "2026-09-28T22:00:00Z", "min_lead_minutes": 120}
  ```

  - With `enabled: false`, `opens_at` and `closes_at` are `null` and `is_open_now` is always `true`.
- While `is_open_now` is false, disable "Request now" and show the next opening from `next_open_at`.
- A scheduled visit (`scheduled_at`) must fall inside the window when hours are enabled, and be at least `min_lead_minutes` ahead.
- Otherwise the server still returns `400 outside_business_hours`.
- **Remove any hard-coded 07:00–19:00 check from the app.**

### 2.5 Rating — mandatory (B8b / B25)

- `POST /api/bookings/{id}/review` with `{"stars": 1-5, "comment": "optional"}` returns `201`.
  - Allowed once the job is `COMPLETED`, including auto-confirmed jobs; otherwise `409 review_not_allowed`.
  - Allowed once per booking; otherwise `409 already_reviewed`. A rating cannot be edited.
- **Mandatory:** while a completed booking is unrated, `POST /api/bookings` returns **`409 review_required`**, and `detail` names the booking.
  - `BookingOut.review_required: true` marks the booking to rate.
  - `BookingOut.review` holds `{stars, comment, created_at}` once rated.
  - Suggested flow: prompt for the rating when the job completes, and again on "Request a clean" if one is pending.
- **Cleaner side:**
  - Push `review.received`.
  - `rating_average` and `rating_count` on `GET /api/contractor/profile`. `rating_average` is `null` before the first rating.
  - How ratings affect dispatch is not decided yet.

### 2.6 Re-clean guarantee — 72 hours (B8a)

- **Eligibility:** `BookingOut.reclean_eligible_until` is set when the job is completed and the booking includes a service the admin put under the guarantee (End of lease, for example). It is `null` otherwise. Show "Request by …" from it.
- **Request:** `POST /api/bookings/{id}/reclean-requests` returns `201`.

  ```json
  {"areas": ["KITCHEN", "BATHROOMS", "BEDROOMS", "LIVING_AREAS", "WINDOWS", "OTHER"], "details": "…"}
  ```

  | Error | When |
  | --- | --- |
  | `409 reclean_not_eligible` | The booking is not covered. |
  | `409 reclean_window_closed` | The 72 hours have passed. |
  | `409 reclean_already_open` | One request is already waiting for a decision. |
  | `422` | Invalid or empty `areas`. |

- **History:** `GET /api/bookings/{id}/reclean-requests` returns `status` (`SUBMITTED`, `APPROVED` or `REJECTED`) and `decision_note`.
- **Decision:** the admin decides and the customer receives push `reclean.updated` (data `reclean_request_id`, `status`). There is no limit on the number of requests.
- **Photos:** the design's "Add photos" waits for storage.

### 2.7 Invoice (B8c)

- `GET /api/bookings/{id}/invoice` is available once the payment has succeeded; otherwise `409 invoice_not_available`.

  ```json
  {"number": "INV-2026-000123", "issued_at": "…", "public_reference": "CLN-…",
   "service_date": "2026-09-27", "service_address": "24 Park St, Bondi NSW 2026",
   "lines": [{"description": "End of lease", "quantity": 3, "amount": "180.00"},
             {"description": "Travel", "quantity": 1, "amount": "12.00"}],
   "total": "192.00", "currency": "AUD", "gst_included": false,
   "payment": {"method": "CARD", "display_name": "Visa •••• 4242", "status": "SUCCEEDED",
               "paid_at": "…", "refunded_amount": "0.00"}}
  ```

- The number is sequential per year and never changes once issued.
- Lines use the frozen prices. A `Rounding` line appears only if needed, so the lines always add up to `total`.
- **PDF:** later. "Download PDF" and "Share" stay hidden for now.

### 2.8 Remove a photo (B21)

- `DELETE /api/contractor/jobs/{job_id}/photos/{photo_id}` returns `204`.
  - Allowed only while the job is `IN_PROGRESS`; `409 job_not_accepting_photos` after mark-done.
  - `404 photo_not_found` if there is no such photo on this job.
- To replace a photo, delete it and upload the new one.
- `mark-done` still needs at least one `BEFORE` and one `AFTER` photo.

### 2.9 New push types

Route by `data.type`. Create no new Android channels: all of these use `general`.

| `type` | To | Data ids |
| --- | --- | --- |
| `job.confirmation_reminder` | customer | `booking_id`, `job_id` |
| `job.auto_confirmed` | customer | `booking_id`, `job_id` |
| `payment.refunded` | customer | `booking_id`, `payment_id`, `amount` |
| `reclean.updated` | customer | `booking_id`, `reclean_request_id`, `status` |
| `review.received` | cleaner | `booking_id`, `review_id` |

### 2.10 New error codes

| Code | Status |
| --- | --- |
| `review_required` | 409 |
| `review_not_allowed` | 409 |
| `already_reviewed` | 409 |
| `reclean_not_eligible` | 409 |
| `reclean_window_closed` | 409 |
| `reclean_already_open` | 409 |
| `invalid_reclean_request` | 422 |
| `invoice_not_available` | 409 |
| `deletion_confirmation_required` | 400 |
| `account_deletion_blocked` | 409 |
| `admin_account_not_deletable` | 403 |
| `photo_not_found` | 404 |

All of them are in `ERROR_CODES.md`.

---

## 3. App checklist

**Now (live):**

1. Re-test booking from a quote (B16).
2. Read `close_reason` on offer open (B15).
3. Hide the Apple button (B3). For Google, set `serverClientId` = `461684012123-23e09b4q707qbjoknhc40jsvosojda1r.apps.googleusercontent.com` now; the Google button works after the next deploy.

**After the next deploy (🚀):**

4. Profile → Delete account (§2.1). This is required for App Store and Google Play.
5. Remove the local 07:00–19:00 check and use `QuoteOut.service_hours` (§2.4).
6. Show the auto-confirm countdown from `auto_confirm_at`, and handle the two new pushes (§2.2).
7. Build the rating screen and handle `409 review_required` on booking creation (§2.5).
8. Re-clean request screen from `reclean_eligible_until` (§2.6).
9. Invoice screen from `GET /invoice` (§2.7).
10. Show the refund message from `refunded_amount` (§2.3).
11. Add photo removal in W05 (§2.8).

**Still waiting on providers or product:** real payments and 3-D Secure, payout account, document and support uploads, customer ETA, cleaner name, price before the address.
