"""
Dispatch Background Tasks — Booking Domain (Change Set §36.6، Infra §15)

مهمة دورية واحدة: تُنهي العروض التي تجاوزت مهلتها وتُطلق التتابع نفسه
الذي يُطلقه الرفض الصريح (§36.6: "عدم الرد يُعامل معاملة الرفض").

🔒 التكرارية (idempotency — Infra §15): المهمة تُرشِّح status=PENDING
   حصرًا، وتُحدِّث الحالة إلى EXPIRED داخل استعلام شرطي. تشغيلها مرتين
   على العرض نفسه لا يُنتج تتابعًا مكرَّرًا: الدورة الثانية لا تجد العرض
   ضمن PENDING أصلًا.

⚠️ لا يوجد هنا أي منطق تسعير: العرض المنتهي لا يُسعَّر، والسعر يُحسب عند
   القبول وحده.
"""

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import Booking, BookingStatus, DispatchOffer, DispatchOfferStatus
from .services.dispatch import assign_next_contractor, redispatch_stranded_bookings

logger = logging.getLogger(__name__)


@shared_task(name="bookings.expire_pending_offers")
def expire_pending_offers():
    """
    يُنهي كل عرض PENDING تجاوز expires_at، ويُسند التالي لكل حجز متأثر.

    Returns:
        dict بعدد العروض المنتهية وعدد العروض الجديدة المُنشأة — مفيد
        للمراقبة، ولتأكيد أن التشغيل الثاني لا يفعل شيئًا.
    """
    now = timezone.now()

    expired_ids = []
    bookings_to_cascade = []

    # الترشيح والتحديث معًا داخل معاملة لكل عرض: لو سبقنا عاملٌ آخر إلى
    # العرض نفسه، فإن الشرط status=PENDING في الـUPDATE يمنع التكرار.
    pending = DispatchOffer.objects.filter(
        status=DispatchOfferStatus.PENDING, expires_at__lt=now
    ).select_related("booking")

    for offer in pending:
        with transaction.atomic():
            # 🔒 تحديث شرطي: يعيد 0 إن غيّر عاملٌ آخر الحالة قبلنا
            updated = DispatchOffer.objects.filter(
                pk=offer.pk, status=DispatchOfferStatus.PENDING
            ).update(status=DispatchOfferStatus.EXPIRED, responded_at=now)

            if not updated:
                # عرض عولج بالتوازي — لا تتابع مكرَّر
                continue

            expired_ids.append(offer.pk)
            bookings_to_cascade.append(offer.booking)

    created = 0
    for booking in bookings_to_cascade:
        # 🔒 كل حجز معزول: استثناء في أحدها لا يترك البقية عالقة في
        #    SEARCHING بلا عرض. ما يفلت هنا تلتقطه redispatch_stranded_bookings.
        try:
            # نفس تتابع الرفض تمامًا (§36.6)
            if assign_next_contractor(booking) is not None:
                created += 1
        except Exception:  # noqa: BLE001
            logger.exception("Cascade after expiry failed (booking_id=%s)", booking.id)

    # شبكة أمان: حجوزات SEARCHING بلا عرض حيّ (عامل تعطل بين المرحلتين،
    # أو hook إرسال ابتُلع استثناؤه بعد الإنشاء/إعادة الجدولة)
    recovered = redispatch_stranded_bookings()

    if expired_ids:
        logger.info(
            "Expired %d offer(s), created %d follow-up offer(s)",
            len(expired_ids),
            created,
        )

    return {"expired": len(expired_ids), "created": created, "recovered": recovered}


@shared_task(name="bookings.repair_confirmed_bookings")
def repair_confirmed_bookings(stale_after_minutes=5):
    """
    يُكمل الآثار الجانبية التي لم تحدث بعد قبول العرض أو الدفع أو إكمال المهمة.

    الـhooks بعد الـcommit تبتلع استثناءاتها عمدًا (لا تُسقط طلبًا ناجحًا)،
    فتبقى حالات معلّقة. هذه المهمة تلتقطها:

      1) عرض ACCEPTED_PENDING_PAYMENT بلا Payment → start_charge_for_booking
         (hook بدء الشحن لم يعمل؛ المقاول ينتظر والحجز محجوز بلا دفع)
      2) Payment SUCCEEDED والحجز غير CONFIRMED → confirm_payment
         (hook الإسناد لم يعمل؛ العميل دفع ولم يُسنَد أحد)
      3) CONFIRMED بلا Job → create_job_for_booking
      4) Job COMPLETED + دفعة عميل SUCCEEDED + بلا Payout → release_payout

    🔒 لا يعيد أي محاولة على سجل موجود (Payment/Payout بأي حالة): التكرارية
       الصارمة محفوظة. السجل PENDING بعد خطأ مزوّد يحتاج مطابقة لا إعادة.
    📌 stale_after_minutes: لا نلمس ما تغيّر للتو — hook قد يكون قيد التنفيذ.
    """
    from apps.jobs.services.jobs import create_job_for_booking
    from apps.payments.models import Payment, PaymentStatus
    from apps.payments.services.payments import confirm_payment, start_charge_for_booking
    from apps.payouts.services.payouts import release_missing_payouts

    cutoff = timezone.now() - timezone.timedelta(minutes=stale_after_minutes)
    confirmed = Booking.objects.filter(status=BookingStatus.CONFIRMED, updated_at__lt=cutoff)
    counts = {"charges": 0, "confirmations": 0, "jobs": 0, "payouts": 0}

    def attempt(kind, func, booking):
        try:
            func(booking)
            counts[kind] += 1
        except Exception:  # noqa: BLE001 — حجز واحد لا يوقف البقية
            logger.exception("Repair %s failed (booking_id=%s)", kind, booking.id)

    reserved_unpaid = Booking.objects.filter(
        status=BookingStatus.PENDING,
        payment__isnull=True,
        dispatch_offers__status=DispatchOfferStatus.ACCEPTED_PENDING_PAYMENT,
        dispatch_offers__responded_at__lt=cutoff,
    ).distinct()
    for booking in reserved_unpaid:
        attempt("charges", start_charge_for_booking, booking)

    paid_unassigned = Payment.objects.filter(
        status=PaymentStatus.SUCCEEDED,
        updated_at__lt=cutoff,
    ).exclude(booking__status=BookingStatus.CONFIRMED)
    for payment in paid_unassigned:
        try:
            confirm_payment(payment.id)
            counts["confirmations"] += 1
        except Exception:  # noqa: BLE001
            logger.exception("Repair confirmation failed (payment_id=%s)", payment.id)

    for booking in confirmed.filter(job__isnull=True):
        attempt("jobs", create_job_for_booking, booking)

    counts["payouts"] = release_missing_payouts(completed_before=cutoff)

    if any(counts.values()):
        logger.warning("Repaired confirmed bookings: %s", counts)
    return counts
