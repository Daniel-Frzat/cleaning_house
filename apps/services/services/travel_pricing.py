"""
Travel Pricing — Services Domain (§7)

حساب مكوّن المسافة وفق قواعد الداشبورد، وتطبيق التقريب.

📌 دالة نقية على مدخلات بدائية: مسافة وإعداد. لا تعرف Booking ولا Offer
   ولا Contractor — نفس انضباط pricing.py.

⚠️ كل الحساب Decimal. لا float إطلاقًا: الأعداد العشرية الثنائية غير
   دقيقة للمال، وهي ممنوعة في هذا المسار كما في pricing.py.

⚠️ التقريب في نقطة واحدة: الإجمالي النهائي وحده. تقريب المكوّنات ثم
   جمعها يُدخل انحرافًا ويجعل المجموع لا يطابق أجزاءه.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal

from ..models import PricingConfig, RoundingRule

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")

# مقادير التقريب لكل قاعدة.
_ROUNDING_STEPS = {
    RoundingRule.NEAREST_CENT: Decimal("0.01"),
    RoundingRule.NEAREST_5C: Decimal("0.05"),
    RoundingRule.NEAREST_10C: Decimal("0.10"),
    RoundingRule.NEAREST_DOLLAR: Decimal("1"),
}


class TravelPricingError(Exception):
    """أصل أخطاء تسعير المسافة."""

    code = "travel_pricing_error"


class InvalidDistanceError(TravelPricingError):
    """مسافة سالبة أو غير Decimal."""

    code = "invalid_distance"


def get_active_config():
    """
    يعيد إعداد التسعير النشط، منشئًا الصف الافتراضي إن لم يوجد.

    📌 لا بوابة دور هنا: التسعير ليس عملية إدارية — يقرأه محرّك الإسناد
       نيابةً عن النظام لا عن مستخدم. القراءة الإدارية المحمية تعيش في
       catalog.get_pricing_config.
    """
    config, _ = PricingConfig.objects.get_or_create(pk=PricingConfig.SINGLETON_PK)
    return config


def _validate_distance(distance_km):
    """
    ⚠️ float مرفوض صراحةً: القيمة تدخل حسابًا ماليًا، والعوائم الثنائية
       ليست دقيقة للمال (نفس قاعدة pricing.py).
    """
    if isinstance(distance_km, bool) or isinstance(distance_km, float):
        raise InvalidDistanceError(
            "distance_km must be a Decimal, not a float "
            "(binary floats are not exact for currency math)."
        )
    if isinstance(distance_km, int):
        distance_km = Decimal(distance_km)
    if not isinstance(distance_km, Decimal):
        raise InvalidDistanceError("distance_km must be a Decimal.")
    if not distance_km.is_finite() or distance_km < 0:
        raise InvalidDistanceError("distance_km must be a finite, non-negative value.")
    return distance_km


def calculate_travel_fee(distance_km, config=None):
    """
    رسم المسافة وفق القواعد النشطة (§7):

        chargeable = max(0, distance_km - included_distance_km)
        uncapped   = chargeable × price_per_km
        fee        = min(maximum_travel_fee, uncapped)

    ⚠️ بلا تقريب هنا: التقريب للإجمالي وحده.
    """
    config = config or get_active_config()
    distance = _validate_distance(distance_km)

    chargeable = max(Decimal("0"), distance - config.included_distance_km)
    uncapped = chargeable * config.price_per_km

    return min(config.maximum_travel_fee, uncapped)


def apply_rounding(amount, config=None):
    """
    يقرّب المبلغ وفق القاعدة المضبوطة — نقطة التقريب الوحيدة.

    ROUND_HALF_UP في كل الحالات: هو ما يتوقعه الناس من المال، وهو
    السلوك القائم في pricing.py.
    """
    config = config or get_active_config()
    step = _ROUNDING_STEPS.get(config.rounding_rule, CENT)

    if step == CENT:
        return amount.quantize(CENT, rounding=ROUND_HALF_UP)

    # التقريب لمضاعف: اقسم، قرّب لعدد صحيح، اضرب — ثم ثبّت المنازل.
    multiples = (amount / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (multiples * step).quantize(CENT)


def calculate_final_total(services_total, distance_km, config=None):
    """
    الإجمالي النهائي = مجموع الخدمات + رسم المسافة، مقرَّبًا مرة واحدة.

    services_total يصل جاهزًا (مجمَّدًا من الاقتباس عادةً) ولا يُشتق هنا.

    ⚠️ يعيد (total, travel_fee) — والرسم المُعاد **مقرَّب للسنت للتخزين
       وحده**. الجمع يستعمل القيمة الكاملة الدقة قبل التقريب: المسافة
       بثلاث منازل، فحاصل ضربها في سعر الكيلومتر قد يحمل منازل أكثر،
       وتقريبه قبل الجمع تقريبٌ مزدوج يُدخل انحرافًا.
    """
    config = config or get_active_config()

    travel_fee = calculate_travel_fee(distance_km, config)
    total = apply_rounding(services_total + travel_fee, config)

    # للتخزين في عمود بمنزلتين — لا يدخل أي حساب بعد هذه النقطة.
    travel_fee = travel_fee.quantize(CENT, rounding=ROUND_HALF_UP)

    logger.info(
        "Final total calculated (services=%s, travel_fee=%s, distance_km=%s, "
        "total=%s, pricing_version=%s)",
        services_total,
        travel_fee,
        distance_km,
        total,
        config.pricing_version,
    )

    return total, travel_fee


def calculate_maximum_total(services_total, config=None):
    """
    السقف المعروض للعميل قبل البحث (§5).

        maximum_total = services_total + maximum_travel_fee

    🔒 مضمون رياضيًا ألا يُتجاوز: رسم المسافة مسقوف بـmaximum_travel_fee
       مهما بعُد المقاول، فالإجمالي النهائي ≤ هذا السقف دائمًا. وهذا ما
       يجعل وعد "لن يتجاوز هذا المبلغ" صادقًا لا تقريبيًا.
    """
    config = config or get_active_config()
    return apply_rounding(services_total + config.maximum_travel_fee, config)
