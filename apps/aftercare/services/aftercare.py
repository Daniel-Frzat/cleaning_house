"""
ما بعد التنظيف — التقييم الإلزامي، ضمان إعادة التنظيف، الفاتورة (قرارات
PO 2026-09-27). راجع docstring apps/aftercare/models.py.

⚠️ استيراد النطاقات الأخرى داخل الدوال: هذا التطبيق يقرأ الحجز والمهمة
   والدفعة، وتلك النطاقات تستدعيه (فرض التقييم، مخرجات الحجز) — الاستيراد
   على مستوى الملف كان سيصنع حلقة.
"""

import logging
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count
from django.utils import timezone

from ..models import (
    Invoice,
    InvoiceCounter,
    RecleanArea,
    RecleanRequest,
    RecleanStatus,
    Review,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# الأخطاء
# ------------------------------------------------------------
class AftercareError(Exception):
    code = "aftercare_error"


class BookingNotFoundError(AftercareError):
    code = "booking_not_found"


class ReviewNotAllowedError(AftercareError):
    """The clean is not completed yet, so it cannot be rated."""

    code = "review_not_allowed"


class AlreadyReviewedError(AftercareError):
    """This booking has already been rated; a rating cannot be changed."""

    code = "already_reviewed"


class ReviewRequiredError(AftercareError):
    """Rate your last completed clean before requesting a new one."""

    code = "review_required"

    def __init__(self, booking):
        self.booking_id = booking.id
        super().__init__(
            f"Please rate your last clean (booking {booking.public_reference}) before requesting a new one."
        )


class RecleanNotEligibleError(AftercareError):
    """This booking is not covered by the re-clean guarantee."""

    code = "reclean_not_eligible"


class RecleanWindowClosedError(AftercareError):
    """The re-clean guarantee window for this booking has closed."""

    code = "reclean_window_closed"


class RecleanAlreadyOpenError(AftercareError):
    """A re-clean request for this booking is already waiting for a decision."""

    code = "reclean_already_open"


class InvalidRecleanRequestError(AftercareError):
    """Choose at least one valid area."""

    code = "invalid_reclean_request"


class RecleanRequestNotFoundError(AftercareError):
    code = "reclean_request_not_found"


class RecleanAlreadyDecidedError(AftercareError):
    """This re-clean request has already been approved or rejected."""

    code = "reclean_already_decided"


class InvoiceNotAvailableError(AftercareError):
    """An invoice exists only once the booking is paid."""

    code = "invoice_not_available"


def _customer_booking(user, booking_id, lock=False):
    from apps.bookings.models import Booking

    qs = Booking.objects.select_related("job", "assigned_contractor")
    if lock:
        qs = qs.select_for_update(of=("self",))
    booking = qs.filter(pk=booking_id, customer=user).first()
    if booking is None:
        raise BookingNotFoundError("Booking not found.")
    return booking


def _completed_job(booking):
    from apps.jobs.models import JobStatus

    job = getattr(booking, "job", None)
    if job is None or job.status != JobStatus.COMPLETED:
        return None
    return job


def _emit(event, *args):
    from apps.notifications.hooks import emit_on_commit

    emit_on_commit(event, *args)


# ------------------------------------------------------------
# التقييم — إلزامي، مرة واحدة، بعد الاكتمال
# ------------------------------------------------------------
@transaction.atomic
def create_review(user, booking_id, stars, comment=""):
    booking = _customer_booking(user, booking_id, lock=True)
    if _completed_job(booking) is None or booking.assigned_contractor_id is None:
        raise ReviewNotAllowedError("You can rate the clean once it is completed.")
    if Review.objects.filter(booking=booking).exists():
        raise AlreadyReviewedError("This clean has already been rated.")

    review = Review(
        booking=booking,
        customer=user,
        contractor_id=booking.assigned_contractor_id,
        stars=stars,
        comment=(comment or "").strip(),
    )
    review.full_clean()
    review.save()
    _emit("review_received", review)
    logger.info("Review created (booking_id=%s, stars=%s)", booking.id, stars)
    return review


def pending_review_booking(user):
    """أقدم حجز مكتمل لم يقيّمه العميل بعد، أو None."""
    from apps.bookings.models import Booking
    from apps.jobs.models import JobStatus

    return (
        Booking.objects.filter(customer=user, job__status=JobStatus.COMPLETED, review__isnull=True)
        .order_by("job__confirmed_at")
        .first()
    )


def assert_no_pending_review(user):
    """التقييم إلزامي: لا طلب جديد قبل تقييم التنظيف المكتمل السابق."""
    booking = pending_review_booking(user)
    if booking is not None:
        raise ReviewRequiredError(booking)


def contractor_rating(profile):
    """(متوسط مقرّب لمنزلتين أو None، عدد التقييمات)."""
    result = Review.objects.filter(contractor=profile).aggregate(avg=Avg("stars"), count=Count("id"))
    avg = result["avg"]
    return (round(Decimal(str(avg)), 2) if avg is not None else None), result["count"]


def review_summary(booking):
    """ما تحتاجه مخرجات الحجز: التقييم إن وُجد، وهل هو مطلوب الآن."""
    try:
        review = booking.review
    except Review.DoesNotExist:
        review = None
    required = review is None and _completed_job(booking) is not None
    return review, required


# ------------------------------------------------------------
# ضمان إعادة التنظيف — 72 ساعة، والأدمن يقرر
# ------------------------------------------------------------
def reclean_deadline(booking):
    """
    آخر لحظة لطلب إعادة التنظيف، أو None إن لم يكن الحجز مشمولًا.

    المشمول: مهمة مكتملة، وخدمة واحدة على الأقل فعّلت الإدارة ضمانها.
    """
    job = _completed_job(booking)
    if job is None or job.confirmed_at is None:
        return None
    # يقرأ الأسطر المجلوبة مسبقًا (prefetch في قائمة الحجوزات) — لا استعلام لكل حجز
    if not any(sel.service_type.reclean_guarantee for sel in booking.service_selections.all()):
        return None
    return job.confirmed_at + timedelta(hours=settings.RECLEAN_GUARANTEE_HOURS)


@transaction.atomic
def create_reclean_request(user, booking_id, areas, details=""):
    booking = _customer_booking(user, booking_id, lock=True)
    deadline = reclean_deadline(booking)
    if deadline is None:
        raise RecleanNotEligibleError("This clean is not covered by the re-clean guarantee.")
    if timezone.now() > deadline:
        raise RecleanWindowClosedError("The re-clean guarantee window has closed.")

    areas = list(dict.fromkeys(areas or []))
    if not areas or any(a not in RecleanArea.values for a in areas):
        raise InvalidRecleanRequestError(
            f"Choose at least one area: {', '.join(RecleanArea.values)}."
        )
    if RecleanRequest.objects.filter(booking=booking, status=RecleanStatus.SUBMITTED).exists():
        raise RecleanAlreadyOpenError("A re-clean request for this booking is already under review.")

    try:
        with transaction.atomic():
            request = RecleanRequest.objects.create(
                booking=booking, customer=user, areas=areas, details=(details or "").strip()
            )
    except IntegrityError as exc:  # سباق بين طلبين متزامنين
        raise RecleanAlreadyOpenError("A re-clean request for this booking is already under review.") from exc
    logger.info("Re-clean requested (booking_id=%s, request_id=%s)", booking.id, request.id)
    return request


def list_reclean_requests_for_customer(user, booking_id):
    booking = _customer_booking(user, booking_id)
    return list(booking.reclean_requests.all())


def list_reclean_requests(actor, status=None):
    from apps.audit.services.backoffice import assert_admin

    assert_admin(actor)
    qs = RecleanRequest.objects.select_related("booking", "customer").order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    return qs


def get_reclean_request(actor, request_id):
    from apps.audit.services.backoffice import assert_admin

    assert_admin(actor)
    request = RecleanRequest.objects.select_related("booking", "customer").filter(pk=request_id).first()
    if request is None:
        raise RecleanRequestNotFoundError("Re-clean request not found.")
    return request


@transaction.atomic
def decide_reclean_request(actor, request_id, status, note="", request=None):
    """
    الأدمن يقبل أو يرفض. 📌 القبول يسجّل القرار ويبلّغ العميل؛ ترتيب زيارة
    إعادة التنظيف نفسها يتم يدويًا من فريق التشغيل حتى يُحسم شكلها الآلي.
    """
    from apps.audit.services.audit import record
    from apps.audit.services.backoffice import assert_admin

    assert_admin(actor)
    reclean = RecleanRequest.objects.select_for_update().filter(pk=request_id).first()
    if reclean is None:
        raise RecleanRequestNotFoundError("Re-clean request not found.")
    if reclean.status != RecleanStatus.SUBMITTED:
        raise RecleanAlreadyDecidedError(f"This request is already {reclean.status}.")
    if status not in (RecleanStatus.APPROVED, RecleanStatus.REJECTED):
        raise InvalidRecleanRequestError("Decision must be APPROVED or REJECTED.")

    reclean.status = status
    reclean.decision_note = (note or "").strip()
    reclean.decided_by = actor
    reclean.decided_at = timezone.now()
    reclean.save(update_fields=["status", "decision_note", "decided_by", "decided_at", "updated_at"])
    record(
        actor, "reclean_request.decide", target=reclean,
        details={"status": status, "note": reclean.decision_note, "booking_id": str(reclean.booking_id)},
        request=request,
    )
    _emit("reclean_decided", reclean)
    return reclean


# ------------------------------------------------------------
# الفاتورة — INV-YYYY-NNNNNN، بلا GST
# ------------------------------------------------------------
INVOICE_PREFIX = "INV"


def _next_invoice_number(year):
    counter, _ = InvoiceCounter.objects.get_or_create(year=year)
    counter = InvoiceCounter.objects.select_for_update().get(pk=counter.pk)
    counter.last_number += 1
    counter.save(update_fields=["last_number"])
    return f"{INVOICE_PREFIX}-{year}-{counter.last_number:06d}"


def _paid_payment(booking):
    from apps.payments.models import PaymentStatus

    payment = getattr(booking, "payment", None)
    if payment is None or payment.status not in (PaymentStatus.SUCCEEDED, PaymentStatus.REFUNDED):
        return None
    return payment


@transaction.atomic
def get_or_issue_invoice(booking):
    """
    يعيد فاتورة الحجز، ويصدرها أول مرة إن كان مدفوعًا.

    🔒 الرقم تسلسلي بلا فجوات ولا تكرار: قفل صف عداد السنة، وقيد التفرد على
       الحجز يمنع فاتورتين لحجز واحد في سباق.
    """
    existing = Invoice.objects.filter(booking=booking).first()
    if existing is not None:
        return existing
    if _paid_payment(booking) is None:
        raise InvoiceNotAvailableError("The invoice is available once the booking is paid.")

    now = timezone.now()
    year = now.astimezone(ZoneInfo(booking.customer_timezone or "Australia/Sydney")).year
    try:
        with transaction.atomic():
            return Invoice.objects.create(booking=booking, number=_next_invoice_number(year), issued_at=now)
    except IntegrityError:
        return Invoice.objects.get(booking=booking)


def build_invoice(booking, invoice):
    """
    البنود من اللقطات المجمَّدة: أسعار الخدمات من الاقتباس، ورسم المسافة من
    العرض المقبول، والإجمالي من الدفعة نفسها. فرق التقريب بند مستقل كي
    يطابق المجموع ما دُفع فعلًا.
    """
    from apps.bookings.models import DispatchOfferStatus
    from apps.bookings.services.scheduling import to_local

    payment = _paid_payment(booking) or booking.payment
    lines = []
    if booking.quote_id is not None and booking.quote is not None:
        for entry in booking.quote.service_snapshot:
            amount = Decimal(entry["room_price"]) * int(entry["room_count"]) + Decimal(entry["base_price"])
            lines.append({"description": entry["name"], "quantity": int(entry["room_count"]), "amount": amount})
    else:
        for sel in booking.service_selections.select_related("service_type"):
            service = sel.service_type
            lines.append({
                "description": service.name,
                "quantity": sel.room_count,
                "amount": service.room_price * sel.room_count + service.base_price,
            })

    offer = booking.dispatch_offers.filter(status=DispatchOfferStatus.ACCEPTED).first()
    travel_fee = offer.travel_fee if offer is not None and offer.travel_fee is not None else Decimal("0")
    if travel_fee:
        lines.append({"description": "Travel", "quantity": 1, "amount": travel_fee})
    difference = payment.amount - sum((line["amount"] for line in lines), Decimal("0"))
    if difference:
        lines.append({"description": "Rounding", "quantity": 1, "amount": difference})

    address = getattr(booking.property, "address", None)
    job = getattr(booking, "job", None)
    service_moment = (job.started_at if job else None) or booking.scheduled_at or booking.requested_at
    service_date = to_local(service_moment, booking.customer_timezone) if service_moment else None
    summary = payment.method_summary or {}
    return {
        "number": invoice.number,
        "issued_at": invoice.issued_at,
        "booking_id": booking.id,
        "public_reference": booking.public_reference,
        "service_date": service_date.date() if service_date else None,
        "service_address": (
            f"{address.street_address}, {address.suburb} {address.state} {address.postcode}"
            if address is not None else ""
        ),
        "lines": lines,
        "total": payment.amount,
        "currency": booking.currency,
        "gst_included": False,
        "payment": {
            "method": payment.method,
            "display_name": summary.get("display_name") or payment.method,
            "status": payment.status,
            "paid_at": payment.paid_at,
            "refunded_amount": payment.refunded_amount,
        },
    }


def invoice_for_customer(user, booking_id):
    booking = _customer_booking(user, booking_id)
    return build_invoice(booking, get_or_issue_invoice(booking))


def invoice_for_admin(actor, booking_id):
    from apps.audit.services.backoffice import assert_admin
    from apps.bookings.models import Booking

    assert_admin(actor)
    booking = Booking.objects.filter(pk=booking_id).first()
    if booking is None:
        raise BookingNotFoundError("Booking not found.")
    return build_invoice(booking, get_or_issue_invoice(booking))
