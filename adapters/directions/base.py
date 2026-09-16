"""
Directions Provider Adapter — Abstract Interface ONLY (§9)

🔴 مزوّد الاتجاهات (Google Directions / Mapbox / ما شابه) قرار مفتوح. لا
   تنفيذ فعلي هنا ولا في أي مكان في كود الإنتاج: لا SDK، ولا استدعاءات
   HTTP، ولا مفاتيح.

الاختيار عبر settings.DIRECTIONS_PROVIDER_ADAPTER_CLASS — نفس نمط بقية
المزوّدين، فطبقة الـDomain لا تعرف أي مزوّد بعينه.

⚠️ لماذا هذا المزوّد ضروري: haversine يعطي مسافة خط مستقيم فقط. لا مسار
   شوارع ولا زمن وصول. عرض ETA مشتق من الخط المستقيم كذبة على العميل،
   والمسافة المستعملة في التسعير يجب أن تكون مسافة الطريق الفعلية.
"""

from abc import ABC, abstractmethod
from decimal import Decimal


class DirectionsUnavailable(Exception):
    """
    تعذّر الحصول على مسار.

    📌 ليست خطأً برمجيًا: المزوّد قد يفشل، أو لا يجد مسارًا، أو لا يكون
       مضبوطًا أصلًا. المستدعي يقرر ماذا يفعل — والقرار لا يكون اختلاق
       مسافة (§9).
    """

    code = "directions_unavailable"


class RouteResult:
    """
    نتيجة مسار — شكل محايد لا يخص مزوّدًا بعينه.

    distance_km : مسافة الطريق الفعلية (Decimal — لا float في مسار مالي).
    duration_s  : زمن الوصول المقدَّر بالثواني.
    polyline    : هندسة المسار كما يعيدها المزوّد (نص مبهم لا يُفسَّر).
    provider    : اسم المزوّد، للتدقيق.
    """

    __slots__ = ("distance_km", "duration_s", "polyline", "provider")

    def __init__(self, distance_km, duration_s, polyline=None, provider=""):
        if isinstance(distance_km, float):
            raise TypeError(
                "distance_km must be Decimal, not float — it feeds a money path."
            )
        self.distance_km = Decimal(str(distance_km))
        self.duration_s = int(duration_s)
        self.polyline = polyline
        self.provider = provider

    def __repr__(self):
        return (
            f"RouteResult(distance_km={self.distance_km}, "
            f"duration_s={self.duration_s}, provider={self.provider!r})"
        )


class BaseDirectionsProviderAdapter(ABC):
    """واجهة مزوّد الاتجاهات — تجريدية بالكامل."""

    @abstractmethod
    def get_route(self, origin, destination) -> RouteResult:
        """
        يعيد مسارًا بين نقطتين.

        Args:
            origin:      (latitude, longitude) — Decimal لكليهما.
            destination: (latitude, longitude)

        Raises:
            DirectionsUnavailable: تعذّر الحصول على مسار لأي سبب.
        """
        raise NotImplementedError(
            "Directions provider غير محسوم — لا تنفيذ فعلي في كود الإنتاج."
        )
