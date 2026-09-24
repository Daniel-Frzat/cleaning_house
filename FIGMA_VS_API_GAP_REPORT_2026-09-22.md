# Figma vs Real API Gap Report

**Updated:** 2026-09-22  
**Repository:** Cleaning House Django backend  
**Validation:** Django 5.2.17, Django Ninja 1.1.0, 965 tests passed

This report replaces the earlier gap report for the backend state verified on
2026-09-22. It distinguishes gaps closed in code from gaps that still require
an external provider or an explicit product/accounting decision.

## 1. Closed In This Pass

| Area | Backend change | API |
| --- | --- | --- |
| Pre-request maximum price | Exposed the existing frozen `BookingQuote` domain. Quotes validate property ownership, GPS serviceability, active services, pricing version, expiry and single use. | `POST /api/quotes` |
| Booking quote consumption | Booking creation accepts `quote_id`, copies the frozen ceiling/pricing metadata, and accepts an opaque `payment_method_reference`. | `POST /api/bookings` |
| Contractor offer discovery | Contractors can recover pending offers on any device. Results are scoped to the authenticated contractor and ordered by expiry. | `GET /api/contractor/offers` |
| Contractor offer content | Offers now expose frozen total, contractor earnings, currency, optional ETA, service names and a safe suburb/state summary. Access notes and contact data remain hidden before acceptance. | `GET /api/contractor/offers` |
| Contractor job recovery | Contractors can list assigned jobs after reinstall or on a second device. | `GET /api/contractor/jobs` |
| Contractor job summary | Job results include booking reference, service names, safe property summary, local scheduled time, earnings and payout status. | `GET /api/contractor/jobs` |
| Payment retry | Exposed the existing retry service for `FAILED` and `REQUIRES_ACTION` payments. Retry uses the frozen amount and an optional opaque provider-method reference. | `POST /api/bookings/payments/{payment_id}/retry` |
| Payment authentication | Exposed provider confirmation for `REQUIRES_ACTION`; customer-only, with action payload returned only to the booking owner. | `POST /api/bookings/payments/{payment_id}/confirm-action` |
| Contractor earnings | Added date-filtered payout items, paid/processing totals and completed-job count. | `GET /api/contractor/earnings?from_date=&to_date=` |
| Lifecycle documentation | Corrected acceptance documentation: a job starts `ASSIGNED`, then the contractor explicitly calls `/start` to move it to `IN_PROGRESS`. | OpenAPI and route descriptions |
| Contract synchronization | Regenerated the checked-in OpenAPI file and verified it with `manage.py export_openapi --check`. | `openapi.json` |

## 2. Existing Capabilities Confirmed

The following were already implemented and remain available:

- Booking `public_reference`.
- Booking `access_notes`.
- Booking reschedule for `PENDING + NO_CONTRACTOR`.
- Customer tracking with privacy window, stale-location metadata and no
  contractor location after work starts.
- Customer support requests linked to a booking.
- `ASSIGNED -> IN_PROGRESS -> AWAITING_CUSTOMER_CONFIRMATION -> COMPLETED`.
- Contractor onboarding, verification review and availability.
- Payment and payout read endpoints.
- Customer completion confirmation.
- Postcode/state validation and property serviceability warnings.

## 3. Remaining Backend Gaps

### 3.1 External provider configuration

These are implemented behind adapters but cannot work in production until
credentials and providers are selected/configured:

- SMS/OTP delivery.
- Apple and Google token verification.
- Payment capture and provider-side 3-D Secure confirmation.
- Contractor payout transfer.
- Photo storage and signed URLs.
- Directions/route provider for street geometry and ETA.

The backend must not simulate successful money movement in production. The
client must continue reading payment/payout records after the triggering
request.

### 3.2 Product or policy decisions

These should not be implemented by guessing:

- Cancellation after assignment, including refund rules, cutoff window and
  contractor reassignment.
- GST/tax treatment and whether tax must be exposed separately.
- Invoice numbering, line-item policy, PDF generation and document storage.
- Rating/review model and endpoint.
- Recurring cleans and discounts.
- 72-hour re-clean guarantee.
- Separate `ARRIVED` state before `IN_PROGRESS`.
- Automatic completion when a customer never confirms.
- Push notification triggers and payload contract.
- Saved payment methods and provider tokenization semantics.
- Support attachments, replies and ticket lifecycle.

### 3.3 Still useful future extensions

- Contractor/admin search and operational reconciliation lists.
- Route polyline and ETA once a directions provider is approved.
- Customer display of contractor before/after photos; the existing job response
  already contains signed photo URLs.
- Verification status presentation for pending/rejected contractor documents.
- Explicit service catalog classification for add-ons instead of client-side
  fixed-ID grouping.

## 4. Contract Notes For Client Developers

- Quote first, then create the booking with `quote_id`.
- A quote is short-lived and single-use; an expired or reused quote returns a
  conflict response.
- Offer earnings are frozen before acceptance and are not recalculated by the
  client.
- `payment_method_reference` is an opaque provider token. Never send PAN, CVC
  or raw card data to this API.
- `REQUIRES_ACTION` exposes a provider action payload only to the booking owner.
- A job list is scoped to the contractor's assigned jobs; it is not a global
  marketplace feed.
- `from_date` and `to_date` on earnings are calendar dates. If omitted, the
  endpoint returns the last seven days including today.
- Money remains JSON decimal strings and identifiers remain UUIDs.

## 5. Verification Record

Commands run from the backend repository:

```text
python manage.py check
python manage.py export_openapi --check
python -m pytest
```

Results:

- Django system check: passed.
- OpenAPI export check: passed.
- Test suite: **965 passed**.
- Known non-blocking warnings: Django Ninja/Pydantic deprecations and a
  Windows pytest cache-directory warning.
