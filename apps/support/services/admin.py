"""
Back-office — طلبات الدعم (ADMIN).

📌 تغيير الحالة صار متاحًا للوحة التحكم المخصّصة (إلى جانب لوحة Django).
   الانتقالات المسموحة وحدها:
       SUBMITTED    → UNDER_REVIEW
       SUBMITTED    → RESOLVED
       UNDER_REVIEW → RESOLVED
   RESOLVED نهائية (نفس قاعدة SupportRequest.clean).

⚠️ ما زال لا ردود ولا مرفقات ولا تعيين موظف — راجع models.py.
"""

import logging

from django.db import transaction
from django.db.models import Count, Q

from apps.audit.services.audit import record
from apps.audit.services.backoffice import assert_admin, filter_date_range

from ..models import SupportRequest, SupportStatus

logger = logging.getLogger(__name__)

ALLOWED_TRANSITIONS = {
    SupportStatus.SUBMITTED: {SupportStatus.UNDER_REVIEW, SupportStatus.RESOLVED},
    SupportStatus.UNDER_REVIEW: {SupportStatus.RESOLVED},
    SupportStatus.RESOLVED: set(),
}


class SupportAdminError(Exception):
    code = "support_admin_error"


class SupportNotFoundError(SupportAdminError):
    code = "support_not_found"


class RequestResolvedError(SupportAdminError):
    """RESOLVED نهائية — لا يُعاد فتح طلب محلول."""

    code = "request_resolved"


class InvalidSupportTransitionError(SupportAdminError):
    code = "invalid_status_transition"


def list_requests(
    actor,
    status=None,
    category=None,
    user_id=None,
    booking_id=None,
    q=None,
    created_from=None,
    created_to=None,
):
    assert_admin(actor)

    qs = SupportRequest.objects.select_related("user", "booking").order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    if category:
        qs = qs.filter(category=category)
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    if booking_id is not None:
        qs = qs.filter(booking_id=booking_id)
    if q:
        qs = qs.filter(message__icontains=q.strip())
    return filter_date_range(qs, "created_at", created_from, created_to)


def get_request(actor, request_id):
    assert_admin(actor)

    obj = SupportRequest.objects.select_related("user", "booking").filter(pk=request_id).first()
    if obj is None:
        raise SupportNotFoundError("Support request not found.")
    return obj


@transaction.atomic
def change_status(actor, request_id, new_status, request=None):
    assert_admin(actor)

    obj = (
        SupportRequest.objects.select_for_update()
        .select_related("user", "booking")
        .filter(pk=request_id)
        .first()
    )
    if obj is None:
        raise SupportNotFoundError("Support request not found.")

    previous = obj.status
    if previous == SupportStatus.RESOLVED:
        raise RequestResolvedError("A resolved request cannot be changed.")
    if new_status not in ALLOWED_TRANSITIONS[previous]:
        raise InvalidSupportTransitionError(
            f"Cannot move a support request from {previous} to {new_status}."
        )

    obj.status = new_status
    obj.save(update_fields=["status", "updated_at"])

    record(
        actor,
        "support_request.status",
        target=obj,
        details={"from": previous, "to": new_status},
        request=request,
    )
    logger.info(
        "Support request status changed (id=%s, %s -> %s, by=%s)",
        obj.id,
        previous,
        new_status,
        actor.id,
    )
    return obj


def summary_counts():
    return SupportRequest.objects.aggregate(
        open=Count("id", filter=~Q(status=SupportStatus.RESOLVED)),
    )
