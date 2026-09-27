"""
أحداث الإشعارات — المصدر الوحيد لنصوص الإشعارات (قائمة معتمدة من PO — 2026-09-25).

النصوص بالإنجليزية (السوق أسترالي). كل حدث دالة تستقبل كائن النطاق وتبني
الإشعار؛ النطاقات تستدعيها عبر hooks.emit_on_commit بعد نجاح المعاملة.

| type                        | المستلم | الأولوية |
|-----------------------------|---------|----------|
| offer.new                   | مقاول   | HIGH     |
| job.confirmed               | مقاول   | NORMAL   |
| booking.confirmed           | عميل    | NORMAL   |
| verification.approved       | مقاول   | NORMAL   |
| verification.rejected       | مقاول   | NORMAL   |
| job.completed               | مقاول   | NORMAL   |
| payout.sent                 | مقاول   | NORMAL   |
| payment.failed              | عميل    | HIGH     |
| payment.action_required     | عميل    | HIGH     |
| job.arrived                 | عميل    | NORMAL   |
| job.started                 | عميل    | NORMAL   |
| job.awaiting_confirmation   | عميل    | NORMAL   |
| support.updated             | مقدّم الطلب | NORMAL |
| offer.cancelled             | مقاول   | NORMAL   |
| job.confirmation_reminder   | عميل    | NORMAL   |
| job.auto_confirmed          | عميل    | NORMAL   |
| payment.refunded            | عميل    | NORMAL   |
| review.received             | مقاول   | NORMAL   |
| reclean.updated             | عميل    | NORMAL   |

⚠️ لا إشعار عند "لم يُعثر على عامل" (NO_CONTRACTOR) — قرار PO صريح،
   والقرار المفتوح #16 باقٍ كما هو.
"""

from decimal import Decimal

from django.utils import timezone

from ..models import Audience, Priority
from .notifications import notify


# ------------------------------------------------------------
# تنسيق
# ------------------------------------------------------------
def _money(amount):
    if amount is None:
        return ""
    return f"${Decimal(amount):,.2f}"


def _when(booking):
    """'Tue 9:00 AM' بتوقيت العقار، أو 'as soon as possible' للطلب الفوري."""
    if booking.scheduled_at is None:
        return "as soon as possible"
    from apps.bookings.services.scheduling import to_local

    local = to_local(booking.scheduled_at, booking.customer_timezone)
    return local.strftime("%a %I:%M %p").replace(" 0", " ")


def _suburb(booking):
    address = getattr(booking.property, "address", None)
    return getattr(address, "suburb", "") or "your area"


def _contractor_user(booking):
    profile = booking.assigned_contractor
    return profile.user if profile is not None else None


# ------------------------------------------------------------
# المقاول
# ------------------------------------------------------------
def offer_new(offer):
    booking = offer.booking
    minutes = max(int((offer.expires_at - timezone.now()).total_seconds() // 60), 1)
    amount = _money(offer.contractor_earnings or offer.total_amount)
    return notify(
        offer.contractor.user,
        "offer.new",
        Audience.CONTRACTOR,
        "New job offer",
        f"{amount} in {_suburb(booking)}, {_when(booking)}. Respond within {minutes} min.".lstrip(),
        data={"offer_id": offer.id, "booking_id": booking.id},
        priority=Priority.HIGH,
    )


def job_confirmed(booking):
    return notify(
        _contractor_user(booking),
        "job.confirmed",
        Audience.CONTRACTOR,
        "Job confirmed",
        f"{_suburb(booking)}, {_when(booking)}. The customer has paid.",
        data={"booking_id": booking.id},
    )


def verification_reviewed(document):
    from apps.contractors.models import BusinessRegistration, VerificationStatus

    label = "ABN" if isinstance(document, BusinessRegistration) else "insurance"
    user = document.contractor.user
    if document.status == VerificationStatus.VERIFIED:
        return notify(
            user, "verification.approved", Audience.CONTRACTOR,
            "Document approved", f"Your {label} has been verified.",
            data={"document_id": document.id, "document_type": label},
        )
    return notify(
        user, "verification.rejected", Audience.CONTRACTOR,
        "Document needs attention",
        f"Your {label} was not approved: {document.rejection_reason}",
        data={"document_id": document.id, "document_type": label},
    )


def job_completed(job):
    return notify(
        _contractor_user(job.booking),
        "job.completed",
        Audience.CONTRACTOR,
        "Job completed",
        "The customer confirmed the job.",
        data={"booking_id": job.booking_id, "job_id": job.id},
    )


def payout_sent(payout):
    return notify(
        payout.contractor,
        "payout.sent",
        Audience.CONTRACTOR,
        "Payout sent",
        f"{_money(payout.amount)} is on its way.",
        data={"booking_id": payout.booking_id, "payout_id": payout.id},
    )


# ------------------------------------------------------------
# العميل
# ------------------------------------------------------------
def booking_confirmed(booking):
    return notify(
        booking.customer,
        "booking.confirmed",
        Audience.CUSTOMER,
        "Cleaner confirmed",
        f"Your cleaning on {_when(booking)} is booked.",
        data={"booking_id": booking.id},
    )


def payment_failed(payment):
    return notify(
        payment.booking.customer,
        "payment.failed",
        Audience.CUSTOMER,
        "Payment failed",
        "Update your payment method to keep your booking.",
        data={"booking_id": payment.booking_id, "payment_id": payment.id},
        priority=Priority.HIGH,
    )


def payment_action_required(payment):
    return notify(
        payment.booking.customer,
        "payment.action_required",
        Audience.CUSTOMER,
        "Action needed",
        "Confirm your payment to secure your cleaner.",
        data={"booking_id": payment.booking_id, "payment_id": payment.id},
        priority=Priority.HIGH,
    )


def review_received(review):
    stars = "★" * review.stars
    return notify(
        review.contractor.user,
        "review.received",
        Audience.CONTRACTOR,
        "New rating",
        f"A customer rated your clean {stars} ({review.stars}/5).",
        data={"booking_id": review.booking_id, "review_id": review.id},
    )


def reclean_decided(reclean):
    from apps.aftercare.models import RecleanStatus

    if reclean.status == RecleanStatus.APPROVED:
        title, body = "Re-clean approved", "We'll be in touch to arrange your free re-clean."
    else:
        title, body = "Re-clean request reviewed", "Your re-clean request was not approved."
    if reclean.decision_note:
        body = f"{body} {reclean.decision_note}"
    return notify(
        reclean.customer,
        "reclean.updated",
        Audience.CUSTOMER,
        title,
        body,
        data={"booking_id": reclean.booking_id, "reclean_request_id": reclean.id, "status": reclean.status},
    )


def payment_refunded(payment, amount):
    """استرداد بقرار الأدمن — كامل أو جزئي (قرار PO — 2026-09-27)."""
    return notify(
        payment.booking.customer,
        "payment.refunded",
        Audience.CUSTOMER,
        "Refund sent",
        f"Your refund of {_money(amount)} has been sent to your payment method.",
        data={"booking_id": payment.booking_id, "payment_id": payment.id, "amount": str(amount)},
    )


def job_arrived(job):
    return notify(
        job.booking.customer,
        "job.arrived",
        Audience.CUSTOMER,
        "Your cleaner has arrived",
        "Your cleaner is at the property.",
        data={"booking_id": job.booking_id, "job_id": job.id},
    )


def job_started(job):
    return notify(
        job.booking.customer,
        "job.started",
        Audience.CUSTOMER,
        "Cleaning started",
        "Your cleaner has started.",
        data={"booking_id": job.booking_id, "job_id": job.id},
    )


def job_confirmation_reminder(job):
    """بعد JOB_CONFIRM_REMINDER_HOURS بلا تأكيد (قرار PO — 2026-09-27)."""
    from django.conf import settings

    remaining = settings.JOB_AUTO_CONFIRM_HOURS - settings.JOB_CONFIRM_REMINDER_HOURS
    return notify(
        job.booking.customer,
        "job.confirmation_reminder",
        Audience.CUSTOMER,
        "Please confirm your clean",
        f"Review the photos and confirm. It will be confirmed automatically in {remaining} hours.",
        data={"booking_id": job.booking_id, "job_id": job.id},
    )


def job_auto_confirmed(job):
    from django.conf import settings

    return notify(
        job.booking.customer,
        "job.auto_confirmed",
        Audience.CUSTOMER,
        "Clean confirmed",
        f"We confirmed your clean automatically after {settings.JOB_AUTO_CONFIRM_HOURS} hours. "
        "Please rate your cleaner.",
        data={"booking_id": job.booking_id, "job_id": job.id},
    )


def job_awaiting_confirmation(job):
    return notify(
        job.booking.customer,
        "job.awaiting_confirmation",
        Audience.CUSTOMER,
        "Please confirm",
        "Your cleaner marked the job done. Review the photos.",
        data={"booking_id": job.booking_id, "job_id": job.id},
    )


# ------------------------------------------------------------
# الدعم — مقدّم الطلب (عميل أو مقاول)
# ------------------------------------------------------------
def support_updated(support_request):
    from apps.support.models import SupportStatus

    state = {
        SupportStatus.UNDER_REVIEW: "under review",
        SupportStatus.RESOLVED: "resolved",
    }.get(support_request.status)
    if state is None:
        return None
    return notify(
        support_request.user,
        "support.updated",
        Audience.ALL,
        "Support update",
        f"Your request is now {state}.",
        data={"support_request_id": support_request.id},
    )


def offer_cancelled(offer, booking):
    """الحجز أُلغي والعرض كان حيًّا أو محجوزًا لهذا المقاول."""
    return notify(
        offer.contractor.user,
        "offer.cancelled",
        Audience.CONTRACTOR,
        "Job cancelled",
        f"The customer cancelled the job in {_suburb(booking)}. No action needed.",
        data={"offer_id": offer.id, "booking_id": booking.id},
    )
