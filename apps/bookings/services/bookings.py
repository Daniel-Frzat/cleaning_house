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

from ..models import Booking, BookingServiceSelection, BookingStatus, DispatchStatus
from .scheduling import TimezoneMismatchError, normalize_scheduled_at
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


class PropertyInactiveError(BookingError):
    """العقار محذوف (soft delete) — لا حجوزات جديدة عليه."""

    code = "property_inactive"


class BookingNotReschedulableError(BookingError):
    """
    الحجز ليس في الحالة الوحيدة التي تسمح بإعادة الجدولة.

    📌 النطاق المسموح ضيّق عمدًا (قرار MVP): PENDING + NO_CONTRACTOR +
       غير مُسنَد + بلا دفعة ناجحة. أي حالة أخرى تعني أن طرفًا آخر
       (مقاول قَبِل، أو عرض قائم لدى مقاول) صار طرفًا في الحجز، وسحبه
       من تحته قرارٌ يخصّ سياسة الإلغاء والاسترداد — وهي مؤجَّلة صراحةً.

    409 لا 400: الطلب سليم شكلًا، والرفض بسبب حالة المورد الحالية.
    """

    code = "booking_not_reschedulable"


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


def _resolve_selections(service_selections, *, allow_inactive=False):
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

        if not service.is_active and not allow_inactive:
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
    user,
    property_id,
    service_selections,
    scheduled_at=None,
    access_notes="",
    quote=None,
    payment_method_reference="",
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

    # 🔒 DELETE على العقار يعده بألا يُستعمل لحجوزات جديدة
    if not prop.is_active:
        raise PropertyInactiveError("This property has been removed and cannot be booked.")

    # All validation precedes writes. A quote owns the frozen selection snapshot;
    # inactive services remain usable because the customer already approved it.
    if quote is not None:
        if quote.customer_id != user.id or quote.property_id != prop.id:
            raise BookingError("This quote does not belong to this property.")
        service_selections = [
            {
                "service_type_id": entry["service_type_id"],
                "room_count": entry["room_count"],
            }
            for entry in quote.service_snapshot
        ]
        resolved = _resolve_selections(service_selections, allow_inactive=True)
    else:
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
        quote=quote,
        max_total=quote.maximum_total if quote is not None else None,
        pricing_version=quote.pricing_version if quote is not None else None,
        currency=quote.currency if quote is not None else "AUD",
        payment_method_reference=(payment_method_reference or "").strip(),
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
    """
    حجوزات المستخدم الحالي فقط — الأحدث أولًا.

    ⚠️ select_related("payment") إلزامي لا تحسين: الرد يحمل ملخص الدفع
       لكل حجز، وبدونه تصدر القائمة استعلامًا لكل صف (N+1).
       العلاقة واحد-لواحد (Payment.booking) فـselect_related هو الصحيح.
    """
    assert_is_customer(user)

    return (
        Booking.objects.filter(customer=user)
        .select_related("property", "payment")
        .prefetch_related("service_selections__service_type")
    )


def get_booking(user, booking_id):
    """يعيد حجزًا يملكه المستخدم، وإلا يرفع خطأ صلاحية/عدم وجود."""
    assert_is_customer(user)

    booking = (
        Booking.objects.filter(pk=booking_id)
        .select_related("property", "payment")
        .prefetch_related("service_selections__service_type")
        .first()
    )
    if booking is None:
        raise BookingNotFoundError("Booking not found.")

    assert_owns(user, booking)
    return booking


# ------------------------------------------------------------
# إعادة الجدولة
# ------------------------------------------------------------
@transaction.atomic
def reschedule_booking(user, booking_id, scheduled_at, timezone_name=None):
    """
    يغيّر موعد حجز لم يجد مقاولًا، ويعيد إطلاق دورة الإسناد.

    📌 النطاق المسموح (قرار MVP): PENDING + NO_CONTRACTOR + غير مُسنَد +
       بلا دفعة ناجحة. إعادة الجدولة والإلغاء بعد الإسناد/التأكيد
       مؤجَّلان حتى تُحسم سياسة الإلغاء والاسترداد.

    ⚠️ لماذا تُحذف العروض القديمة: find_candidates يستبعد كل مقاول
       عُرض عليه هذا الحجز **بأي حالة** (§36.5)، وقيد التفرد
       (booking, contractor) يمنع عرضًا ثانيًا على المقاول نفسه. فبعد
       NO_CONTRACTOR يكون لكل مقاول مؤهَّل صفُّ عرض، ولو تُركت الصفوف
       لعادت القائمة فارغة فورًا ولعادت الحالة NO_CONTRACTOR — أي لكانت
       إعادة الجدولة بلا أثر.

       الحذف مبرَّر منطقيًا لا التفافًا على القيد: الموعد الجديد عرض
       مختلف فعلًا، ومن رفض الثلاثاء قد يقبل الخميس. والحذف محصور في
       مدخلات assign_next_contractor — لا تغيير في find_candidates ولا
       في قيد التفرد، وكلاهما يعتمد عليه الرفض وانتهاء المهلة أيضًا.

    ⚠️ ثمن الحذف مقبول ومعلوم: يضيع سجلّ من رُفض عليه الموعد القديم.
       لا تقرير أداء يعتمد عليه اليوم، وإبقاؤه كان يتطلب تغيير القيد
       ودالة الترشيح معًا — أثرٌ أوسع بكثير من الحاجة.
    """
    get_booking(user, booking_id)  # الصلاحية والملكية (404 للغير)

    # 🔒 قفل الصف: طلبا إعادة جدولة متزامنان كانا يمرّان معًا على الحالة
    #    القديمة ويطلقان جولتي إسناد، فيتلقى مقاولان عرضين حيّين معًا.
    booking = Booking.objects.select_for_update().get(pk=booking_id)

    _assert_reschedulable(booking)

    # 🔒 المنطقة الزمنية مشتقة من العنوان حصرًا، كما في الإنشاء. قبول
    #    منطقة من العميل كان يتيح تجاوز ساعات العمل (منطقة أجنبية) أو 500
    #    (اسم غير موجود). الحقل مقبول للتوافق فقط إن طابق المنطقة المشتقة.
    effective_timezone = booking.customer_timezone
    if timezone_name and timezone_name != effective_timezone:
        raise TimezoneMismatchError(
            f"The visit timezone is derived from the property address "
            f"({effective_timezone}) and cannot be changed."
        )

    # 🔒 نفس قواعد الإنشاء حرفيًا (ماضٍ / ساعات العمل) — تُعاد من
    #    scheduling.py ولا تُكرَّر هنا، فلا تفترق القاعدتان.
    scheduled_utc = normalize_scheduled_at(scheduled_at, effective_timezone)

    # ⚠️ الحذف قبل الكتابة وداخل المعاملة نفسها: لو فشل ما بعده لا يبقى
    #    حجز بموعد قديم وقد فُقدت عروضه.
    deleted_count, _ = booking.dispatch_offers.all().delete()

    booking.scheduled_at = scheduled_utc
    booking.customer_timezone = effective_timezone
    # 📌 العودة إلى SEARCHING: البحث يبدأ من جديد فعلًا. القيمة تُصحَّح
    #    مباشرةً في assign_next_contractor إن لم يوجد مرشَّح.
    booking.dispatch_status = DispatchStatus.SEARCHING
    booking.save(
        update_fields=[
            "scheduled_at",
            "customer_timezone",
            "dispatch_status",
            "updated_at",
        ]
    )

    logger.info(
        "Booking rescheduled (booking_id=%s, scheduled_at=%s, offers_cleared=%s)",
        booking.id,
        scheduled_utc,
        deleted_count,
    )

    # ⚠️ بعد الـcommit لا داخله — نفس نمط الإنشاء: فشل الإسناد لا يجوز
    #    أن يتراجع عن إعادة جدولة صحيحة.
    transaction.on_commit(lambda: _dispatch_after_commit(booking), robust=True)

    return booking


def _assert_reschedulable(booking):
    """
    الشروط الأربعة، بترتيب الأعمّ فالأخصّ.

    ⚠️ الشرط الرابع (دفعة ناجحة) زائد منطقيًا: صف الدفع لا يُنشأ إلا عند
       القبول، والقبول يضبط dispatch_status=ASSIGNED فيسقط الشرطان قبله.
       أُبقي صمّامًا أمام صف عُدّل يدويًا أو مسار مستقبلي يشحن قبل
       الإسناد — استعلام واحد رخيص مقابل ألا يُعاد جدولة حجز مدفوع.
    """
    if booking.status != BookingStatus.PENDING:
        raise BookingNotReschedulableError(
            f"Only a PENDING booking can be rescheduled (currently {booking.status})."
        )

    if booking.dispatch_status != DispatchStatus.NO_CONTRACTOR:
        raise BookingNotReschedulableError(
            "A booking can only be rescheduled once the search has ended with no "
            f"available cleaner (dispatch status is currently {booking.dispatch_status})."
        )

    if booking.assigned_contractor_id is not None:
        raise BookingNotReschedulableError(
            "This booking already has an assigned contractor."
        )

    if _has_successful_payment(booking):
        raise BookingNotReschedulableError(
            "This booking has already been paid and cannot be rescheduled."
        )


def _has_successful_payment(booking):
    """
    هل للحجز دفعة ناجحة؟

    الاستيراد داخل الدالة: apps.payments يستورد من apps.bookings،
    والاستيراد على مستوى الوحدة يصنع دورة (نفس نمط offers.py).
    """
    from apps.payments.models import Payment, PaymentStatus

    return Payment.objects.filter(
        booking=booking, status=PaymentStatus.SUCCEEDED
    ).exists()
