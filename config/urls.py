"""
Root URL Configuration.

  - /admin/       → Django Admin (للاستخدام الداخلي فقط)
  - /api/         → Django Ninja root (API Docs تلقائية على /api/docs)
  - /api/health/  → Health-check بسيط للتأكد من أن التطبيق يعمل
  - /api/auth/    → Identity Domain (OTP / Social / JWT)
  - /api/properties → Properties & Address Domain (CUSTOMER only)
  - /api/admin/   → Service Catalog & Pricing Domain (ADMIN only)
  - /api/services → Public service catalog (any authenticated role, no prices)
  - /api/contractor/ → Contractor Profile Domain (CONTRACTOR only, self-service)
  - /api/admin/contractors → Contractor records (ADMIN only, read-only)
  - /api/bookings → Booking Domain (CUSTOMER only)
  - /api/contractor/offers → Dispatch offers response (CONTRACTOR only)
  - /api/bookings/{id}/payment → Payment status (owner CUSTOMER or ADMIN)
  - /api/bookings/{id}/job → Job status + photos (customer/admin/assigned contractor)
  - /api/contractor/jobs/{id}/photos → Before/after photo upload (assigned contractor)
  - /api/bookings/{id}/payout → Contractor payout status (payee contractor or ADMIN)
  - /api/support-requests → Support Domain (any authenticated role, own requests)

⚠️ /api/admin/ مسار الإدارة عبر الـAPI — لا علاقة له بـ/admin/ (Django Admin).
⚠️ السعر يُكشف للعميل فقط بعد قبول مقاول للعرض (§36.1).
⚠️ الدفع شحن مباشر لحظة التأكيد (§36.4) — لا escrow ولا Invoice بعد.
"""

from django.contrib import admin
from django.urls import path
from ninja import NinjaAPI

from apps.accounts.api.auth import router as auth_router
from apps.bookings.api.bookings import router as bookings_router
from apps.bookings.api.quotes import router as booking_quotes_router
from apps.bookings.api.offers import router as contractor_offers_router
from apps.jobs.api.jobs import booking_router as jobs_booking_router
from apps.jobs.api.jobs import contractor_router as jobs_contractor_router
from apps.payments.api.payments import router as payments_router
from apps.payouts.api.payouts import earnings_router as contractor_earnings_router
from apps.payouts.api.payouts import router as payouts_router
from apps.properties.api.properties import router as properties_router
from apps.contractors.api.admin_contractors import router as admin_contractors_router
from apps.contractors.api.profile import router as contractor_profile_router
from apps.services.api.catalog import router as admin_catalog_router
from apps.services.api.public_catalog import router as public_services_router
from apps.support.api.support import router as support_router

API_DESCRIPTION = """
REST API for **Cleaning House**, an Australian cleaning marketplace that connects
customers with cleaning contractors. Built with Django and Django Ninja.

## Authentication

All endpoints require a JWT access token unless explicitly marked otherwise
(`POST /api/auth/otp/request`, `POST /api/auth/otp/verify`,
`POST /api/auth/social/{provider}` and `GET /api/health` are public).

Send the token on every protected request:

```
Authorization: Bearer <JWT access token>
```

There is no password login. A token is obtained either by verifying an SMS
one-time password (OTP) or by logging in with Apple or Google (Social Login).
Both flows return an `access` and a `refresh` token.

## Roles

A user's permissions decide which endpoints are reachable:

* **CUSTOMER** — owns properties, creates bookings, confirms job completion.
* **CONTRACTOR** — owns a contractor profile, responds to dispatch offers,
  executes jobs and uploads proof photos.
* **ADMIN** — manages the service catalog and pricing, reviews contractor
  verifications, and sees provider references hidden from other roles.

**One account can hold both the customer and contractor permissions** — the
same login books cleans and, once approved, takes work. `ADMIN` is exclusive
and is never combined with either.

`GET /api/auth/me` returns `roles` (an array — read this), alongside
`contractor_status` and `available_modes`. The singular `role` field is the
account's primary role and is kept for backwards compatibility only: a
dual-role account reports `"role": "CUSTOMER"` while `roles` holds both.

Calling an endpoint without the required permission returns `403`. On resources that are
owned by a specific user, a request from a non-owner returns `404` instead of
`403` wherever distinguishing the two would leak the existence of the resource.

## Domains

Nine domains are implemented end to end: Identity, Properties, Service
Catalog & Pricing, Contractors (profile and verification), Bookings (with
auto-dispatch, offers and rescheduling), Payments, Jobs, Payouts, and Support.

## Booking lifecycle

A booking is created as `PENDING` with **no price and no contractor**. Dispatch
then offers it to the nearest available contractor. The price is calculated and
frozen onto the booking **only when a contractor accepts an offer** — that is
also the first moment the price is visible to the customer. Acceptance moves the
booking to `CONFIRMED`, charges the customer directly, and creates the job as
`ASSIGNED`; the contractor starts it explicitly.
The contractor marks the job done, the customer confirms it, and the contractor
payout is released.

## ⚠️ External providers

Some external providers are **not selected yet**. Payment capture, contractor
payout, photo storage, SMS/OTP delivery and social-login verification are all
defined as abstract adapters, and no concrete implementation ships with this
build. Until a provider is configured for each of them, those operations fail
in a production deployment.
""".strip()

api = NinjaAPI(
    title="Cleaning House API",
    version="1.0.0",
    description=API_DESCRIPTION,
    # 📌 مخطط الأمان: JWTAuth يولّد {"type": "http", "scheme": "bearer"}
    #    تلقائيًا في components.securitySchemes، وكل نقطة محمية تشير إليه.
    #    ولا يمكن إثراؤه من هنا: django-ninja 1.1.0 يتجاهل مفاتيح
    #    openapi_extra الموجودة أصلًا (`if k not in self`)، و"components"
    #    منها. وإثراؤه بوراثة JWTAuth تغيير سلوكي ممنوع في مهمة توثيقية.
    #    البديل: شرح `Authorization: Bearer <JWT access token>` في الوصف
    #    أعلاه، وهو يظهر في صدر صفحة /api/docs.
    openapi_extra={
        "tags": [
            {"name": "Auth", "description": "OTP and social login, JWT issuance."},
            {"name": "Properties", "description": "Customer properties and their addresses."},
            {
                "name": "Admin — Service Catalog",
                "description": "Service types and the global per-km price (ADMIN only).",
            },
            {
                "name": "Contractor Profile",
                "description": (
                    "Contractor self-service: profile, availability, and "
                    "verification submissions."
                ),
            },
            {
                "name": "Admin — Contractors",
                "description": (
                    "Contractor records and manual verification review (ADMIN only)."
                ),
            },
            {"name": "Bookings", "description": "Customer bookings and their service selections."},
            {
                "name": "Contractor Offers",
                "description": "Contractor responses to dispatch offers.",
            },
            {"name": "Payments", "description": "Customer payment for a booking."},
            {"name": "Jobs", "description": "Job status and completion, seen from the booking."},
            {
                "name": "Contractor Jobs",
                "description": "Job execution by the assigned contractor: photos and mark-done.",
            },
            {"name": "Payouts", "description": "Contractor payout for a completed booking."},
            {
                "name": "Support",
                "description": (
                    "Support requests raised from the app. One-way in this "
                    "version: no replies and no attachments."
                ),
            },
            {"name": "System", "description": "Service health."},
        ],
    },
)


@api.get(
    "/health",
    tags=["System"],
    auth=None,
    summary="Service health check",
    description=(
        "Public, unauthenticated liveness probe used by the platform to decide "
        "whether the process is up. No token is required and nothing is "
        "persisted."
    ),
)
def health_check(request):
    """يتأكد أن التطبيق والاتصال بقاعدة البيانات يعملان."""
    return {"status": "ok", "phase": "Phase 0 — Foundation"}


api.add_router("/auth/", auth_router)
api.add_router("/properties", properties_router)
# كتالوج الخدمات والتسعير — ADMIN فقط (إنشاء/تعديل/تعطيل + سعر الكيلومتر).
api.add_router("/admin", admin_catalog_router)
# الكتالوج العام — قراءة فقط لأي مستخدم مصادَق عليه، بلا أي حقل تسعير.
# مسار منفصل تمامًا عن /admin/services الذي يكشف الأسعار للإدارة.
api.add_router("/services", public_services_router)
# ملف المقاول — مسارات ذاتية بالكامل (لا تقبل معرّفًا من العميل).
api.add_router("/contractor", contractor_profile_router)
# سجلات المقاولين للإدارة — قراءة فقط. مسارات /contractors* لا تتعارض
# مع /services* و /pricing-config في الـrouter الآخر المركّب على /admin.
api.add_router("/admin", admin_contractors_router)
# الحجوزات — CUSTOMER فقط. الرد بلا أي حقل سعر (§36.1).
api.add_router("/bookings", bookings_router)
api.add_router("/quotes", booking_quotes_router)
# رد المقاول على عروض الإسناد — مسارات /offers/* لا تتعارض مع /profile*
# في الـrouter الآخر المركّب على /contractor.
api.add_router("/contractor", contractor_offers_router)
# الدفع — يُركَّب على /bookings لأن المسار /bookings/{id}/payment.
# لا تعارض مع مسارات الحجوزات: تلك /bookings و /bookings/{id} فقط.
api.add_router("/bookings", payments_router)
# تنفيذ المهام — عرض المهمة عبر الحجز، ورفع الصور للمقاول المُسنَد.
api.add_router("/bookings", jobs_booking_router)
api.add_router("/contractor", jobs_contractor_router)
# دفع المقاول — قراءة فقط (المقاول المستحِق أو الإدارة). لا مسار إطلاق يدوي.
api.add_router("/bookings", payouts_router)
api.add_router("", contractor_earnings_router)
# الدعم — مسار مستقل تمامًا: متاح لأي دور مصادَق عليه، والصلاحية ملكية
# لا دور. لا تعارض مع /bookings رغم أن الطلب قد يشير إلى حجز.
api.add_router("/support-requests", support_router)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
