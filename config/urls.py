"""
Root URL Configuration.

  - /admin/       → Django Admin (للاستخدام الداخلي فقط)
  - /api/         → Django Ninja root (API Docs تلقائية على /api/docs)
  - /api/health/  → Health-check بسيط للتأكد من أن التطبيق يعمل
  - /api/auth/    → Identity Domain (OTP / Social / JWT)
  - /api/properties → Properties & Address Domain (CUSTOMER only)
  - /api/admin/   → Service Catalog & Pricing Domain (ADMIN only)
  - /api/contractor/ → Contractor Profile Domain (CONTRACTOR only, self-service)
  - /api/admin/contractors → Contractor records (ADMIN only, read-only)
  - /api/bookings → Booking Domain (CUSTOMER only)
  - /api/contractor/offers → Dispatch offers response (CONTRACTOR only)

⚠️ /api/admin/ مسار الإدارة عبر الـAPI — لا علاقة له بـ/admin/ (Django Admin).
⚠️ السعر يُكشف للعميل فقط بعد قبول مقاول للعرض (§36.1).
⚠️ لا يوجد بعد أي Endpoint لـPayment / Escrow / Invoice.
"""

from django.contrib import admin
from django.urls import path
from ninja import NinjaAPI

from apps.accounts.api.auth import router as auth_router
from apps.bookings.api.bookings import router as bookings_router
from apps.bookings.api.offers import router as contractor_offers_router
from apps.properties.api.properties import router as properties_router
from apps.contractors.api.admin_contractors import router as admin_contractors_router
from apps.contractors.api.profile import router as contractor_profile_router
from apps.services.api.catalog import router as admin_catalog_router

api = NinjaAPI(
    title="Cleaning House API",
    version="0.2.0-phase1",
    description="Identity Domain — OTP, social login, and JWT issuance.",
)


@api.get("/health", tags=["System"], auth=None)
def health_check(request):
    """يتأكد أن التطبيق والاتصال بقاعدة البيانات يعملان."""
    return {"status": "ok", "phase": "Phase 0 — Foundation"}


api.add_router("/auth/", auth_router)
api.add_router("/properties", properties_router)
# كتالوج الخدمات والتسعير — ADMIN فقط. لا نقطة نهاية للعميل في هذه المرحلة.
api.add_router("/admin", admin_catalog_router)
# ملف المقاول — مسارات ذاتية بالكامل (لا تقبل معرّفًا من العميل).
api.add_router("/contractor", contractor_profile_router)
# سجلات المقاولين للإدارة — قراءة فقط. مسارات /contractors* لا تتعارض
# مع /services* و /pricing-config في الـrouter الآخر المركّب على /admin.
api.add_router("/admin", admin_contractors_router)
# الحجوزات — CUSTOMER فقط. الرد بلا أي حقل سعر (§36.1).
api.add_router("/bookings", bookings_router)
# رد المقاول على عروض الإسناد — مسارات /offers/* لا تتعارض مع /profile*
# في الـrouter الآخر المركّب على /contractor.
api.add_router("/contractor", contractor_offers_router)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
