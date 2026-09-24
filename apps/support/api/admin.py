"""
Back-office — طلبات الدعم (ADMIN فقط).

    GET   /api/admin/support-requests
    GET   /api/admin/support-requests/{request_id}
    PATCH /api/admin/support-requests/{request_id}   {status}
"""

import uuid
from datetime import datetime
from typing import Optional

from ninja import Query, Router, Schema
from pydantic import Field

from apps.accounts.authentication import AdminJWTAuth
from apps.audit.api.common import CreatedRangeQuery, ErrorOut, error, forbidden, page
from apps.audit.services.backoffice import AdminRequiredError

from ..models import SupportCategory, SupportStatus
from ..services import admin as svc

router = Router(tags=["Admin — Support"], auth=AdminJWTAuth())


class SupportFilters(CreatedRangeQuery):
    status: Optional[SupportStatus] = None
    category: Optional[SupportCategory] = None
    user_id: Optional[uuid.UUID] = None
    booking_id: Optional[uuid.UUID] = None
    q: Optional[str] = Field(None, max_length=255, description="message contains")


class AdminSupportRequestOut(Schema):
    id: uuid.UUID
    user_id: uuid.UUID
    user_phone: str
    user_name: str
    booking_id: Optional[uuid.UUID] = None
    booking_reference: Optional[str] = None
    category: str
    message: str
    status: str
    created_at: datetime
    updated_at: datetime


class AdminSupportRequestListOut(Schema):
    count: int
    items: list[AdminSupportRequestOut]


class SupportStatusPatch(Schema):
    status: SupportStatus


def _serialize(obj):
    return {
        "id": obj.id,
        "user_id": obj.user_id,
        "user_phone": obj.user.phone,
        "user_name": obj.user.full_name,
        "booking_id": obj.booking_id,
        "booking_reference": obj.booking.public_reference if obj.booking_id else None,
        "category": obj.category,
        "message": obj.message,
        "status": obj.status,
        "created_at": obj.created_at,
        "updated_at": obj.updated_at,
    }


@router.get(
    "/support-requests",
    response={200: AdminSupportRequestListOut, 403: ErrorOut},
    summary="List support requests (admin only)",
)
def list_support_requests(request, filters: SupportFilters = Query(...)):
    try:
        qs = svc.list_requests(
            request.user,
            status=filters.status,
            category=filters.category,
            user_id=filters.user_id,
            booking_id=filters.booking_id,
            q=filters.q,
            created_from=filters.created_from,
            created_to=filters.created_to,
        )
    except AdminRequiredError as exc:
        return forbidden(exc)
    return 200, page(qs, filters, _serialize)


@router.get(
    "/support-requests/{request_id}",
    response={200: AdminSupportRequestOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve a support request (admin only)",
)
def retrieve_support_request(request, request_id: uuid.UUID):
    try:
        obj = svc.get_request(request.user, request_id)
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.SupportNotFoundError as exc:
        return error(404, exc.code, str(exc))
    return 200, _serialize(obj)


@router.patch(
    "/support-requests/{request_id}",
    response={200: AdminSupportRequestOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    summary="Change a support request's status (admin only)",
    description=(
        "Allowed: `SUBMITTED → UNDER_REVIEW`, `SUBMITTED → RESOLVED`, "
        "`UNDER_REVIEW → RESOLVED`. `RESOLVED` is final (`409 request_resolved`); "
        "any other move is `409 invalid_status_transition`. Recorded in the audit log."
    ),
)
def update_support_request(request, request_id: uuid.UUID, payload: SupportStatusPatch):
    try:
        obj = svc.change_status(request.user, request_id, payload.status.value, request=request)
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.SupportNotFoundError as exc:
        return error(404, exc.code, str(exc))
    except svc.SupportAdminError as exc:
        return error(409, exc.code, str(exc))
    return 200, _serialize(obj)
