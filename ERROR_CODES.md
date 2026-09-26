# API error codes

Every handled error response has the body `{"code": "...", "detail": "..."}` (auth endpoints may add `retry_after_seconds`; `validation_error` adds `errors[]`). `detail` is English and may change; **`code` is stable** — translate by `code`.

`401` from a missing or invalid token has no `code` (framework response: `{"detail": "Unauthorized"}`) — treat any `401` as "log in again" (or refresh the token first).

Generated from the source by `python manage.py export_error_codes` — do not edit by hand. A test fails when this file is out of date, so a new or renamed code always shows up in review.

**136 codes.**

| Code | HTTP | Meaning | Defined in |
| --- | --- | --- | --- |
| `account_inactive` | 403 |  | apps/accounts/services/admin_auth.py |
| `account_locked` | 429 |  | apps/accounts/services/admin_auth.py |
| `admin_account` | 403, 404, 503 |  | apps/accounts/services/backoffice_users.py |
| `admin_auth_error` | 400 |  | apps/accounts/services/admin_auth.py |
| `admin_login_required` | 403 |  | apps/accounts/api/auth.py |
| `admin_management_error` | 409 |  | apps/accounts/services/admin_auth.py |
| `admin_not_found` | 404 |  | apps/accounts/api/admin_auth.py |
| `admin_required` | 403, 404, 503 |  | apps/accounts/authentication.py, apps/audit/api/common.py, apps/audit/services/backoffice.py, apps/contractors/api/admin_contractors.py |
| `admin_role_required` | 403, 422 |  | apps/contractors/services/profile.py, apps/services/services/catalog.py |
| `already_reviewed` | 409 |  | apps/contractors/services/verification.py |
| `booking_error` | 400 |  | apps/bookings/services/bookings.py |
| `booking_forbidden` | 403 |  | apps/bookings/services/bookings.py |
| `booking_has_no_price` | 422 |  | apps/payments/services/payments.py, apps/payouts/services/payouts.py |
| `booking_not_cancellable` | 409 |  | apps/bookings/services/bookings.py |
| `booking_not_confirmed` | 400, 409 |  | apps/jobs/services/jobs.py, apps/payments/services/payments.py |
| `booking_not_found` | 404 |  | apps/bookings/api/bookings.py, apps/bookings/services/admin.py, apps/bookings/services/bookings.py, apps/support/api/support.py, apps/support/services/support.py |
| `booking_not_reschedulable` | 409 |  | apps/bookings/services/bookings.py |
| `broadcast_invalid` | 422 |  | apps/notifications/services/broadcasts.py |
| `cancellation_requires_support` | 409 |  | apps/bookings/services/bookings.py |
| `catalog_error` | 422 |  | apps/services/services/catalog.py |
| `challenge_invalid` | 400 |  | apps/accounts/services/admin_auth.py |
| `contractor_profile_error` | 422 |  | apps/contractors/services/profile.py |
| `contractor_profile_exists` | 409 |  | apps/contractors/services/profile.py |
| `contractor_profile_forbidden` | 403 |  | apps/contractors/services/profile.py |
| `contractor_profile_not_found` | 404 |  | apps/contractors/api/admin_contractors.py, apps/contractors/api/profile.py, apps/contractors/services/profile.py |
| `customer_payment_not_settled` | 403, 404, 503 |  | apps/payouts/services/payouts.py |
| `device_not_found` | 404 |  | apps/accounts/api/admin_auth.py |
| `document_expired` | 409, 422 |  | apps/contractors/services/verification.py |
| `email_already_used` | 409 |  | apps/accounts/services/identity.py |
| `empty_photo` | 400 |  | apps/jobs/services/photos.py |
| `empty_service_selection` | 400 |  | apps/bookings/services/bookings.py, apps/services/services/pricing.py |
| `inactive_service` | 400 |  | apps/bookings/services/bookings.py, apps/services/services/pricing.py |
| `inactive_user` | 403 |  | apps/accounts/services/identity.py, apps/accounts/services/social.py |
| `invalid_contractor_role` | 403 |  | apps/bookings/services/offers.py, apps/contractors/services/profile.py |
| `invalid_credentials` | 401 |  | apps/accounts/services/admin_auth.py |
| `invalid_customer_role` | 403 |  | apps/bookings/services/bookings.py |
| `invalid_date_range` | 400 |  | apps/payouts/api/payouts.py |
| `invalid_distance` | 403, 404, 503 |  | apps/services/services/pricing.py, apps/services/services/travel_pricing.py |
| `invalid_job_status` | 409 |  | apps/jobs/services/jobs.py |
| `invalid_location` | 400 |  | apps/contractors/services/location.py, apps/jobs/services/tracking.py |
| `invalid_owner_role` | 403 |  | apps/properties/api/properties.py, apps/properties/services/properties.py |
| `invalid_phone` | 400 |  | apps/accounts/services/otp.py |
| `invalid_photo_type` | 400 |  | apps/jobs/services/photos.py |
| `invalid_review_status` | 422 |  | apps/contractors/services/verification.py |
| `invalid_room_count` | 400 |  | apps/bookings/services/bookings.py, apps/services/services/pricing.py |
| `invalid_status_transition` | 403, 404, 409, 503 |  | apps/accounts/services/backoffice_users.py, apps/support/services/admin.py |
| `job_already_exists` | 400, 409 |  | apps/jobs/services/jobs.py |
| `job_error` | 400, 409 |  | apps/jobs/services/jobs.py |
| `job_forbidden` | 403, 404 |  | apps/jobs/services/jobs.py |
| `job_not_accepting_photos` | 409 |  | apps/jobs/services/photos.py |
| `job_not_completed` | 403, 404, 503 |  | apps/payouts/services/payouts.py |
| `job_not_found` | 404 |  | apps/jobs/api/jobs.py, apps/jobs/services/admin.py, apps/jobs/services/jobs.py |
| `location_error` | 422 |  | apps/contractors/services/location.py |
| `missing_proof_photos` | 400 |  | apps/jobs/services/jobs.py |
| `no_assigned_contractor` | 403, 404, 503 |  | apps/payouts/services/payouts.py |
| `not_at_property` | 409 |  | apps/jobs/services/jobs.py |
| `not_reconcilable` | 409 |  | apps/payments/services/admin.py, apps/payouts/services/admin.py |
| `notification_error` | 403, 404, 503 |  | apps/notifications/services/notifications.py |
| `notification_not_found` | 404 |  | apps/notifications/services/notifications.py |
| `offer_error` | 422 |  | apps/bookings/services/offers.py |
| `offer_forbidden` | 403 |  | apps/bookings/services/offers.py |
| `offer_no_longer_reserved` | 409 |  | apps/payments/services/payments.py |
| `offer_not_actionable` | 409 |  | apps/bookings/services/offers.py |
| `offer_not_found` | 404 |  | apps/bookings/services/offers.py |
| `otp_delivery_failed` | 503 |  | apps/accounts/services/otp.py |
| `otp_error` | 400 |  | apps/accounts/services/otp.py |
| `otp_expired` | 400 |  | apps/accounts/services/otp.py |
| `otp_invalid_code` | 400 |  | apps/accounts/services/otp.py |
| `otp_max_attempts` | 429 |  | apps/accounts/services/otp.py |
| `otp_not_found` | 400 |  | apps/accounts/services/otp.py |
| `otp_rate_limited` | 429 |  | apps/accounts/services/otp.py |
| `otp_resend_cooldown` | 429 |  | apps/accounts/services/otp.py |
| `outside_business_hours` | 400 |  | apps/bookings/services/scheduling.py |
| `password_change_required` | 403 |  | apps/accounts/authentication.py |
| `password_invalid` | 422 |  | apps/accounts/services/admin_auth.py |
| `payment_action_not_available` | 409 |  | apps/payments/services/payments.py |
| `payment_action_not_supported` | 501 |  | apps/payments/api/payments.py |
| `payment_admin_error` | 403, 404, 503 |  | apps/payments/services/admin.py |
| `payment_already_exists` | 422 |  | apps/payments/services/payments.py |
| `payment_error` | 422 |  | apps/payments/services/payments.py |
| `payment_forbidden` | 422 |  | apps/payments/services/payments.py |
| `payment_not_found` | 404 |  | apps/payments/api/payments.py, apps/payments/services/admin.py, apps/payments/services/payments.py |
| `payment_not_retryable` | 409 |  | apps/payments/services/payments.py |
| `payment_not_settled` | 409 |  | apps/jobs/services/jobs.py |
| `payout_admin_error` | 403, 404, 503 |  | apps/payouts/services/admin.py |
| `payout_already_exists` | 403, 404, 503 |  | apps/payouts/services/payouts.py |
| `payout_error` | 403, 404, 503 |  | apps/payouts/services/payouts.py |
| `payout_forbidden` | 403 |  | apps/payouts/services/payouts.py |
| `payout_not_found` | 404 |  | apps/payouts/api/payouts.py, apps/payouts/services/admin.py, apps/payouts/services/payouts.py |
| `photo_error` | 400, 409 |  | apps/jobs/services/photos.py |
| `photo_too_large` | 400, 409 |  | apps/jobs/services/photos.py |
| `pricing_error` | 403, 404, 503 |  | apps/services/services/pricing.py |
| `profile_update_error` | 422 |  | apps/accounts/services/identity.py |
| `property_error` | 422 |  | apps/properties/services/properties.py |
| `property_forbidden` | 403 |  | apps/bookings/api/bookings.py, apps/bookings/api/quotes.py, apps/properties/services/properties.py |
| `property_has_active_bookings` | 409 |  | apps/properties/services/properties.py |
| `property_inactive` | 409 |  | apps/bookings/services/bookings.py |
| `property_not_found` | 404 |  | apps/bookings/api/bookings.py, apps/bookings/api/quotes.py, apps/properties/api/properties.py, apps/properties/services/admin.py, apps/properties/services/properties.py |
| `property_not_serviceable` | 400 |  | apps/bookings/services/quotes.py |
| `provider_already_linked` | 400 |  | apps/accounts/services/social.py |
| `provider_reference_required` | 422 |  | apps/payments/services/admin.py, apps/payouts/services/admin.py |
| `quote_already_used` | 409 |  | apps/bookings/services/quotes.py |
| `quote_error` | 400 |  | apps/bookings/services/quotes.py |
| `quote_expired` | 409 |  | apps/bookings/services/quotes.py |
| `quote_not_found` | 404 |  | apps/bookings/services/quotes.py |
| `rejection_reason_required` | 400 |  | apps/contractors/services/verification.py |
| `request_resolved` | 409 |  | apps/support/services/admin.py |
| `scheduled_at_in_past` | 400 |  | apps/bookings/services/scheduling.py |
| `scheduled_at_required` | 400 |  | apps/bookings/services/scheduling.py |
| `scheduled_at_too_soon` | 400 |  | apps/bookings/services/scheduling.py |
| `scheduling_error` | 400 |  | apps/bookings/services/scheduling.py |
| `second_factor_unavailable` | 409 |  | apps/accounts/services/admin_auth.py |
| `self_assignment_forbidden` | 403 |  | apps/bookings/services/offers.py |
| `self_review_forbidden` | 403 |  | apps/contractors/services/verification.py |
| `service_not_found` | 400, 404 |  | apps/bookings/services/bookings.py, apps/services/api/catalog.py, apps/services/services/catalog.py, apps/services/services/pricing.py |
| `social_login_error` | 400 |  | apps/accounts/services/social.py |
| `submission_pending` | 409 |  | apps/contractors/services/verification.py |
| `superuser_required` | 403 |  | apps/accounts/authentication.py, apps/audit/services/backoffice.py |
| `support_admin_error` | 409 |  | apps/support/services/admin.py |
| `support_error` | 422 |  | apps/support/services/support.py |
| `support_forbidden` | 403 |  | apps/support/services/support.py |
| `support_not_found` | 404 |  | apps/support/api/support.py, apps/support/services/admin.py, apps/support/services/support.py |
| `timezone_not_allowed` | 400 |  | apps/bookings/services/scheduling.py |
| `token_not_valid` | 401 |  | apps/accounts/services/tokens.py |
| `too_many_photos` | 400, 409 |  | apps/jobs/services/photos.py |
| `tracking_error` | 400, 409 |  | apps/jobs/services/tracking.py |
| `tracking_window_closed` | 409 |  | apps/jobs/services/tracking.py |
| `travel_pricing_error` | 403, 404, 503 |  | apps/services/services/travel_pricing.py |
| `unsupported_photo_format` | 400, 409 |  | apps/jobs/services/photos.py |
| `unsupported_provider` | 400 |  | apps/accounts/api/auth.py, apps/accounts/services/social.py |
| `user_admin_error` | 403, 404, 503 |  | apps/accounts/services/backoffice_users.py |
| `user_not_found` | 404 |  | apps/accounts/services/backoffice_users.py |
| `validation_error` | 422 | The request body, query or path failed schema validation; see `errors[]`. | apps/accounts/api/auth.py, apps/bookings/api/bookings.py, apps/bookings/api/offers.py, apps/contractors/api/admin_contractors.py, apps/contractors/api/profile.py, apps/jobs/api/jobs.py, apps/properties/api/properties.py, apps/services/api/catalog.py, apps/support/api/support.py, config/urls.py |
| `verification_error` | 403, 404, 503 |  | apps/contractors/services/verification.py |
| `verification_not_found` | 404 |  | apps/contractors/api/admin_contractors.py, apps/contractors/services/verification.py |
| `wrong_password` | 400 |  | apps/accounts/services/admin_auth.py |
