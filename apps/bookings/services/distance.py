"""
Distance Calculation — Booking Domain (Change Set §36.5)

مسافة خط مستقيم (haversine) بين إحداثيات مخزَّنة. رياضيات بحتة.

⚠️ هذا ليس استدعاءً لـgps_distance Provider Adapter، ولا Routing Engine:
   لا شبكة، ولا API خارجي، ولا geocoding، ولا مسارات طرق. مجرد معادلة
   على إحداثيات موجودة أصلًا في قاعدة البيانات — متسق مع Infra §8
   ("خارج النطاق: Routing Engine ظاهر داخل Backend").

⚠️ "مسافة غير معروفة" ليست صفرًا ولا قيمة افتراضية: إن غاب أي إحداثي
   تُعاد None، والمقاول يُستبعد من الإسناد. افتراض مسافة وهمية كان
   سيُدخل مقاولًا غير قابل للقياس في ترتيب الأقرب ويشوّه التسعير.
"""

import logging
from decimal import Decimal
from math import asin, cos, radians, sin, sqrt

logger = logging.getLogger(__name__)

# نصف قطر الأرض المتوسط بالكيلومترات (IUGG mean radius)
EARTH_RADIUS_KM = Decimal("6371.0088")

# دقة المسافة المعادة — كافية للتسعير بالكيلومتر
DISTANCE_QUANTUM = Decimal("0.001")


def haversine_km(lat1, lon1, lat2, lon2):
    """
    المسافة بالكيلومترات بين نقطتين على سطح كروي.

    تقبل Decimal أو float أو int. تُعيد Decimal مقرَّبًا إلى 3 منازل.

    ⚠️ الحساب الداخلي يتم بـfloat لأن المثلثات تتطلب ذلك، لكن المخرَج
       يُحوَّل إلى Decimal: القيمة تدخل لاحقًا في حساب نقدي، وخلط float
       بـDecimal في التسعير مرفوض صراحةً في محرّك التسعير.
    """
    # التحويل إلى float للدوال المثلثية فقط
    phi1, phi2 = radians(float(lat1)), radians(float(lat2))
    delta_phi = radians(float(lat2) - float(lat1))
    delta_lambda = radians(float(lon2) - float(lon1))

    a = (
        sin(delta_phi / 2) ** 2
        + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
    )
    c = 2 * asin(min(1.0, sqrt(a)))

    distance = EARTH_RADIUS_KM * Decimal(str(c))
    return distance.quantize(DISTANCE_QUANTUM)


def get_coordinates(obj):
    """
    يستخرج (lat, lon) من كائن يحمل الحقلين، أو None إن نقص أحدهما.

    يقبل PropertyAddress أو ContractorProfile — كلاهما يسمّي الحقلين
    latitude/longitude.
    """
    if obj is None:
        return None

    lat = getattr(obj, "latitude", None)
    lon = getattr(obj, "longitude", None)

    if lat is None or lon is None:
        return None

    return (lat, lon)


def property_coordinates(prop):
    """
    إحداثيات العقار من عنوانه المرتبط، أو None.

    العقار قد لا يملك عنوانًا أصلًا (العلاقة OneToOne قد تكون غائبة)،
    وقد يملك عنوانًا بلا إحداثيات — الحالتان تعنيان "غير معروفة".
    """
    if prop is None:
        return None

    address = getattr(prop, "address", None)
    return get_coordinates(address)


def distance_between(prop, contractor_profile):
    """
    المسافة بين عقار ومقاول بالكيلومترات، أو None إن تعذّر القياس.

    ⚠️ None تعني "غير معروفة" ويجب أن تؤدي إلى استبعاد المقاول — لا
       تُعامل كصفر ولا كقيمة افتراضية.
    """
    origin = property_coordinates(prop)
    destination = get_coordinates(contractor_profile)

    if origin is None or destination is None:
        logger.debug(
            "Distance unknown (property_id=%s, contractor_id=%s)",
            getattr(prop, "id", None),
            getattr(contractor_profile, "id", None),
        )
        return None

    return haversine_km(origin[0], origin[1], destination[0], destination[1])
