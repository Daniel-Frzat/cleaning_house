"""
Root URL Configuration.

⚠️ لا يوجد هنا أي Endpoint خاص بـDomain عملي (Booking, Payment...).
فقط:
  - /admin/       → Django Admin (للاستخدام الداخلي فقط في Phase 0)
  - /api/         → Django Ninja root (API Docs تلقائية على /api/docs)
  - /api/health/  → Health-check بسيط للتأكد من أن التطبيق يعمل
"""

from django.contrib import admin
from django.urls import path
from ninja import NinjaAPI

api = NinjaAPI(
    title="Cleaning House API",
    version="0.1.0-phase0",
    description="Foundation phase — no domain endpoints implemented yet.",
)


@api.get("/health", tags=["System"])
def health_check(request):
    """يتأكد أن التطبيق والاتصال بقاعدة البيانات يعملان."""
    return {"status": "ok", "phase": "Phase 0 — Foundation"}


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]