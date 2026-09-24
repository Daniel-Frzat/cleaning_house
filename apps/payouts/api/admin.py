"""
Back-office — دفعات المقاولين.

    GET  /api/admin/payouts                        ADMIN
    GET  /api/admin/payouts/{payout_id}            ADMIN
    POST /api/admin/payouts/{payout_id}/reconcile  SUPERUSER

⚠️ reconcile ليس مُحفِّزًا للدفع: لا يستدعي المزوّد ولا يُنشئ Payout —
   يسجّل نتيجة دفعة قائمة مجهولة النتيجة فقط.
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

from ..models import PayoutStatus
from ..services import admin as svc

router = Router(tags=["Admin — Payouts"], auth=AdminJWTAuth())


class PayoutFilters(CreatedRangeQuery):
    status: Optional[PayoutStatus] = None
    # معرّف ملف المقاول (ContractorProfile.id)
    contractor_id: Optional[uuid.UUID] = None
    needs_reconciliation: Optional[bool] = None


class AdminPayoutOut(Schema):
    id: uuid.UUID
    booking_id: uuid.UUID
    public_reference: str
    contractor_user_id: uuid.UUID
    contractor_phone: str
    amount: Decimal
    status: str
    provider_reference: Optional[str] = None
    failure_reason: Optional[str] = None
    needs_reconciliation: bool
    created_at: datetime
    updated_at: datetime


class AdminPayoutListOut(Schema):
    count: int
    items: list[AdminPayoutOut]


class ReconcileOutcome(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ReconcileIn(Schema):
    outcome: ReconcileOutcome
    provider_reference: Optional[str] = Field(None, max_length=255)
    note: str = Field(..., min_length=1, max_length=1000, pattern=r"\S")


def _serialize(payout):
    return {
        "id": payout.id,
        "booking_id": payout.booking_id,
        "public_reference": payout.booking.public_reference,
        "contractor_user_id": payout.contractor_id,
        "contractor_phone": payout.contractor.phone,
        "amount": payout.amount,
        "status": payout.status,
        "provider_reference": payout.provider_reference,
        "failure_reason": payout.failure_reason,
        "needs_reconciliation": payout.status == PayoutStatus.PENDING
        and is_unknown_outcome(payout.failure_reason),
        "created_at": payout.created_at,
        "updated_at": payout.updated_at,
    }


@router.get(
    "/payouts",
    response={200: AdminPayoutListOut, 403: ErrorOut},
    summary="List contractor payouts (admin only)",
)
def list_payouts(request, filters: PayoutFilters = Query(...)):
    try:
        qs = svc.list_payouts(
            request.user,
            status=filters.status,
            contractor_id=filters.contractor_id,
            needs_reconciliation=filters.needs_reconciliation,
            created_from=filters.created_from,
            created_to=filters.created_to,
        )
    except AdminRequiredError as exc:
        return forbidden(exc)
    return 200, page(qs, filters, _serialize)


@router.get(
    "/payouts/{payout_id}",
    response={200: AdminPayoutOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve a contractor payout (admin only)",
)
def retrieve_payout(request, payout_id: uuid.UUID):
    try:
        payout = svc.get_payout(request.user, payout_id)
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.PayoutNotFoundError as exc:
        return error(404, exc.code, str(exc))
    return 200, _serialize(payout)


@router.post(
    "/payouts/{payout_id}/reconcile",
    auth=SuperuserJWTAuth(),
    response={200: AdminPayoutOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut},
    summary="Record the provider outcome of an unknown-outcome payout (superuser only)",
    description=(
        "Only for a `PENDING` payout whose provider call raised (`failure_reason` "
        "starts with `provider_error`); anything else is `409`. The provider is not "
        "called and no retry is attempted. `SUCCEEDED` requires `provider_reference`; "
        "`FAILED` stores `note` as the failure reason. Recorded in the audit log."
    ),
)
def reconcile_payout(request, payout_id: uuid.UUID, payload: ReconcileIn):
    try:
        payout = svc.reconcile_payout(
            request.user,
            payout_id,
            outcome=payload.outcome.value,
            note=payload.note.strip(),
            provider_reference=(payload.provider_reference or "").strip() or None,
            request=request,
        )
    except SuperuserRequiredError as exc:
        return error(403, exc.code, str(exc))
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.PayoutNotFoundError as exc:
        return error(404, exc.code, str(exc))
    except svc.ProviderReferenceRequiredError as exc:
        return error(422, exc.code, str(exc))
    except svc.NotReconcilableError as exc:
        return error(409, exc.code, str(exc))
    return 200, _serialize(payout)
