"""
Booking Service — Booking Domain (Change Set §36.1، §20)

كل وصول إلى الحجوزات يمر من هنا. طبقة الـAPI لا تستدعي `.objects` مباشرة
— نفس النمط المعتمد في properties / services / contractors.

طبقتا حماية منفصلتان عمدًا:
  1) بوابة الدور: CUSTOMER فقط.
  2) فحص الملكية على مستوى الكائن: العقار يخص هذا العميل، والحجز يخصه.
لا يُستغنى عن أي منهما.

⚠️ ما لا يوجد هنا، عمدًا (المرحلة التالية):
   - أي إسناد مقاول أو عروض أو قبول/رفض.
   - أي استدعاء لـcalculate_price — لا يُحسب سعر ولا يُكشف في هذه المرحلة.
   - أي كتابة لـcomputed_price أو assigned_contractor.
"""

import logging

from django.db import transaction

from apps.accounts.roles import ConfirmedRole
from apps.properties.services import properties as properties_svc
from apps.services.models import ServiceType

from ..models import Booking, BookingServiceSelection, BookingStatus
from .scheduling import normalize_scheduled_at
from .timezone import get_timezone_for_state

logger = logging.getLogger(__name__)


class BookingError(Exception):
    """أصل أخطاء نطاق الحجوزات."""

    code = "booking_error"


class BookingPermissionError(BookingError):
    """المستخدم لا يملك هذا الحجز."""

    code = "booking_forbidden"


class BookingNotFoundError(BookingError):
    code = "booking_not_found"


class InvalidCustomerRoleError(BookingError):
    """الدور غير مسموح له بإنشاء حجز."""

    code = "invalid_customer_role"


class EmptySelectionError(BookingError):
    """حجز بلا خدمة واحدة على الأقل لا معنى له."""

    code = "empty_service_selection"


class InactiveServiceError(BookingError):
    """
    خدمة معطّلة لا تُحجز.

    نفس منطق apps/services/services/pricing.py:InactiveServiceError —
    التعطيل الناعم سحبٌ من التداول، وقبول حجزها كان سيعيدها من الباب
    الخلفي. الرمز نفسه متعمَّد حتى يتعرّف العميل على الحالة ذاتها.
    """

    code = "inactive_service"


class UnknownServiceError(BookingError):
    """معرّف خدمة غير موجود في الكتالوج."""

    code = "service_not_found"


class InvalidRoomCountError(BookingError):
    """عدد الغرف سالب أو ليس عددًا صحيحًا."""

    code = "invalid_room_count"


# ------------------------------------------------------------
# فحوص الصلاحية
# ------------------------------------------------------------
def assert_is_customer(user):
    """إنشاء الحجوزات وقراءتها صلاحية CUSTOMER حصرًا."""
    if user is None or not user.is_authenticated:
        raise BookingPermissionError("Authentication required.")
    if not user.has_customer_access():
        raise InvalidCustomerRoleError("Only customers can manage bookings.")


def assert_owns(user, booking):
    """
    فحص الملكية على مستوى الكائن — نقطة الإنفاذ الوحيدة.

    تُستدعى في كل قراءة، حتى لو كان الاستعلام مُرشَّحًا أصلًا
    (نفس نمط properties/services/properties.py:assert_owns).
    """
    if user is None or not user.is_authenticated:
        raise BookingPermissionError("Authentication required.")
    if booking.customer_id != user.id:
        logger.warning(
            "Booking ownership check failed (user_id=%s, booking_id=%s)",
            user.id,
            booking.id,
        )
        raise BookingPermissionError("You do not have access to this booking.")


# ------------------------------------------------------------
# التحقق من المدخلات
# ------------------------------------------------------------
def _validate_room_count(raw_count, service_type_id):
    """عدد الغرف عدد صحيح غير سالب. صفر مسموح — الرسم الأساسي وحده."""
    if isinstance(raw_count, bool) or not isinstance(raw_count, int):
        raise InvalidRoomCountError(
            f"room_count for service {service_type_id} must be an integer."
        )
    if raw_count < 0:
        raise InvalidRoomCountError(
            f"room_count for service {service_type_id} must be >= 0."
        )
    return raw_count


def _resolve_selections(service_selections):
    """
    يحوّل المدخل الخام إلى [(ServiceType, room_count)] بعد التحقق.

    🔒 كل التحقق يسبق أي كتابة: لا يُنشأ حجز ناقص ثم يُكمَّل. الخدمة
       المعطّلة أو المجهولة تُبطل الطلب كله قبل لمس قاعدة البيانات.
    """
    if not service_selections:
        raise EmptySelectionError("A booking requires at least one service selection.")

    parsed = []
    for entry in service_selections:
        if not isinstance(entry, dict):
            raise BookingError(
                "Each service selection must be a dict with 'service_type_id' "
                "and 'room_count'."
            )
        if "service_type_id" not in entry:
            raise BookingError("Each service selection requires 'service_type_id'.")
        if "room_count" not in entry:
            raise BookingError("Each service selection requires 'room_count'.")

        service_type_id = entry["service_type_id"]
        room_count = _validate_room_count(entry["room_count"], service_type_id)
        parsed.append((service_type_id, room_count))

    requested_ids = [sid for sid, _ in parsed]
    found = {s.id: s for s in ServiceType.objects.filter(pk__in=requested_ids)}

    resolved = []
    for service_type_id, room_count in parsed:
        service = found.get(service_type_id)

        if service is None:
            raise UnknownServiceError(f"Service type {service_type_id} not found.")

        if not service.is_active:
            # 🔒 لا حجز لخدمة مسحوبة من التداول
            logger.warning(
                "Booking attempted for inactive service (service_id=%s)", service.id
            )
            raise InactiveServiceError(
                f"Service type '{service.name}' is inactive and cannot be booked."
            )

        resolved.append((service, room_count))

    return resolved


# ------------------------------------------------------------
# العمليات
# ------------------------------------------------------------
@transaction.atomic
def create_booking(
    user, property_id, service_selections, scheduled_at=None, access_notes=""
):
    """
    ينشئ حجزًا بحالة PENDING مع أسطر خدماته.

    ⚠️ computed_price و assigned_contractor يبقيان null: لا سعر يُحسب ولا
       مقاول يُسنَد في هذه المرحلة. ذلك شأن Dispatch لاحقًا.

    الملكية تُفحص عبر properties_svc.get_property الذي يستدعي assert_owns —
    لا نكرّر منطق الملكية هنا.

    📌 موعد الزيارة: المنطقة الزمنية تُشتق من ولاية عنوان العقار وتُخزَّن
       للعرض، والموعد يُطبَّع إلى UTC ويُتحقق منه (ساعات العمل + المستقبل).

    ⚠️ scheduled_at اختياري في التوقيع لا في المنتَج: طبقة الـAPI تفرضه
       إلزاميًا في الـschema. بقاؤه اختياريًا هنا يُبقي المسارات الداخلية
       (الاختبارات، الأوامر الإدارية) قادرة على إنشاء حجز بلا موعد، تمامًا
       كالصفوف السابقة للحقل — ولا يفتح ثغرة في المسار العام.
    ⚠️ لا فحص لتوفّر أي مقاول هنا: الإسناد يقع بعد الحجز لا قبله (§36.1).

    📌 access_notes يكتبه العميل بنفسه ويصل كما هو: لا يُشتق من العنوان،
       ولا يُورَّث من حجز سابق، ولا يُولَّد من أي مصدر آخر. تركه فارغًا
       هو الحالة الطبيعية — أغلب الزيارات لا تحتاج تعليمات.
    """
    assert_is_customer(user)

    # يرفع PropertyPermissionError / PropertyNotFoundError عند الفشل
    prop = properties_svc.get_property(user, property_id)

    # كل التحقق قبل أي كتابة
    resolved = _resolve_selections(service_selections)

    address = getattr(prop, "address", None)
    customer_timezone = get_timezone_for_state(getattr(address, "state", ""))

    # يرفع SchedulingError عند موعد ماضٍ أو خارج الدوام
    scheduled_utc = (
        normalize_scheduled_at(scheduled_at, customer_timezone)
        if scheduled_at is not None
        else None
    )

    booking = Booking(
        customer=user,
        property=prop,
        status=BookingStatus.PENDING,
        scheduled_at=scheduled_utc,
        customer_timezone=customer_timezone,
        # نصّ العميل كما كتبه — التشذيب فقط، بلا أي تفسير
        access_notes=(access_notes or "").strip(),
    )
    # full_clean يفرض حدّ الطول (ACCESS_NOTES_MAX_LENGTH) ويرفع 422
    booking.full_clean()
    booking.save()

    for service, room_count in resolved:
        selection = BookingServiceSelection(
            booking=booking,
            service_type=service,
            room_count=room_count,
        )
        selection.full_clean()
        selection.save()

    logger.info(
        "Booking created (booking_id=%s, customer_id=%s, property_id=%s, lines=%d)",
        booking.id,
        user.id,
        prop.id,
        len(resolved),
    )

    # ⚠️ الإسناد التلقائي بعد نجاح المعاملة لا داخلها (§36.1):
    #    فشل الإسناد لا يجوز أن يُلغي حجزًا صالحًا. و"لا مقاول متاح"
    #    ليس فشلًا أصلًا — يبقى الحجز PENDING بلا عرض (القرار المفتوح #16).
    # robust=True: طبقة حماية من الإطار فوق try/except الداخلي — استثناء
    #   غير متوقع من خارجه لا يُسقط بقية hooks نفس المعاملة (Django 5.0+).
    transaction.on_commit(lambda: _dispatch_after_commit(booking), robust=True)

    return booking


def _dispatch_after_commit(booking):
    """
    يُطلق الإسناد التلقائي بعد تثبيت الحجز.

    الاستثناءات تُبتلع وتُسجَّل: الحجز نفسه صالح ومحفوظ، وخطأ في محرّك
    الإسناد يجب ألا يتحول إلى خطأ 500 على طلب إنشاء ناجح. الحجز يبقى
    PENDING ويمكن إسناده لاحقًا.
    """
    from .dispatch import assign_next_contractor

    try:
        assign_next_contractor(booking)
    except Exception:  # noqa: BLE001 — نسجّل ولا نُسقط طلبًا ناجحًا
        logger.exception(
            "Auto-dispatch failed after booking creation (booking_id=%s)", booking.id
        )


def list_bookings(user):
    """حجوزات المستخدم الحالي فقط — الأحدث أولًا."""
    assert_is_customer(user)

    return (
        Booking.objects.filter(customer=user)
        .select_related("property")
        .prefetch_related("service_selections__service_type")
    )


def get_booking(user, booking_id):
    """يعيد حجزًا يملكه المستخدم، وإلا يرفع خطأ صلاحية/عدم وجود."""
    assert_is_customer(user)

    booking = (
        Booking.objects.filter(pk=booking_id)
        .select_related("property")
        .prefetch_related("service_selections__service_type")
        .first()
    )
    if booking is None:
        raise BookingNotFoundError("Booking not found.")

    assert_owns(user, booking)
    return booking
