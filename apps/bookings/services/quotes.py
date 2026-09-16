"""
Booking Quote Service — Booking Domain (§5)

اقتباس السعر قبل طلب عامل: يجمّد أسعار الخدمات ويحسب السقف الذي يوافق
عليه العميل.

📌 لماذا يلزم اقتباس أصلًا: شاشة المراجعة تعرض "Up to A$152.00" قبل أن
   يوجد مقاول. السعر النهائي يحتاج مسافة، والمسافة تحتاج مقاولًا — لكن
   السقف لا يحتاج أيًّا منهما: مجموع الخدمات + أقصى رسم مسافة.

🔒 السقف عقدٌ مع العميل: الشحن اللاحق لا يتجاوزه أبدًا. يُفحص في
   Payment.clean() قبل أي محاولة شحن، ويُرفض الشحن عند المخالفة.

⚠️ الاقتباس غير قابل للتعديل: أسعار الخدمات تُجمَّد داخله، فتغيير الإدارة
   للأسعار بين شاشة المراجعة والقبول لا يمسّ ما وافق عليه العميل (§11).
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.properties.services import properties as properties_svc
from apps.services.models import ServiceType
from apps.services.services import travel_pricing

from ..models import QUOTE_TTL_MINUTES, BookingQuote
from .bookings import (
    BookingError,
    BookingPermissionError,
    EmptySelectionError,
    InactiveServiceError,
    InvalidRoomCountError,
    UnknownServiceError,
    assert_is_customer,
)

logger = logging.getLogger(__name__)


class QuoteError(BookingError):
    """أصل أخطاء الاقتباس."""

    code = "quote_error"


class QuoteNotFoundError(QuoteError):
    code = "quote_not_found"


class QuoteExpiredError(QuoteError):
    """
    انتهت صلاحية الاقتباس.

    409 لا 400: الطلب سليم شكلًا، والرفض بسبب حالة المورد.
    """

    code = "quote_expired"


class QuoteAlreadyUsedError(QuoteError):
    """
    الاقتباس استُهلك في حجز سابق.

    🔒 الاستعمال مرة واحدة: إعادة استعماله كانت ستثبّت سعرًا قديمًا إلى
       الأبد وتلتف على تحديثات الأسعار.
    """

    code = "quote_already_used"


class PropertyNotServiceableError(QuoteError):
    """
    العقار بلا إحداثيات، فلا يمكن إسناده لأحد.

    📌 يُرفض عند الاقتباس لا عند الإسناد: الترشيح يقيس المسافة، ومن لا
       إحداثيات له يسقط صامتًا بلا رسالة. الرفض المبكر يخبر العميل بما
       يجب إصلاحه بدل بحث لا ينتهي.
    """

    code = "property_not_serviceable"


# ------------------------------------------------------------
# تجميد اختيارات الخدمات
# ------------------------------------------------------------
def _freeze_selections(service_selections):
    """
    يتحقق من الاختيارات ويعيد (لقطة JSON، مجموع الخدمات).

    ⚠️ كل التحقق قبل أي حساب — لا مجموع جزئي لمدخلات غير صالحة
       (نفس انضباط pricing.py).

    📌 الأسماء تُحفظ مع الأسعار (§9): الخدمة قد يُعاد تسميتها، وما يراه
       العميل في فاتورته يجب أن يكون ما وافق عليه.
    """
    if not service_selections:
        raise EmptySelectionError("A quote requires at least one service selection.")

    parsed = []
    for entry in service_selections:
        if not isinstance(entry, dict):
            raise QuoteError("service_selections must be a list of objects.")

        room_count = entry.get("room_count")
        if isinstance(room_count, bool) or not isinstance(room_count, int):
            raise InvalidRoomCountError("room_count must be an integer.")
        if room_count < 0:
            raise InvalidRoomCountError("room_count cannot be negative.")

        parsed.append((entry.get("service_type_id"), room_count))

    services = {
        str(s.id): s
        for s in ServiceType.objects.filter(pk__in=[sid for sid, _ in parsed])
    }

    snapshot = []
    services_total = Decimal("0")

    for service_type_id, room_count in parsed:
        service = services.get(str(service_type_id))
        if service is None:
            raise UnknownServiceError("Unknown service type in selection.")
        if not service.is_active:
            raise InactiveServiceError(
                f"Service type '{service.name}' is inactive and cannot be booked."
            )

        line_total = (service.room_price * room_count) + service.base_price
        services_total += line_total

        snapshot.append(
            {
                "service_type_id": str(service.id),
                "name": service.name,
                "room_count": room_count,
                "room_price": str(service.room_price),
                "base_price": str(service.base_price),
            }
        )

    return snapshot, services_total


# ------------------------------------------------------------
# الإنشاء
# ------------------------------------------------------------
@transaction.atomic
def create_quote(user, property_id, service_selections):
    """
    ينشئ اقتباسًا مجمَّدًا للعميل.

    🔒 يتحقق من ملكية العقار عبر طبقة العقارات — لا يُكرَّر المنطق هنا.
    """
    assert_is_customer(user)

    prop = properties_svc.get_property(user, property_id)

    address = getattr(prop, "address", None)
    if address is None or address.latitude is None or address.longitude is None:
        raise PropertyNotServiceableError(
            "This property has no GPS coordinates, so no cleaner can be matched "
            "to it. Set the location to make it bookable."
        )

    snapshot, services_total = _freeze_selections(service_selections)

    config = travel_pricing.get_active_config()
    maximum_total = travel_pricing.calculate_maximum_total(services_total, config)

    quote = BookingQuote(
        customer=user,
        property=prop,
        service_snapshot=snapshot,
        services_total=services_total,
        maximum_total=maximum_total,
        currency=config.currency,
        pricing_version=config.pricing_version,
        expires_at=timezone.now() + timedelta(minutes=QUOTE_TTL_MINUTES),
    )
    quote.full_clean()
    quote.save()

    logger.info(
        "Quote created (quote_id=%s, customer_id=%s, services_total=%s, "
        "maximum_total=%s, pricing_version=%s)",
        quote.id,
        user.id,
        services_total,
        maximum_total,
        config.pricing_version,
    )

    return quote


# ------------------------------------------------------------
# الاستهلاك
# ------------------------------------------------------------
def get_usable_quote(user, quote_id, property_id=None):
    """
    يعيد اقتباسًا صالحًا للاستعمال، وإلا يرفع خطأً مفهومًا.

    🔒 الملكية أولًا: اقتباس الغير كغير الموجود — لا نكشف وجوده.
    """
    assert_is_customer(user)

    quote = BookingQuote.objects.filter(pk=quote_id).select_related("property").first()

    if quote is None or quote.customer_id != user.id:
        raise QuoteNotFoundError("Quote not found.")

    if quote.is_expired():
        raise QuoteExpiredError(
            "This quote has expired; request a new one before booking."
        )

    if quote.bookings.exists():
        raise QuoteAlreadyUsedError("This quote has already been used for a booking.")

    # 📌 العقار يجب أن يطابق ما اقتُبس عليه: السقف حُسب لهذا العقار،
    #    واستعماله لعقار آخر يجعل الموافقة بلا معنى.
    if property_id is not None and str(quote.property_id) != str(property_id):
        raise QuoteError("This quote was issued for a different property.")

    return quote
