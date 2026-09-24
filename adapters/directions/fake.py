"""
FakeDirectionsAdapter — للتطوير والاختبار فقط.

⚠️ لا يتصل بأي مزوّد ولا يعرف الشوارع. يشتق مسافة تقريبية من الخط
   المستقيم بمعامل التفاف ثابت، وزمنًا من سرعة ثابتة.

🔒 يرفض العمل عند DEBUG=False ما لم يُفعَّل DIRECTIONS_ALLOW_FAKE_ADAPTER
   صراحةً — نفس نمط بقية الـfakes. نظام إنتاجي يعرض ETA مشتقًا من سرعة
   مفترضة يكذب على العميل بثقة.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import BaseDirectionsProviderAdapter, DirectionsUnavailable, RouteResult

# 📌 معامل التفاف: الطرق أطول من الخط المستقيم بنحو 30% في المدن.
#    رقم تقريبي للتطوير وحده ولا يدّعي دقة.
ROUTE_FACTOR = Decimal("1.30")

# متوسط سرعة حضرية مفترضة (كم/ساعة) لاشتقاق زمن تقريبي.
ASSUMED_SPEED_KMH = Decimal("30")

# إحداثية بهذه القيمة تُحاكي فشل المزوّد في الاختبارات.
FAILURE_SENTINEL_LAT = Decimal("-89.999999")


class FakeDirectionsAdapter(BaseDirectionsProviderAdapter):
    """مسار مشتق حسابيًا — للتطوير والاختبار."""

    def __init__(self):
        allow_fake = getattr(settings, "DIRECTIONS_ALLOW_FAKE_ADAPTER", False)
        if not settings.DEBUG and not allow_fake:
            raise ImproperlyConfigured(
                "FakeDirectionsAdapter لا يعرف الشوارع ولا يجوز استخدامه خارج "
                "بيئة التطوير/الاختبار: الـETA الذي يعيده مشتق من سرعة مفترضة. "
                "اضبط DIRECTIONS_PROVIDER_ADAPTER_CLASS على تنفيذ حقيقي، أو "
                "فعّل DIRECTIONS_ALLOW_FAKE_ADAPTER=True صراحةً."
            )

    def get_route(self, origin, destination):
        from apps.bookings.services.distance import haversine_km

        if Decimal(str(origin[0])) == FAILURE_SENTINEL_LAT:
            raise DirectionsUnavailable("Simulated provider failure.")

        straight = haversine_km(origin[0], origin[1], destination[0], destination[1])
        distance = (straight * ROUTE_FACTOR).quantize(Decimal("0.001"))

        duration = int((distance / ASSUMED_SPEED_KMH) * Decimal("3600"))

        return RouteResult(
            distance_km=distance,
            duration_s=duration,
            polyline=f"fake-polyline:{origin[0]},{origin[1]}->{destination[0]},{destination[1]}",
            provider="fake-directions",
        )
