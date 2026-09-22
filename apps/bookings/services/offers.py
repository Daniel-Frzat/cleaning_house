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


from ..models import Booking, DispatchOffer, DispatchOfferStatus
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


def list_offers_for_contractor(user):
    """Return current actionable offers for the authenticated contractor."""
    assert_is_contractor(user)

    profile = getattr(user, "contractor_profile", None)
    if profile is None:
        return DispatchOffer.objects.none()

    return (
        DispatchOffer.objects.filter(
            contractor=profile,
            status=DispatchOfferStatus.PENDING,
        )
        .select_related("booking", "booking__property", "booking__property__address")
        .prefetch_related("booking__service_selections__service_type")
        .order_by("expires_at", "-offered_at")
    )


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


@transaction.atomic
def accept_offer(user, offer_id):
    """
    يقبل المقاول العرض — فيُحجز الحجز له وتبدأ محاولة الشحن (§12).

    ⚠️ **لا يُسنَد المقاول هنا ولا يُؤكَّد الحجز ولا تُنشأ مهمة.** القبول
       وحده لم يعد كافيًا: الإسناد يقع بعد تأكيد نجاح الدفع وحده (§14).
       هذا عكسٌ متعمَّد للسلوك السابق الذي كان يؤكّد ثم يشحن، فيترك
       حالة "مؤكَّد بدفعة فاشلة" ممكنة.

    📌 ما يقع هنا:
         1) العرض → ACCEPTED_PENDING_PAYMENT (يحجز الحجز، ويوقف العرض
            على غيره).
         2) دفعة PROCESSING بمبلغ العرض المجمَّد.
         3) محاولة الشحن بعد الـcommit.

    🔒 القفل على الحجز (select_for_update) يمنع قبول مقاولَين معًا: الثاني
       ينتظر ثم يجد الحجز محجوزًا فيُرفض بـ409 (§11).
    """
    offer = get_offer_for_contractor(user, offer_id)

    # 🔒 القفل على صف الحجز لا على العرض: التنافس بين عرضين مختلفين على
    #    الحجز نفسه، فالحجز هو المورد المتنازع عليه.
    booking = Booking.objects.select_for_update().get(pk=offer.booking_id)

    assert_not_own_booking(user, offer, action="accept")

    # 📌 القبول المكرَّر لنفس العرض ليس خطأً: يعيد الحالة نفسها (§12).
    #    يشمل ACCEPTED (نجح الدفع وأُسنِد) لا ACCEPTED_PENDING_PAYMENT وحدها —
    #    إعادة إرسال الطلب بعد انقطاع شبكة قد تصل بعد اكتمال الدفع، ورفضها
    #    عندئذٍ يخبر المقاول أن قبوله لم يُسجَّل وهو مُسنَد فعلًا.
    if offer.is_reserved():
        return offer

    if not offer.is_actionable():
        raise OfferNotActionableError(
            f"Offer is {offer.status.lower()} or expired and cannot be accepted."
        )

    # 🔒 حجز آخر سبقنا إليه — لا يقبله اثنان.
    if _booking_is_reserved(booking, exclude_offer_id=offer.id):
        raise OfferNotActionableError(
            "This booking has already been accepted by another contractor."
        )

    if offer.total_amount is None:
        # لا ينبغي أن يحدث: العرض لا يُنشأ بلا لقطة تسعير.
        raise OfferNotActionableError("Offer has no frozen price.")

    offer.status = DispatchOfferStatus.ACCEPTED_PENDING_PAYMENT
    offer.responded_at = timezone.now()
    offer.save(update_fields=["status", "responded_at"])

    logger.info(
        "Offer accepted, awaiting payment (offer_id=%s, booking_id=%s, "
        "contractor_id=%s, total=%s)",
        offer.id,
        booking.id,
        offer.contractor_id,
        offer.total_amount,
    )

    # ⚠️ الشحن بعد الـcommit: نداء شبكة داخل معاملة يُبقيها مفتوحة طوال
    #    رحلة الطلب. والحجز محجوز فعلًا بحالة العرض، فلا سباق.
    transaction.on_commit(lambda: _charge_after_commit(booking), robust=True)

    return offer


def _booking_is_reserved(booking, exclude_offer_id=None):
    """هل يحجز الحجزَ عرضٌ مقبول (بانتظار الدفع أو مدفوع)؟"""
    queryset = booking.dispatch_offers.filter(
        status__in=(
            DispatchOfferStatus.ACCEPTED_PENDING_PAYMENT,
            DispatchOfferStatus.ACCEPTED,
        )
    )
    if exclude_offer_id is not None:
        queryset = queryset.exclude(pk=exclude_offer_id)
    return queryset.exists()


def _charge_after_commit(booking):
    """
    يبدأ محاولة الشحن بعد تثبيت حجز العرض.

    الاستثناءات تُبتلع وتُسجَّل: العرض محجوز فعلًا، وخطأ في طبقة الدفع
    يجب ألا يتحول إلى 500 على طلب قبول ناجح. الدفعة الفاشلة تبقى مسجَّلة
    بحالة FAILED، ولا يُسنَد أحد.
    """
    from apps.payments.services.payments import start_charge_for_booking

    try:
        start_charge_for_booking(booking)
    except Exception:  # noqa: BLE001 — نسجّل ولا نُسقط طلبًا ناجحًا
        logger.exception(
            "Automatic charge failed to start after offer acceptance (booking_id=%s)",
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
