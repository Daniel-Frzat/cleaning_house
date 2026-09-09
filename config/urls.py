"""
Root URL Configuration.

  - /admin/       → Django Admin (للاستخدام الداخلي فقط)
  - /api/         → Django Ninja root (API Docs تلقائية على /api/docs)
  - /api/health/  → Health-check بسيط للتأكد من أن التطبيق يعمل
  - /api/auth/    → Identity Domain (OTP / Social / JWT)
  - /api/properties → Properties & Address Domain (CUSTOMER only)

⚠️ لا يوجد بعد أي Endpoint لـDomains العمل (Booking, Payment, ...).
"""

from django.contrib import admin
from django.urls import path
from ninja import NinjaAPI

from apps.accounts.api.auth import router as auth_router
from apps.properties.api.properties import router as properties_router

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

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
