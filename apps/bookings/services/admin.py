"""
Back-office — الحجوزات (قراءة فقط، ADMIN).

⚠️ لا طفرات هنا عمدًا: إلغاء الحجز والاسترداد قرار منتج مفتوح (#12)،
   وما يحدث حين لا يوجد مقاول قرار مفتوح (#16). لا إعادة إسناد يدوية ولا
   إلغاء إداري حتى يُحسما.
"""

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from apps.audit.services.backoffice import assert_admin, filter_date_range

from ..models import Booking, BookingStatus, DispatchOffer, DispatchStatus


class BookingNotFoundError(Exception):
    code = "booking_not_found"


def list_bookings(
    actor,
    status=None,
    dispatch_status=None,
    customer_id=None,
    contractor_id=None,
    created_from=None,
    created_to=None,
    scheduled_from=None,
    scheduled_to=None,
    q=None,
):
    """
    كل الحجوزات — الأحدث أولًا.

    📌 contractor_id هو معرّف ملف المقاول (ContractorProfile) — نفس معرّف
       /admin/contractors/{profile_id}.
    """
    assert_admin(actor)

    qs = Booking.objects.select_related(
        "customer", "property__address", "assigned_contractor"
    ).order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    if dispatch_status:
        qs = qs.filter(dispatch_status=dispatch_status)
    if customer_id is not None:
        qs = qs.filter(customer_id=customer_id)
    if contractor_id is not None:
        qs = qs.filter(assigned_contractor_id=contractor_id)
    if q:
        qs = qs.filter(public_reference__icontains=q.strip())
    qs = filter_date_range(qs, "created_at", created_from, created_to)
    return filter_date_range(qs, "scheduled_at", scheduled_from, scheduled_to)


def get_booking(actor, booking_id):
    """الحجز كاملًا: الأسطر، العروض بكل جولاتها، الدفع والمهمة والدفع للمقاول."""
    assert_admin(actor)

    booking = (
        Booking.objects.select_related(
            "customer",
            "property__address",
            "assigned_contractor",
            "quote",
            "payment",
            "job",
            "payout",
        )
        .prefetch_related("service_selections__service_type")
        .filter(pk=booking_id)
        .first()
    )
    if booking is None:
        raise BookingNotFoundError("Booking not found.")
    return booking


def dispatch_history(booking):
    """كل عروض الحجز عبر كل الجولات — الأقدم أولًا ليُقرأ كتسلسل زمني."""
    return (
        DispatchOffer.objects.filter(booking=booking)
        .select_related("contractor")
        .order_by("dispatch_round", "offered_at")
    )


def summary_counts(now=None):
    """عدّادات لوحة المؤشرات الخاصة بالحجوزات."""
    now = now or timezone.now()
    today = timezone.localdate(now)

    by_status = {s: 0 for s in BookingStatus.values}
    for row in Booking.objects.values("status").annotate(n=Count("id")):
        by_status[row["status"]] = row["n"]

    by_dispatch = {s: 0 for s in DispatchStatus.values}
    for row in Booking.objects.values("dispatch_status").annotate(n=Count("id")):
        by_dispatch[row["dispatch_status"]] = row["n"]

    created = Booking.objects.aggregate(
        today=Count("id", filter=Q(created_at__date=today)),
        last_7_days=Count("id", filter=Q(created_at__gte=now - timedelta(days=7))),
    )
    return {
        "by_status": by_status,
        "by_dispatch_status": by_dispatch,
        "created_today": created["today"],
        "created_last_7_days": created["last_7_days"],
    }
