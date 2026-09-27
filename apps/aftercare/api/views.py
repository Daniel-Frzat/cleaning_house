"""
ما بعد التنظيف — مسارات العميل تحت /api/bookings والإدارة تحت /api/admin.

    POST /api/bookings/{id}/review                التقييم (إلزامي، مرة واحدة)
    POST /api/bookings/{id}/reclean-requests      طلب إعادة تنظيف ضمن 72 ساعة
    GET  /api/bookings/{id}/reclean-requests
    GET  /api/bookings/{id}/invoice               الفاتورة (بعد الدفع)
    GET  /api/admin/reclean-requests              قائمة الطلبات
    GET  /api/admin/reclean-requests/{id}
    PATCH /api/admin/reclean-requests/{id}        قبول/رفض
    GET  /api/admin/bookings/{id}/invoice
"""

import uuid
from typing import Optional

from ninja import Query, Router

from apps.accounts.authentication import ActiveUserJWTAuth, AdminJWTAuth
from apps.audit.api.common import ErrorOut, PageQuery, error, forbidden, page
from apps.audit.services.backoffice import AdminRequiredError

from ..models import RecleanStatus
from ..services import aftercare as svc
from .schemas import (
    AdminRecleanListOut,
    AdminRecleanRequestOut,
    InvoiceOut,
    RecleanDecisionIn,
    RecleanRequestIn,
    RecleanRequestOut,
    ReviewIn,
    ReviewOut,
)

booking_router = Router(tags=["Aftercare"], auth=ActiveUserJWTAuth())
admin_router = Router(tags=["Admin — Aftercare"], auth=AdminJWTAuth())


def _review(review):
    return {
        "id": review.id,
        "booking_id": review.booking_id,
        "stars": review.stars,
        "comment": review.comment,
        "created_at": review.created_at,
    }


def _reclean(request):
    return {
        "id": request.id,
        "booking_id": request.booking_id,
        "areas": request.areas,
        "details": request.details,
        "status": request.status,
        "decision_note": request.decision_note,
        "decided_at": request.decided_at,
        "created_at": request.created_at,
    }


def _admin_reclean(request):
    return {
        **_reclean(request),
        "customer_id": request.customer_id,
        "public_reference": request.booking.public_reference,
    }


# ------------------------------------------------------------
# العميل
# ------------------------------------------------------------
@booking_router.post(
    "/{booking_id}/review",
    response={201: ReviewOut, 404: ErrorOut, 409: ErrorOut},
    summary="Rate the cleaner (mandatory, once, after completion)",
    description=(
        "`stars` 1–5 and an optional `comment`. Allowed once the job is `COMPLETED` "
        "(including auto-confirmed), and only once per booking "
        "(`409 already_reviewed`); a rating cannot be edited.\\n\\n"
        "**Mandatory:** while a completed booking is unrated, `POST /api/bookings` "
        "is refused with `409 review_required`. `BookingOut.review_required` marks "
        "the booking to rate."
    ),
)
def create_review(request, booking_id: uuid.UUID, payload: ReviewIn):
    try:
        review = svc.create_review(request.user, booking_id, payload.stars, payload.comment)
    except svc.BookingNotFoundError as exc:
        return error(404, exc.code, str(exc))
    except (svc.ReviewNotAllowedError, svc.AlreadyReviewedError) as exc:
        return error(409, exc.code, str(exc))
    return 201, _review(review)


@booking_router.post(
    "/{booking_id}/reclean-requests",
    response={201: RecleanRequestOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut},
    summary="Request a free re-clean (guarantee window)",
    description=(
        "Within `RECLEAN_GUARANTEE_HOURS` (72) of completion, for bookings that "
        "include a service covered by the guarantee (`BookingOut.reclean_eligible_until` "
        "is set). `areas`: KITCHEN, BATHROOMS, BEDROOMS, LIVING_AREAS, WINDOWS, OTHER.\\n\\n"
        "An admin approves or rejects it; the customer receives `reclean.updated`. "
        "No limit on requests, but only one can wait for a decision at a time "
        "(`409 reclean_already_open`). Photos will be added once storage is live."
    ),
)
def create_reclean_request(request, booking_id: uuid.UUID, payload: RecleanRequestIn):
    try:
        reclean = svc.create_reclean_request(
            request.user, booking_id, [a.value for a in payload.areas], payload.details
        )
    except svc.BookingNotFoundError as exc:
        return error(404, exc.code, str(exc))
    except (svc.RecleanNotEligibleError, svc.RecleanWindowClosedError, svc.RecleanAlreadyOpenError) as exc:
        return error(409, exc.code, str(exc))
    except svc.InvalidRecleanRequestError as exc:
        return error(422, exc.code, str(exc))
    return 201, _reclean(reclean)


@booking_router.get(
    "/{booking_id}/reclean-requests",
    response={200: list[RecleanRequestOut], 404: ErrorOut},
    summary="My re-clean requests for a booking",
)
def list_reclean_requests(request, booking_id: uuid.UUID):
    try:
        return 200, [_reclean(r) for r in svc.list_reclean_requests_for_customer(request.user, booking_id)]
    except svc.BookingNotFoundError as exc:
        return error(404, exc.code, str(exc))


@booking_router.get(
    "/{booking_id}/invoice",
    response={200: InvoiceOut, 404: ErrorOut, 409: ErrorOut},
    summary="Invoice for a paid booking",
    description=(
        "Issued on first request once the payment has succeeded "
        "(`409 invoice_not_available` before). The number `INV-YYYY-NNNNNN` is "
        "sequential per year and never changes. Lines come from the frozen prices; "
        "no GST line. PDF download will follow."
    ),
)
def customer_invoice(request, booking_id: uuid.UUID):
    try:
        return 200, svc.invoice_for_customer(request.user, booking_id)
    except svc.BookingNotFoundError as exc:
        return error(404, exc.code, str(exc))
    except svc.InvoiceNotAvailableError as exc:
        return error(409, exc.code, str(exc))


# ------------------------------------------------------------
# الإدارة
# ------------------------------------------------------------
class RecleanListQuery(PageQuery):
    status: Optional[RecleanStatus] = None


@admin_router.get(
    "/reclean-requests",
    response={200: AdminRecleanListOut, 403: ErrorOut},
    summary="Re-clean requests (admin only)",
)
def admin_list_reclean_requests(request, filters: Query[RecleanListQuery]):
    try:
        qs = svc.list_reclean_requests(request.user, status=filters.status)
    except AdminRequiredError as exc:
        return forbidden(exc)
    return 200, page(qs, filters, _admin_reclean)


@admin_router.get(
    "/reclean-requests/{request_id}",
    response={200: AdminRecleanRequestOut, 403: ErrorOut, 404: ErrorOut},
    summary="One re-clean request (admin only)",
)
def admin_get_reclean_request(request, request_id: uuid.UUID):
    try:
        return 200, _admin_reclean(svc.get_reclean_request(request.user, request_id))
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.RecleanRequestNotFoundError as exc:
        return error(404, exc.code, str(exc))


@admin_router.patch(
    "/reclean-requests/{request_id}",
    response={200: AdminRecleanRequestOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    summary="Approve or reject a re-clean request (admin only)",
    description=(
        "`status`: APPROVED or REJECTED, with an optional `note` shown to the "
        "customer. Decided once (`409 reclean_already_decided`). The customer "
        "receives `reclean.updated`. Arranging the re-clean visit itself is done by "
        "the operations team for now. Recorded in the audit log."
    ),
)
def admin_decide_reclean_request(request, request_id: uuid.UUID, payload: RecleanDecisionIn):
    try:
        reclean = svc.decide_reclean_request(
            request.user, request_id, payload.status, payload.note, request=request
        )
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.RecleanRequestNotFoundError as exc:
        return error(404, exc.code, str(exc))
    except svc.RecleanAlreadyDecidedError as exc:
        return error(409, exc.code, str(exc))
    return 200, _admin_reclean(reclean)


@admin_router.get(
    "/bookings/{booking_id}/invoice",
    response={200: InvoiceOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    summary="Invoice for a paid booking (admin only)",
)
def admin_invoice(request, booking_id: uuid.UUID):
    try:
        return 200, svc.invoice_for_admin(request.user, booking_id)
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.BookingNotFoundError as exc:
        return error(404, exc.code, str(exc))
    except svc.InvoiceNotAvailableError as exc:
        return error(409, exc.code, str(exc))
