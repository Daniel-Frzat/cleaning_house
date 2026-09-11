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

from .models import DispatchOffer, DispatchOfferStatus
from .services.dispatch import assign_next_contractor

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
        # نفس تتابع الرفض تمامًا (§36.6)
        if assign_next_contractor(booking) is not None:
            created += 1

    if expired_ids:
        logger.info(
            "Expired %d offer(s), created %d follow-up offer(s)",
            len(expired_ids),
            created,
        )

    return {"expired": len(expired_ids), "created": created}
