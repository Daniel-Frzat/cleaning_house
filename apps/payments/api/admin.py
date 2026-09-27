"""
Back-office — دفعات العملاء.

    GET  /api/admin/payments                         ADMIN
    GET  /api/admin/payments/{payment_id}            ADMIN
    POST /api/admin/payments/{payment_id}/reconcile  SUPERUSER

⚠️ لا مسار شحن ولا إعادة محاولة ولا استرداد — راجع services/admin.py.
⚠️ action_payload غير معروض: يحمل بيانات مصادقة 3-D Secure الخاصة بالعميل.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from ninja import Query, Router, Schema
from pydantic import Field

from apps.accounts.authentication import AdminJWTAuth, SuperuserJWTAuth
from apps.audit.api.common import CreatedRangeQuery, ErrorOut, error, forbidden, page
from apps.audit.services.backoffice import (
    AdminRequiredError,
    SuperuserRequiredError,
    is_unknown_outcome,
)

from ..models import PaymentStatus
from ..services import admin as svc

router = Router(tags=["Admin — Payments"], auth=AdminJWTAuth())


class PaymentFilters(CreatedRangeQuery):
    status: Optional[PaymentStatus] = None
    booking_id: Optional[uuid.UUID] = None
    needs_reconciliation: Optional[bool] = None


class AdminPaymentOut(Schema):
    id: uuid.UUID
    booking_id: uuid.UUID
    public_reference: str
    customer_id: uuid.UUID
    amount: Decimal
    method: str
    status: str
    attempt_number: int
    provider_reference: Optional[str] = None
    provider_error_code: str
    failure_reason: Optional[str] = None
    needs_reconciliation: bool
    paid_at: Optional[datetime] = None
    refunded_amount: Decimal = Decimal("0")
    refunded_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class AdminPaymentListOut(Schema):
    count: int
    items: list[AdminPaymentOut]


class AdminPaymentDetailOut(AdminPaymentOut):
    method_summary: Optional[dict] = None
    has_pending_action: bool


class ReconcileOutcome(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ReconcileIn(Schema):
    outcome: ReconcileOutcome
    provider_reference: Optional[str] = Field(None, max_length=255)
    note: str = Field(..., min_length=1, max_length=1000, pattern=r"\S")


class PaymentReconcileOut(AdminPaymentDetailOut):
    booking_confirmed: bool


class RefundIn(Schema):
    amount: Optional[Decimal] = Field(
        None, gt=0, max_digits=10, decimal_places=2,
        description="Omit to refund the whole unrefunded balance.",
    )
    reason: str = Field(..., min_length=1, max_length=1000, pattern=r"\S")


def _serialize(payment):
    return {
        "id": payment.id,
        "booking_id": payment.booking_id,
        "public_reference": payment.booking.public_reference,
        "customer_id": payment.booking.customer_id,
        "amount": payment.amount,
        "method": payment.method,
        "status": payment.status,
        "attempt_number": payment.attempt_number,
        "provider_reference": payment.provider_reference,
        "provider_error_code": payment.provider_error_code,
        "failure_reason": payment.failure_reason,
        "needs_reconciliation": payment.status == PaymentStatus.PROCESSING
        and is_unknown_outcome(payment.failure_reason),
        "paid_at": payment.paid_at,
        "refunded_amount": payment.refunded_amount,
        "refunded_at": payment.refunded_at,
        "created_at": payment.created_at,
        "updated_at": payment.updated_at,
    }


def _serialize_detail(payment):
    return {
        **_serialize(payment),
        "method_summary": payment.method_summary,
        "has_pending_action": payment.action_payload is not None,
    }


@router.get(
    "/payments",
    response={200: AdminPaymentListOut, 403: ErrorOut},
    summary="List customer payments (admin only)",
    description=(
        "`needs_reconciliation=true` returns `PROCESSING` payments whose provider "
        "call raised (outcome unknown — `failure_reason` starts with `provider_error`)."
    ),
)
def list_payments(request, filters: PaymentFilters = Query(...)):
    try:
        qs = svc.list_payments(
            request.user,
            status=filters.status,
            booking_id=filters.booking_id,
            needs_reconciliation=filters.needs_reconciliation,
            created_from=filters.created_from,
            created_to=filters.created_to,
        )
    except AdminRequiredError as exc:
        return forbidden(exc)
    return 200, page(qs, filters, _serialize)


@router.get(
    "/payments/{payment_id}",
    response={200: AdminPaymentDetailOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve a customer payment (admin only)",
)
def retrieve_payment(request, payment_id: uuid.UUID):
    try:
        payment = svc.get_payment(request.user, payment_id)
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.PaymentNotFoundError as exc:
        return error(404, exc.code, str(exc))
    return 200, _serialize_detail(payment)


@router.post(
    "/payments/{payment_id}/reconcile",
    auth=SuperuserJWTAuth(),
    response={
        200: PaymentReconcileOut,
        403: ErrorOut,
        404: ErrorOut,
        409: ErrorOut,
        422: ErrorOut,
    },
    summary="Record the provider outcome of an unknown-outcome payment (superuser only)",
    description=(
        "Only for a `PROCESSING` payment whose provider call raised "
        "(`failure_reason` starts with `provider_error`); anything else is `409`. "
        "The provider is **not** called — this records what the superuser verified "
        "with the provider.\n\n"
        "`SUCCEEDED` (requires `provider_reference`) marks the payment paid and runs "
        "the normal confirmation (contractor assigned, booking `CONFIRMED`, job "
        "created). If the accepted offer is no longer reserved the payment stays "
        "`SUCCEEDED` and `booking_confirmed` is `false` — what happens next is an "
        "open product decision.\n\n"
        "`FAILED` marks the payment failed with `note` as the reason; the customer "
        "may retry from the app. Recorded in the audit log."
    ),
)
def reconcile_payment(request, payment_id: uuid.UUID, payload: ReconcileIn):
    try:
        payment, confirmed = svc.reconcile_payment(
            request.user,
            payment_id,
            outcome=payload.outcome.value,
            note=payload.note.strip(),
            provider_reference=(payload.provider_reference or "").strip() or None,
            request=request,
        )
    except SuperuserRequiredError as exc:
        return error(403, exc.code, str(exc))
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.PaymentNotFoundError as exc:
        return error(404, exc.code, str(exc))
    except svc.ProviderReferenceRequiredError as exc:
        return error(422, exc.code, str(exc))
    except svc.NotReconcilableError as exc:
        return error(409, exc.code, str(exc))
    return 200, {**_serialize_detail(payment), "booking_confirmed": confirmed}


@router.post(
    "/payments/{payment_id}/refund",
    response={200: AdminPaymentDetailOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut, 502: ErrorOut},
    summary="Refund a customer payment, in full or in part (admin only)",
    description=(
        "For an unhappy clean: the admin decides the amount. Omit `amount` to "
        "refund the whole unrefunded balance; send it for a partial refund. "
        "Several partial refunds add up, never beyond the amount paid.\n\n"
        "Only a `SUCCEEDED` payment can be refunded (`409 not_refundable`). A full "
        "refund sets the status to `REFUNDED`; a partial one keeps `SUCCEEDED` "
        "and increases `refunded_amount`.\n\n"
        "The refund goes through the payment provider (`502 refund_failed` if it "
        "refuses). The customer receives a `payment.refunded` notification. The "
        "cleaner's payout is not changed. Recorded in the audit log."
    ),
)
def refund_payment(request, payment_id: uuid.UUID, payload: RefundIn):
    try:
        payment = svc.refund_payment(
            request.user, payment_id, reason=payload.reason.strip(), amount=payload.amount, request=request
        )
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.PaymentNotFoundError as exc:
        return error(404, exc.code, str(exc))
    except svc.NotRefundableError as exc:
        return error(409, exc.code, str(exc))
    except svc.InvalidRefundAmountError as exc:
        return error(422, exc.code, str(exc))
    except svc.RefundFailedError as exc:
        return error(502, exc.code, str(exc))
    return 200, _serialize_detail(payment)
