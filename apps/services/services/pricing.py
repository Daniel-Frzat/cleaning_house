"""
Pricing Calculation Engine — Services Domain (Change Set §36.2)

الصيغة المعتمدة حرفيًا:

    per_service_amount = (room_price[service] × room_count) + base_price[service]
    total = Σ per_service_amount  +  (distance_km × price_per_km)

قرارات مقصودة في هذه الدالة:

  1) دالة نقية (pure) بمدخلات أوّلية فقط: معرّفات، أعداد، ومسافة.
     لا تعرف شيئًا عن Booking ولا Customer ولا Contractor — هذه الكيانات
     غير موجودة في هذه المرحلة أصلًا، وربطها هنا كان سيخلق اعتمادًا
     معكوسًا يصعب فكّه لاحقًا.

  2) مصدر المسافة ليس مسؤوليتها: distance_km يصل جاهزًا كمُعطى.
     اشتقاق المسافة من مقاول مُسنَد فعليًا شأن Dispatch Domain لاحقًا.

  3) ⚠️ بلا ذاكرة وبلا لقطة سعر (no snapshotting): تقرأ القيم الحيّة من
     الكتالوج عند كل استدعاء. استدعاء اليوم واستدعاء الغد بعد تعديل
     الإدارة للسعر يعطيان نتيجتين مختلفتين — وهذا هو السلوك المقصود.
     تجميد السعر على حجز بعينه مسؤولية Booking Domain وحده، ولا يجوز
     تسريبها إلى هنا (راجع اختبار
     test_price_change_is_reflected_proving_no_snapshotting).

  4) مكوّن المسافة يُحتسب مرة واحدة للطلب كله — لا يتكرر بعدد الخدمات
     المختارة. الرحلة واحدة مهما تعدّدت الخدمات في الموقع نفسه.

⚠️ لا تُضاف هنا ضرائب/خصومات/رسوم منصّة/حد أدنى للسعر. أي منها قاعدة عمل
   مستقلة غير واردة في §36.2، وإضافتها استنتاجًا تفسد الصيغة المعتمدة.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from ..models import PricingConfig, ServiceType

logger = logging.getLogger(__name__)

# دقة العرض النهائية فقط — الحساب الوسيط يبقى بدقة Decimal الكاملة.
CURRENCY_QUANTUM = Decimal("0.01")


class PricingError(Exception):
    """أصل أخطاء حساب السعر."""

    code = "pricing_error"


class EmptySelectionError(PricingError):
    """لا يمكن تسعير طلب بلا خدمة واحدة على الأقل."""

    code = "empty_service_selection"


class InactiveServiceError(PricingError):
    """
    خدمة معطّلة لا تُسعَّر.

    التعطيل الناعم يعني سحب الخدمة من التداول؛ تسعيرها بصمت كان سيعيدها
    إلى الخدمة من الباب الخلفي.
    """

    code = "inactive_service"


class UnknownServiceError(PricingError):
    """معرّف خدمة غير موجود في الكتالوج."""

    code = "service_not_found"


class InvalidDistanceError(PricingError):
    """المسافة سالبة أو غير قابلة للتحويل إلى Decimal."""

    code = "invalid_distance"


class InvalidRoomCountError(PricingError):
    """عدد الغرف سالب أو ليس عددًا صحيحًا."""

    code = "invalid_room_count"


# ------------------------------------------------------------
# تحقق من المدخلات
# ------------------------------------------------------------
def _validate_distance(distance_km):
    """
    المسافة عدد Decimal غير سالب.

    الأعداد الصحيحة مقبولة وتُحوَّل (5 → Decimal("5")). الـfloat مرفوض
    عمدًا: 0.1 الثنائية ليست 0.1 العشرية، وإدخالها في حساب نقدي يفتح باب
    أخطاء التقريب.
    """
    if isinstance(distance_km, float):
        raise InvalidDistanceError(
            "distance_km must be a Decimal, not a float (binary floats are "
            "not exact for currency math)."
        )

    if isinstance(distance_km, bool) or not isinstance(distance_km, (Decimal, int)):
        raise InvalidDistanceError("distance_km must be a Decimal.")

    try:
        distance = Decimal(distance_km)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidDistanceError("distance_km must be a valid Decimal.") from exc

    if not distance.is_finite():
        raise InvalidDistanceError("distance_km must be a finite number.")

    if distance < 0:
        raise InvalidDistanceError("distance_km must be greater than or equal to 0.")

    return distance


def _validate_room_count(raw_count, service_type_id):
    """عدد الغرف عدد صحيح غير سالب. صفر مسموح — يعني الرسم الأساسي فقط."""
    if isinstance(raw_count, bool) or not isinstance(raw_count, int):
        raise InvalidRoomCountError(
            f"room_count for service {service_type_id} must be an integer."
        )

    if raw_count < 0:
        raise InvalidRoomCountError(
            f"room_count for service {service_type_id} must be >= 0."
        )

    return raw_count


def _parse_selections(service_selections):
    """
    يحوّل المدخل الخام إلى [(service_type_id, room_count)] بعد التحقق.

    الخدمة المكررة في نفس الطلب لا تُدمج ولا تُرفض: كل سطر يُحتسب كما ورد،
    بما فيه base_price. الدمج قاعدة عمل غير واردة في §36.2، وافتراضها
    استنتاجًا يغيّر الناتج بصمت.
    """
    if service_selections is None:
        raise EmptySelectionError("service_selections must not be empty.")

    if isinstance(service_selections, (str, bytes, dict)):
        raise PricingError("service_selections must be a list of dicts.")

    try:
        selections = list(service_selections)
    except TypeError as exc:
        raise PricingError("service_selections must be a list of dicts.") from exc

    if not selections:
        raise EmptySelectionError("service_selections must not be empty.")

    parsed = []
    for entry in selections:
        if not isinstance(entry, dict):
            raise PricingError(
                "Each service selection must be a dict with 'service_type_id' "
                "and 'room_count'."
            )

        if "service_type_id" not in entry:
            raise PricingError("Each service selection requires 'service_type_id'.")
        if "room_count" not in entry:
            raise PricingError("Each service selection requires 'room_count'.")

        service_type_id = entry["service_type_id"]
        room_count = _validate_room_count(entry["room_count"], service_type_id)
        parsed.append((service_type_id, room_count))

    return parsed


def _load_services(parsed_selections):
    """
    يجلب كل الخدمات المشار إليها باستعلام واحد، ويرفض المجهول والمعطّل.

    الفحص يسبق أي حساب: لا يُعاد مبلغ جزئي عن طلب يحتوي خدمة معطّلة.
    """
    requested_ids = [sid for sid, _ in parsed_selections]

    try:
        found = {s.id: s for s in ServiceType.objects.filter(pk__in=requested_ids)}
    except (ValueError, TypeError, ValidationError) as exc:
        # معرّف غير صالح الشكل (ليس UUID) — يُعامل كخدمة غير موجودة
        raise UnknownServiceError("Unknown service type in selection.") from exc

    services = []
    for service_type_id, room_count in parsed_selections:
        service = found.get(service_type_id)

        if service is None:
            raise UnknownServiceError(f"Service type {service_type_id} not found.")

        if not service.is_active:
            # 🔒 لا تسعير لخدمة مسحوبة من التداول
            logger.warning(
                "Pricing attempted for inactive service (service_id=%s)", service.id
            )
            raise InactiveServiceError(
                f"Service type '{service.name}' is inactive and cannot be priced."
            )

        services.append((service, room_count))

    return services


def _read_price_per_km():
    """
    يقرأ سعر الكيلومتر العام الحيّ (§5) — قيمة واحدة للنظام كله.

    ملاحظة: لا يمر عبر catalog.get_pricing_config لأن تلك الدالة تفرض دور
    ADMIN. هذه الدالة بلا مستخدم عمدًا — ستُستدعى لاحقًا في مسارات العميل،
    والتسعير ليس عملية إدارية.
    """
    config = PricingConfig.objects.filter(pk=PricingConfig.SINGLETON_PK).first()

    # غياب الصف = النظام لم يُضبط بعد؛ الصفر هو نفسه الافتراض في الـModel.
    return config.price_per_km if config else Decimal("0")


# ------------------------------------------------------------
# الدالة العامة
# ------------------------------------------------------------
def calculate_price(
    service_selections: list[dict],
    distance_km: Decimal,
) -> Decimal:
    """
    يحسب السعر الإجمالي لمجموعة خدمات مختارة ومسافة (§36.2).

    Args:
        service_selections: [{"service_type_id": UUID, "room_count": int}, ...]
        distance_km: Decimal >= 0 — تصل جاهزة، ولا تُشتق هنا.

    Returns:
        Decimal مقرَّب إلى منزلتين عشريتين.

    Raises:
        EmptySelectionError:   القائمة فارغة.
        UnknownServiceError:   معرّف خدمة غير موجود.
        InactiveServiceError:  خدمة is_active=False.
        InvalidDistanceError:  مسافة سالبة أو غير Decimal.
        InvalidRoomCountError: عدد غرف سالب أو غير صحيح.

    ⚠️ القيمة المُعادة لقطة لحظية غير محفوظة. تعديل الإدارة للأسعار بعدها
       يغيّر نتيجة الاستدعاء التالي — تثبيت السعر شأن Booking Domain.
    """
    # كل التحقق أولًا: لا حساب جزئي على مدخلات غير صالحة.
    parsed = _parse_selections(service_selections)
    distance = _validate_distance(distance_km)
    services = _load_services(parsed)

    # مجموع الخدمات — تراكمي لكل خدمة مختارة (§36.2)
    services_total = Decimal("0")
    for service, room_count in services:
        per_service_amount = (service.room_price * room_count) + service.base_price
        services_total += per_service_amount

    # مكوّن المسافة — مرة واحدة للطلب كله، لا لكل خدمة
    distance_component = distance * _read_price_per_km()

    total = services_total + distance_component

    logger.info(
        "Price calculated (services=%d, services_total=%s, distance_km=%s, total=%s)",
        len(services),
        services_total,
        distance,
        total,
    )

    # التقريب في نقطة واحدة فقط: القيمة المُعادة.
    return total.quantize(CURRENCY_QUANTUM, rounding=ROUND_HALF_UP)
