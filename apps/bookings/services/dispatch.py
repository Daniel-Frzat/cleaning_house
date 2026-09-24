"""
Dispatch Engine — Booking Domain (Change Set §36.1، §36.5، §36.6)

الإسناد التلقائي: يختار أقرب مقاول مؤهَّل ومتاح لم يُعرض عليه هذا الحجز
بعد، وينشئ له عرضًا صالحًا 60 دقيقة.

⚠️ قرار مفتوح صراحةً (Important Decision #16): ماذا يحدث حين لا يبقى أي
   مقاول مرشَّح — سواء لأن لا أحد متاح أصلًا، أو لأن الجميع رفض/انتهت
   مهلهم. لا سلوك بديل يُخترع هنا: تُعاد None ويبقى الحجز PENDING بلا
   عرض نشط. لا إلغاء تلقائي، ولا إشعار، ولا إعادة محاولة مجدولة.

📌 الترتيب بمسافة الطريق من الموقع الحالي للمقاول (مزوّد الاتجاهات)،
   أو خط مستقيم بإذن صريح (DISPATCH_ALLOW_HAVERSINE_FALLBACK). لا مرشَّح
   خارج DISPATCH_MAX_DISTANCE_KM.
"""

import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from adapters.directions import DirectionsUnavailable, get_directions_adapter
from apps.accounts.models import UserStatus
from apps.accounts.roles import ConfirmedRole
from apps.contractors.models import AvailabilityStatus, ContractorProfile
from apps.contractors.services.location import get_fresh_location
from apps.contractors.services.verification import is_contractor_eligible
from apps.services.services import travel_pricing

from ..models import (
    Booking,
    BookingStatus,
    DispatchOffer,
    DispatchOfferStatus,
    DispatchStatus,
    DistanceSource,
    OFFER_TTL_MINUTES,
)
from .distance import haversine_km, property_coordinates


# حالات عرض تحجز الحجز لمقاول بعينه
RESERVING_STATUSES = (
    DispatchOfferStatus.ACCEPTED_PENDING_PAYMENT,
    DispatchOfferStatus.ACCEPTED,
)


def max_distance_km():
    """أقصى مسافة إرسال (قرار PO — 2026-09-24: 50 كم). 0 = بلا حد."""
    return Decimal(getattr(settings, "DISPATCH_MAX_DISTANCE_KM", 0) or 0)


logger = logging.getLogger(__name__)


def _already_offered_contractor_ids(booking):
    """
    معرّفات المقاولين المستبعَدين من جولة الإسناد الحالية.

    📌 الاستبعاد يقتصر على **الجولة الحالية** (§4): العرض لا يُكرَّر على
       المقاول نفسه داخل الجولة الواحدة (§36.5)، لكن إعادة المحاولة
       تبدأ جولة جديدة يُعاد فيها النظر في الجميع.

    ⚠️ الفارق عن السلوك السابق: كان الاستبعاد أبديًا، فكان كل مقاول
       مؤهَّل مستبعَدًا بعد أول جولة فاشلة — وكانت إعادة المحاولة بلا أثر.
       الآن العروض المنتهية بحالة نهائية لا تمنع إعادة النظر، ويتكفّل
       القيد الفريد (booking, contractor) بمنع صفّين متزامنين.
    """
    return set(
        DispatchOffer.objects.filter(
            booking=booking, dispatch_round=booking.dispatch_round
        ).values_list("contractor_id", flat=True)
    )


class Measurement:
    """
    نتيجة قياس المسافة بين نقطتين، ومصدرها.

    📌 المصدر جزء من النتيجة لا تفصيل جانبي: يُخزَّن على العرض حتى يرى
       تدقيق الإدارة بأي أساس حُسب السعر (§9).
    """

    __slots__ = ("distance_km", "source", "eta_seconds", "polyline")

    def __init__(self, distance_km, source, eta_seconds=None, polyline=None):
        self.distance_km = distance_km
        self.source = source
        self.eta_seconds = eta_seconds
        self.polyline = polyline


def measure_distance(origin, destination):
    """
    مسافة الطريق من مزوّد الاتجاهات، أو خط مستقيم بإعداد صريح.

    ⚠️ الارتداد ليس صامتًا (§9): يحتاج DISPATCH_ALLOW_HAVERSINE_FALLBACK،
       والنتيجة تحمل مصدرها فيُخزَّن على العرض. الإنتاج بلا مزوّد وبلا
       إذن ارتداد لا يُسند أحدًا — وهو الفشل الصاخب المقصود.

    ⚠️ لا ETA من haversine أبدًا: مسافة الخط المستقيم ليست زمن وصول،
        واختلاقه كذب على العميل. eta_seconds تبقى None.
    """
    try:
        route = get_directions_adapter().get_route(origin, destination)
        return Measurement(
            distance_km=route.distance_km,
            source=DistanceSource.ROUTE,
            eta_seconds=route.duration_s,
            polyline=route.polyline,
        )
    except DirectionsUnavailable as exc:
        if not getattr(settings, "DISPATCH_ALLOW_HAVERSINE_FALLBACK", False):
            logger.warning(
                "Directions unavailable and haversine fallback is disabled — "
                "no offer will be created (%s)",
                exc,
            )
            return None

        logger.info("Directions unavailable; falling back to haversine (%s)", exc)

    distance = haversine_km(origin[0], origin[1], destination[0], destination[1])
    if distance is None:
        return None

    return Measurement(distance_km=distance, source=DistanceSource.HAVERSINE)


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

    origin = property_coordinates(booking.property)
    if origin is None:
        # 📌 عقار بلا إحداثيات لا يُقاس إليه شيء. الاقتباس يرفض هذا مبكرًا
        #    (§5)، وهذا حارس أخير للحجوزات السابقة لتلك القاعدة.
        logger.warning(
            "Booking property has no coordinates (booking_id=%s)", booking.id
        )
        return []

    limit = max_distance_km()

    candidates = []
    for profile in queryset:
        # 🔒 الموقع الحالي من الهاتف لا عنوان العمل (§8): الإسناد الفوري
        #    يسأل "أين هو الآن"، وعنوان المقرّ لا يجيب عن ذلك.
        # ⚠️ الموقع المتقادم كالغائب تمامًا: كلاهما يعني أننا لا نعرف
        #    مكانه، والمسافة المجهولة ليست صفرًا ولا افتراضًا.
        location = get_fresh_location(profile)
        if location is None:
            continue

        destination = (location.latitude, location.longitude)
        # 📌 فحص رخيص قبل نداء مزوّد الاتجاهات: مسافة الطريق لا تقل عن
        #    الخط المستقيم، فمن تجاوزه خارج النطاق حتمًا.
        if limit:
            straight = haversine_km(origin[0], origin[1], destination[0], destination[1])
            if straight is not None and straight > limit:
                continue

        measured = measure_distance(origin, destination)
        if measured is None:
            continue

        if limit and measured.distance_km > limit:
            continue

        if not is_contractor_eligible(profile):
            continue

        candidates.append((profile, measured))

    # الأقرب أولًا — المسافة أول عنصر في نتيجة القياس.
    candidates.sort(key=lambda pair: pair[1].distance_km)
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

    # 🔒 مقاول قبل وينتظر الدفع (أو دُفع) — الحجز محجوز، لا عرض لغيره
    if DispatchOffer.objects.filter(booking=booking, status__in=RESERVING_STATUSES).exists():
        logger.info("Dispatch skipped — booking is reserved (booking_id=%s)", booking.id)
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

    profile, measured = candidates[0]

    # ------------------------------------------------------------
    # تجميد لقطة التسعير على العرض (§10)
    # ------------------------------------------------------------
    # 📌 المقاول يرى أرباحه قبل أن يقبل. كان السعر يُحسب بعد القبول،
    #    فكان يقبل على المجهول.
    # 🔒 مجموع الخدمات من الاقتباس المجمَّد لا من الكتالوج الحيّ: تغيير
    #    الإدارة للأسعار بعد موافقة العميل لا يمسّ هذا الحجز (§11).
    config = travel_pricing.get_active_config()
    services_total = _frozen_services_total(booking)

    total, travel_fee = travel_pricing.calculate_final_total(
        services_total, measured.distance_km, config
    )

    # 🔒 حارس السقف (§7): الإجمالي لا يتجاوز ما وافق عليه العميل. الخرق
    #    هنا يعني خللًا في التسعير — لا يُصحَّح بصمت ولا يُعرض على مقاول.
    if booking.max_total is not None and total > booking.max_total:
        logger.error(
            "Pricing invariant violated — offer total exceeds the approved "
            "maximum (booking_id=%s, total=%s, max_total=%s, pricing_version=%s)",
            booking.id,
            total,
            booking.max_total,
            config.pricing_version,
        )
        return None

    ttl_seconds = config.dispatch_offer_ttl_seconds or (OFFER_TTL_MINUTES * 60)

    # العرض لا يعيش بعد موعد الزيارة: قبوله بعدها بلا معنى
    expires_at = now + timezone.timedelta(seconds=ttl_seconds)
    if booking.scheduled_at is not None:
        expires_at = min(expires_at, booking.scheduled_at)

    offer = DispatchOffer(
        booking=booking,
        contractor=profile,
        status=DispatchOfferStatus.PENDING,
        dispatch_round=booking.dispatch_round,
        distance_km=measured.distance_km,
        distance_source=measured.source,
        eta_seconds=measured.eta_seconds,
        services_total=services_total,
        travel_fee=travel_fee,
        total_amount=total,
        # صفر عمولة: ما يُدفع للمقاول هو الإجمالي نفسه (§8 من المواصفة).
        contractor_earnings=total,
        currency=config.currency,
        pricing_version=config.pricing_version,
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
        "distance_km=%s, source=%s, total=%s, pricing_version=%s)",
        offer.id,
        booking.id,
        profile.id,
        measured.distance_km,
        measured.source,
        total,
        config.pricing_version,
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
        .exclude(dispatch_offers__status__in=RESERVING_STATUSES)
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
def _frozen_services_total(booking):
    """
    مجموع الخدمات المجمَّد من الاقتباس، وإلا يُحسب من الكتالوج الحيّ.

    ⚠️ الرجوع إلى الكتالوج يخدم الحجوزات السابقة للاقتباس وحدها — تلك لا
       اقتباس لها. كل حجز جديد يمرّ باقتباس، فالمسار الحيّ لا يُستعمل.
    """
    if booking.quote_id is not None and booking.quote is not None:
        return booking.quote.services_total

    from decimal import Decimal

    total = Decimal("0")
    for selection in booking.service_selections.select_related("service_type"):
        service = selection.service_type
        total += (service.room_price * selection.room_count) + service.base_price
    return total
