"""
Dispatch Engine — Booking Domain (Change Set §36.1، §36.5، §36.6)

الإسناد التلقائي: يختار أقرب مقاول مؤهَّل ومتاح لم يُعرض عليه هذا الحجز
بعد، وينشئ له عرضًا صالحًا 60 دقيقة.

⚠️ قرار مفتوح صراحةً (Important Decision #16): ماذا يحدث حين لا يبقى أي
   مقاول مرشَّح — سواء لأن لا أحد متاح أصلًا، أو لأن الجميع رفض/انتهت
   مهلهم. لا سلوك بديل يُخترع هنا: تُعاد None ويبقى الحجز PENDING بلا
   عرض نشط. لا إلغاء تلقائي، ولا إشعار، ولا إعادة محاولة مجدولة.

⚠️ ترتيب الأقرب يعتمد على مسافة خط مستقيم محسوبة محليًا (distance.py) —
   ليست Routing Engine ولا adapter خارجي (Infra §8).
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.contractors.models import AvailabilityStatus, ContractorProfile
from apps.contractors.services.verification import is_contractor_eligible

from ..models import DispatchOffer, DispatchOfferStatus, OFFER_TTL_MINUTES
from .distance import distance_between

logger = logging.getLogger(__name__)


def _already_offered_contractor_ids(booking):
    """
    معرّفات المقاولين الذين عُرض عليهم هذا الحجز سابقًا — بأي حالة.

    يشمل المرفوض والمنتهي والمقبول: العرض لا يُكرَّر على المقاول نفسه
    مهما كانت نتيجة عرضه السابق (§36.5).
    """
    return set(
        DispatchOffer.objects.filter(booking=booking).values_list(
            "contractor_id", flat=True
        )
    )


def find_candidates(booking):
    """
    يعيد [(contractor_profile, distance_km)] مرتّبة تصاعديًا بالمسافة.

    المرشَّح يجب أن يستوفي الأربعة معًا:
      1) availability_status == AVAILABLE
      2) is_contractor_eligible(profile) — تحقّق ABN وتأمين ساري (§6)
      3) لم يُعرض عليه هذا الحجز من قبل
      4) مسافته قابلة للقياس (إحداثيات معلومة للطرفين)

    ⚠️ الأهلية تُحسب لحظيًا لكل مرشَّح — ليست حقلًا مخزَّنًا، ولا يمكن
       ترشيحها داخل استعلام قاعدة البيانات.
    """
    excluded_ids = _already_offered_contractor_ids(booking)

    queryset = ContractorProfile.objects.filter(
        availability_status=AvailabilityStatus.AVAILABLE
    ).exclude(id__in=excluded_ids)

    candidates = []
    for profile in queryset:
        # المسافة أولًا: أرخص من فحص الأهلية (الذي يستعلم عن مستندين)
        distance = distance_between(booking.property, profile)
        if distance is None:
            # إحداثيات ناقصة لأحد الطرفين — يُستبعد، ولا يُفترض أي بديل
            continue

        if not is_contractor_eligible(profile):
            continue

        candidates.append((profile, distance))

    candidates.sort(key=lambda pair: pair[1])
    return candidates


@transaction.atomic
def assign_next_contractor(booking):
    """
    ينشئ عرضًا للمقاول الأقرب المؤهَّل، أو يعيد None إن لم يوجد مرشَّح.

    ⚠️ None ليست خطأ: الحجز يبقى PENDING بلا عرض نشط، وهذا بالضبط ما
       ينص عليه القرار المفتوح #16. لا تُضف هنا إلغاءً أو إشعارًا أو
       إعادة جدولة.

    ⚠️ المسافة تُخزَّن على العرض (distance_km) ولا يُعاد حسابها عند
       القبول — اللقطة السعرية يجب أن تطابق مسافة هذا العرض تحديدًا.
    """
    candidates = find_candidates(booking)

    if not candidates:
        logger.info(
            "No eligible contractor for booking (booking_id=%s) — staying PENDING "
            "with no active offer (open decision #16)",
            booking.id,
        )
        return None

    profile, distance = candidates[0]

    offer = DispatchOffer(
        booking=booking,
        contractor=profile,
        status=DispatchOfferStatus.PENDING,
        distance_km=distance,
        expires_at=timezone.now() + timezone.timedelta(minutes=OFFER_TTL_MINUTES),
    )
    offer.full_clean()
    offer.save()

    logger.info(
        "Dispatch offer created (offer_id=%s, booking_id=%s, contractor_id=%s, "
        "distance_km=%s)",
        offer.id,
        booking.id,
        profile.id,
        distance,
    )
    return offer
