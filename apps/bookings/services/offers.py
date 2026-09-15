"""
Offer Response Service — Booking Domain (Change Set §36.1، §36.2، §36.6)

قبول العرض ورفضه. كل الطفرات تمر من هنا — طبقة الـAPI لا تستدعي
`.objects` مباشرة.

📌 لحظة كشف السعر: القبول هو النقطة الوحيدة التي يُحسب فيها السعر
   ويُثبَّت على الحجز (§36.1/§36.2). قبلها لا سعر ولا مقاول مُسنَد.

⚠️ المسافة المستخدمة في التسعير تُقرأ من العرض (offer.distance_km) ولا
   يُعاد حسابها: لو غيّر المقاول إحداثياته بين العرض والقبول، لاختلف
   السعر عن الأساس الذي بُني عليه العرض.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import ConfirmedRole
from apps.services.services.pricing import calculate_price

from ..models import (
    BookingStatus,
    DispatchOffer,
    DispatchOfferStatus,
    DispatchStatus,
)
from .dispatch import assign_next_contractor

logger = logging.getLogger(__name__)


class OfferError(Exception):
    """أصل أخطاء العروض."""

    code = "offer_error"


class OfferPermissionError(OfferError):
    """العرض موجَّه لمقاول آخر."""

    code = "offer_forbidden"


class OfferNotFoundError(OfferError):
    code = "offer_not_found"


class InvalidContractorRoleError(OfferError):
    """الدور غير مسموح له بالرد على العروض."""

    code = "invalid_contractor_role"


class OfferNotActionableError(OfferError):
    """
    العرض لم يعد قابلًا للرد — مُجاب عليه سابقًا أو انتهت مهلته.

    الرفض صريح وليس تجاهلًا صامتًا: المقاول يجب أن يعرف أن ردّه لم يُسجَّل.
    """

    code = "offer_not_actionable"


class SelfAssignmentError(OfferError):
    """
    المقاول هو صاحب الحجز نفسه.

    🔒 الحساب الواحد قد يحمل الصفتين، فلا يجوز أن ينفّذ المستخدم حجزه.
       الإسناد مُستبعَد أصلًا في محرّك الترشيح (services/dispatch.py)، وهذا
       الفحص طبقة ثانية: عرض قديم أُنشئ قبل القاعدة، أو صف أُدخل يدويًا،
       لا يجوز أن يمرّ من هنا.
    """

    code = "self_assignment_forbidden"


def assert_is_contractor(user):
    """الرد على العروض صلاحية CONTRACTOR حصرًا."""
    if user is None or not user.is_authenticated:
        raise OfferPermissionError("Authentication required.")
    if not user.has_contractor_access():
        raise InvalidContractorRoleError("Only contractors can respond to offers.")


def get_offer_for_contractor(user, offer_id):
    """
    يعيد عرضًا موجَّهًا لهذا المقاول تحديدًا.

    🔒 فحص الملكية على مستوى الكائن: العرض يخص ملف مقاول هذا المستخدم.
       عرض مقاول آخر يرفع OfferPermissionError (403 كما تنص المواصفة).
    """
    assert_is_contractor(user)

    offer = (
        DispatchOffer.objects.filter(pk=offer_id)
        .select_related("booking", "contractor")
        .first()
    )
    if offer is None:
        raise OfferNotFoundError("Offer not found.")

    profile = getattr(user, "contractor_profile", None)
    if profile is None or offer.contractor_id != profile.id:
        logger.warning(
            "Offer ownership check failed (user_id=%s, offer_id=%s)", user.id, offer_id
        )
        raise OfferPermissionError("This offer is not addressed to you.")

    return offer


def assert_not_own_booking(user, offer, *, action):
    """
    🔒 لا يردّ المستخدم على عرض يخص حجزه هو — قبولًا كان أو رفضًا.

    الرفض ممنوع أيضًا لا احتياطًا فقط: قبوله كان سيتيح لصاحب الحجز تحريك
    تتابع الإسناد إلى المقاول التالي من موقع لا يحق له أصلًا.

    طبقة ثانية فوق استبعاد محرّك الترشيح (services/dispatch.py): عرض
    أُنشئ قبل تطبيق القاعدة، أو صف أُدخل يدويًا، لا يجوز أن يمرّ.
    """
    if offer.booking.customer_id == user.id:
        logger.warning(
            "Self-assignment blocked (action=%s, user_id=%s, booking_id=%s, "
            "offer_id=%s)",
            action,
            user.id,
            offer.booking_id,
            offer.id,
        )
        raise SelfAssignmentError(f"You cannot {action} an offer on your own booking.")


def _selections_for_pricing(booking):
    """أسطر خدمات الحجز بالشكل الذي يتوقعه محرّك التسعير."""
    return [
        {"service_type_id": sel.service_type_id, "room_count": sel.room_count}
        for sel in booking.service_selections.all()
    ]


@transaction.atomic
def accept_offer(user, offer_id):
    """
    يقبل العرض، فيُحسب السعر ويُثبَّت، ويُسنَد المقاول، ويصبح الحجز CONFIRMED.

    📌 هذه هي لحظة كشف السعر للعميل (§36.1) — ولا لحظة قبلها.
    """
    offer = get_offer_for_contractor(user, offer_id)

    if not offer.is_actionable():
        raise OfferNotActionableError(
            f"Offer is {offer.status.lower()} or expired and cannot be accepted."
        )

    booking = offer.booking

    assert_not_own_booking(user, offer, action="accept")

    # المسافة من العرض نفسه — لا إعادة حساب (راجع docstring الملف)
    distance_km = offer.distance_km
    if distance_km is None:
        # لا ينبغي أن يحدث: العرض لا يُنشأ أصلًا بمسافة مجهولة
        raise OfferNotActionableError("Offer has no recorded distance.")

    price = calculate_price(
        service_selections=_selections_for_pricing(booking),
        distance_km=distance_km,
    )

    offer.status = DispatchOfferStatus.ACCEPTED
    offer.responded_at = timezone.now()
    offer.save(update_fields=["status", "responded_at"])

    # 📌 اللقطة تُكتب مرة واحدة هنا ولا تُعاد أبدًا (§36.2)
    booking.computed_price = price
    booking.assigned_contractor = offer.contractor
    booking.status = BookingStatus.CONFIRMED
    # 📌 البحث انتهى — الواجهة لم تعد تعرض "نبحث لك عن عامل".
    booking.dispatch_status = DispatchStatus.ASSIGNED
    booking.save(
        update_fields=[
            "computed_price",
            "assigned_contractor",
            "status",
            "dispatch_status",
            "updated_at",
        ]
    )

    logger.info(
        "Offer accepted (offer_id=%s, booking_id=%s, contractor_id=%s, price=%s, "
        "distance_km=%s)",
        offer.id,
        booking.id,
        offer.contractor_id,
        price,
        distance_km,
    )

    # 📌 لحظة التأكيد تُطلق أثرين جانبيين، كلاهما بعد تثبيت المعاملة لا
    #    داخلها: فشل أيّهما لا يجوز أن يُلغي تأكيدًا صحيحًا.
    #      1) الشحن المباشر (§36.4)
    #      2) إنشاء مهمة التنفيذ بحالة IN_PROGRESS (§20، §36.3)
    # robust=True: طبقة حماية من الإطار فوق try/except الداخلي في كل hook.
    #   العزل الحالي يعتمد على انضباط كل دالة؛ هذا يضمنه من الإطار أيضًا،
    #   فلو رُفع استثناء من خارج try/except بالخطأ لا يُسقط الـhook التالي.
    transaction.on_commit(lambda: _charge_after_commit(booking), robust=True)
    transaction.on_commit(lambda: _start_job_after_commit(booking), robust=True)

    return offer


def _charge_after_commit(booking):
    """
    يُطلق الشحن المباشر بعد تثبيت تأكيد الحجز.

    الاستثناءات تُبتلع وتُسجَّل: الحجز مؤكَّد فعلًا، وخطأ في طبقة الدفع
    يجب ألا يتحول إلى 500 على طلب قبول ناجح. الدفعة الفاشلة تبقى مسجَّلة
    بحالة FAILED، والحجز كما هو.
    """
    from apps.payments.services.payments import charge_for_booking

    try:
        charge_for_booking(booking)
    except Exception:  # noqa: BLE001 — نسجّل ولا نُسقط طلبًا ناجحًا
        logger.exception(
            "Automatic charge failed after booking confirmation (booking_id=%s)",
            booking.id,
        )


def _start_job_after_commit(booking):
    """
    يُنشئ مهمة التنفيذ بعد تثبيت تأكيد الحجز (§20، §36.3).

    الاستثناءات تُبتلع وتُسجَّل: الحجز مؤكَّد فعلًا، وخطأ في نطاق المهام
    يجب ألا يتحول إلى 500 على طلب قبول ناجح.
    """
    from apps.jobs.services.jobs import create_job_for_booking

    try:
        create_job_for_booking(booking)
    except Exception:  # noqa: BLE001 — نسجّل ولا نُسقط طلبًا ناجحًا
        logger.exception(
            "Automatic job creation failed after booking confirmation (booking_id=%s)",
            booking.id,
        )


@transaction.atomic
def decline_offer(user, offer_id):
    """
    يرفض العرض ويُطلق التتابع فورًا إلى المقاول التالي (§36.1).

    ⚠️ إن لم يوجد مقاول تالٍ، يبقى الحجز PENDING بلا عرض نشط — القرار
       المفتوح #16، ولا سلوك بديل يُخترع هنا.
    """
    offer = get_offer_for_contractor(user, offer_id)

    if not offer.is_actionable():
        raise OfferNotActionableError(
            f"Offer is {offer.status.lower()} or expired and cannot be declined."
        )

    assert_not_own_booking(user, offer, action="decline")

    offer.status = DispatchOfferStatus.DECLINED
    offer.responded_at = timezone.now()
    offer.save(update_fields=["status", "responded_at"])

    logger.info(
        "Offer declined (offer_id=%s, booking_id=%s, contractor_id=%s)",
        offer.id,
        offer.booking_id,
        offer.contractor_id,
    )

    # التتابع التلقائي — نفس سلوك انتهاء المهلة (§36.6)
    next_offer = assign_next_contractor(offer.booking)

    return offer, next_offer
