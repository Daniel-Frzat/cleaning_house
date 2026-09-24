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
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import UserStatus
from apps.accounts.roles import ConfirmedRole
from apps.contractors.models import AvailabilityStatus, ContractorProfile
from apps.contractors.services.verification import is_contractor_eligible

from ..models import (
    Booking,
    BookingStatus,
    DispatchOffer,
    DispatchOfferStatus,
    DispatchStatus,
    OFFER_TTL_MINUTES,
)
from .distance import distance_between, property_coordinates

# درجة عرض واحدة ≈ 111 كم. يُستعمل لمربّع تقريبي يضيّق الاستعلام قبل
# حساب المسافة الدقيقة وفحص الأهلية (الذي يستعلم عن مستندين لكل مرشَّح).
_KM_PER_DEGREE = Decimal("111")


def max_distance_km():
    """أقصى مسافة إرسال (قرار PO — 2026-09-24: 50 كم). 0 = بلا حد."""
    return Decimal(getattr(settings, "DISPATCH_MAX_DISTANCE_KM", 0) or 0)


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

    المرشَّح يجب أن يستوفي كل ما يلي:
      1) availability_status == AVAILABLE
      2) is_contractor_eligible(profile) — تحقّق ABN وتأمين ساري (§6)
      3) لم يُعرض عليه هذا الحجز من قبل
      4) مسافته قابلة للقياس (إحداثيات معلومة للطرفين)
      5) ليس صاحب الحجز نفسه
      6) حسابه نشط (is_active و status=ACTIVE) ويملك صلاحية المقاول —
         الحساب الموقوف لا يتلقى عروضًا حتى لو بقي ملفه AVAILABLE
      7) ضمن DISPATCH_MAX_DISTANCE_KM

    🔒 الشرط الخامس: الحساب الواحد قد يكون عميلًا ومقاولًا معًا، فلا يجوز
       أن يُسنَد إليه حجزه هو. الاستبعاد على مستوى الاستعلام لا بعده:
       ما لا يدخل قائمة المرشحين لا يمكن أن يُعرض عليه بأي مسار.

    ⚠️ الأهلية تُحسب لحظيًا لكل مرشَّح — ليست حقلًا مخزَّنًا، ولا يمكن
       ترشيحها داخل استعلام قاعدة البيانات.
    """
    excluded_ids = _already_offered_contractor_ids(booking)

    queryset = (
        ContractorProfile.objects.filter(
            availability_status=AvailabilityStatus.AVAILABLE,
            # 🔒 الحساب نفسه نشط — نفس شروط ActiveUserJWTAuth
            user__is_active=True,
            user__status=UserStatus.ACTIVE,
        )
        # 🔒 صلاحية المقاول بنفس منطق User.has_contractor_access
        .filter(Q(user__role=ConfirmedRole.CONTRACTOR) | Q(user__is_contractor=True))
        .exclude(user__role=ConfirmedRole.ADMIN)
        .exclude(id__in=excluded_ids)
        # 🔒 لا إسناد ذاتي — يُقارن بالمستخدم لا بملف المقاول، لأن
        #    الربط بينهما واحد-لواحد والعميل يُعرَّف بحسابه.
        .exclude(user_id=booking.customer_id)
    )

    limit = max_distance_km()
    origin = property_coordinates(booking.property)
    if limit and origin is not None:
        # مربّع تقريبي حول العقار — المسافة الدقيقة تُفحص أدناه. خط الطول
        # يُوسَّع بعامل 2 لأن درجته أقصر من 111 كم في أستراليا (حتى −44°).
        lat, lon = Decimal(origin[0]), Decimal(origin[1])
        dlat = limit / _KM_PER_DEGREE
        dlon = dlat * 2
        queryset = queryset.filter(
            latitude__gte=lat - dlat,
            latitude__lte=lat + dlat,
            longitude__gte=lon - dlon,
            longitude__lte=lon + dlon,
        )

    candidates = []
    for profile in queryset:
        # المسافة أولًا: أرخص من فحص الأهلية (الذي يستعلم عن مستندين)
        distance = distance_between(booking.property, profile)
        if distance is None:
            # إحداثيات ناقصة لأحد الطرفين — يُستبعد، ولا يُفترض أي بديل
            continue

        if limit and distance > limit:
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

    🔒 يقفل صف الحجز أولًا (select_for_update) — القبول والرفض وإعادة
       الجدولة وانتهاء المهلة كلها تقفله أيضًا، فلا تتداخل. ثم:
       - حجز لم يعد PENDING (قُبل أو أُلغي) → لا عرض جديد.
       - عرض PENDING حيّ قائم → لا عرض ثانٍ (طلبان متزامنان مثلًا).
       - موعد الزيارة مضى → NO_CONTRACTOR بلا عرض (يمكن إعادة الجدولة).
    """
    caller_booking = booking
    booking = Booking.objects.select_for_update().select_related(
        "property__address"
    ).get(pk=booking.pk)

    if booking.status != BookingStatus.PENDING:
        logger.info(
            "Dispatch skipped — booking is %s (booking_id=%s)", booking.status, booking.id
        )
        return None

    now = timezone.now()
    if DispatchOffer.objects.filter(
        booking=booking, status=DispatchOfferStatus.PENDING, expires_at__gt=now
    ).exists():
        logger.info("Dispatch skipped — a live offer exists (booking_id=%s)", booking.id)
        return None

    # 📌 كل محاولة تُسجَّل، نجحت أو لا — الواجهة تعرض "نبحث منذ ..." بلا تخمين.
    booking.last_dispatch_attempt_at = now

    visit_passed = booking.scheduled_at is not None and booking.scheduled_at <= now
    candidates = [] if visit_passed else find_candidates(booking)

    if not candidates:
        # 📌 استُنفد المرشَّحون: هذا ما يميّز "لا يوجد عامل" عن "ما زلنا نبحث".
        # ⚠️ الحجز يبقى PENDING كما هو — الحقل للعرض ولا يُلغي شيئًا
        #    (القرار المفتوح #16 لم يُحسم هنا).
        booking.dispatch_status = DispatchStatus.NO_CONTRACTOR
        booking.save(update_fields=[
            "dispatch_status", "last_dispatch_attempt_at", "updated_at",
        ])
        _sync(caller_booking, booking)
        logger.info(
            "No eligible contractor for booking (booking_id=%s, visit_passed=%s) — "
            "staying PENDING with no active offer (open decision #16)",
            booking.id,
            visit_passed,
        )
        return None

    profile, distance = candidates[0]

    # العرض لا يعيش بعد موعد الزيارة: قبوله بعدها بلا معنى
    expires_at = now + timezone.timedelta(minutes=OFFER_TTL_MINUTES)
    if booking.scheduled_at is not None:
        expires_at = min(expires_at, booking.scheduled_at)

    offer = DispatchOffer(
        booking=booking,
        contractor=profile,
        status=DispatchOfferStatus.PENDING,
        distance_km=distance,
        expires_at=expires_at,
    )
    offer.full_clean()
    offer.save()

    # 📌 SEARCHING بعد حفظ العرض لا قبله — فلا تُرى الحالة بلا عرض حيّ.
    booking.dispatch_status = DispatchStatus.SEARCHING
    booking.save(update_fields=[
        "dispatch_status", "last_dispatch_attempt_at", "updated_at",
    ])
    _sync(caller_booking, booking)

    logger.info(
        "Dispatch offer created (offer_id=%s, booking_id=%s, contractor_id=%s, "
        "distance_km=%s)",
        offer.id,
        booking.id,
        profile.id,
        distance,
    )
    return offer


def _sync(target, source):
    """ينسخ حقول الإرسال إلى نسخة المستدعي — تبقى متسقة دون refresh_from_db."""
    if target is not source:
        target.dispatch_status = source.dispatch_status
        target.last_dispatch_attempt_at = source.last_dispatch_attempt_at


def redispatch_stranded_bookings(stale_after_minutes=5):
    """
    يستعيد الحجوزات العالقة: PENDING + SEARCHING بلا عرض PENDING حيّ.

    تحدث حين يُبتلع استثناء في hook الإرسال بعد الإنشاء/إعادة الجدولة، أو
    يتعطل العامل بين إنهاء العروض وإطلاق التتابع. بدون هذا يبقى العميل
    يرى "نبحث لك" إلى الأبد، ولا يستطيع إعادة الجدولة (تتطلب NO_CONTRACTOR).

    📌 stale_after_minutes: لا نلمس حجزًا عُدّل للتو — hook الإرسال قد
       يكون قيد التنفيذ.
    """
    now = timezone.now()
    cutoff = now - timezone.timedelta(minutes=stale_after_minutes)
    live = DispatchOffer.objects.filter(status=DispatchOfferStatus.PENDING, expires_at__gt=now)
    stranded = (
        Booking.objects.filter(
            status=BookingStatus.PENDING,
            dispatch_status=DispatchStatus.SEARCHING,
            updated_at__lt=cutoff,
        )
        .exclude(dispatch_offers__in=live)
        .only("id")
    )

    recovered = 0
    for booking in stranded:
        try:
            assign_next_contractor(booking)
            recovered += 1
        except Exception:  # noqa: BLE001 — حجز واحد لا يوقف البقية
            logger.exception("Re-dispatch failed (booking_id=%s)", booking.id)
    if recovered:
        logger.warning("Re-dispatched %d stranded booking(s)", recovered)
    return recovered
