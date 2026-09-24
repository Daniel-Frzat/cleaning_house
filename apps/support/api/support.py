"""
Support API — Support Domain (MVP)

نقاط النهاية (كلها محمية بـJWT، ومتاحة لأي دور):
    POST /api/support-requests        فتح طلب دعم
    GET  /api/support-requests        قائمة طلبات المستخدم (الإدارة: الكل)
    GET  /api/support-requests/{id}   قراءة طلب

📌 لا بوابة دور هنا: الدعم متاح لكل مستخدم مصادَق عليه — العميل
   والمقاول والإدارة. الصلاحية ملكية لا دور.

🔒 طلب يخصّ مستخدمًا آخر يعيد 404 لا 403 — نفس سياسة apps/bookings
   و apps/payments: لا نكشف وجود مورد لغير مالكه.

⚠️ لا مسار لتغيير الحالة: التغيير من لوحة Django وحدها في هذه المرحلة.
⚠️ لا مرفقات ولا ردود — راجع models.py.
"""

import uuid

from django.core.exceptions import ValidationError
from ninja import Query, Router
from apps.accounts.authentication import ActiveUserJWTAuth

from ..services import support as svc
from .schemas import ErrorOut, SupportRequestIn, SupportRequestOut

router = Router(tags=["Support"], auth=ActiveUserJWTAuth())


# ------------------------------------------------------------
# أدوات مشتركة
# ------------------------------------------------------------
def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


def _not_found():
    """رد موحّد: غير موجود، أو موجود لكن ليس ملكًا للطالب."""
    return _error(404, "support_not_found", "Support request not found.")


def _validation_error(exc):
    """يحوّل ValidationError من الـModel إلى رد 422 مفهوم."""
    if hasattr(exc, "message_dict"):
        detail = "; ".join(
            f"{field}: {' '.join(msgs)}" for field, msgs in exc.message_dict.items()
        )
    else:
        detail = "; ".join(exc.messages)
    return _error(422, "validation_error", detail)


def _serialize(support_request):
    """
    🔒 قائمة حقول صريحة: ما لا يُبنى هنا لا يظهر في أي رد.
    """
    return {
        "id": support_request.id,
        "user_id": support_request.user_id,
        "booking_id": support_request.booking_id,
        "category": support_request.category,
        "message": support_request.message,
        "status": support_request.status,
        "created_at": support_request.created_at,
        "updated_at": support_request.updated_at,
    }


# ------------------------------------------------------------
# POST /support-requests
# ------------------------------------------------------------
@router.post(
    "",
    response={201: SupportRequestOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut},
    summary="Open a support request (any authenticated user)",
    description=(
        "**Who may call:** any authenticated user — customers, contractors and "
        "administrators alike.\n\n"
        "**Preconditions:** `category` must be one of the known categories and "
        "`message` must not be empty. `booking_id` is optional; when given it "
        "must be a booking the caller owns — a request about a login problem or "
        "an app crash needs no booking.\n\n"
        "The request is always created as `SUBMITTED`; `status` is not accepted "
        "from the client. There are no attachments and no replies in this "
        "version — the request is a one-way channel and is answered outside the "
        "app.\n\n"
        "**Side effects:** none beyond creating the record. No notification is "
        "sent and no ticket is opened with any external system."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "No valid token was supplied."},
            404: {
                "description": (
                    "The referenced booking does not exist, or belongs to another "
                    "customer — deliberately indistinguishable."
                )
            },
            422: {
                "description": (
                    "Unknown `category`, empty `message`, or the message exceeds "
                    "the maximum length."
                )
            },
        }
    },
)
def create_support_request(request, payload: SupportRequestIn):
    """📌 status لا يُقبل من العميل — كل طلب يبدأ SUBMITTED."""
    try:
        support_request = svc.create_support_request(
            request.user,
            category=payload.category,
            message=payload.message,
            booking_id=payload.booking_id,
        )
    except svc.SupportPermissionError as exc:
        return _error(403, exc.code, str(exc))
    except svc.InvalidBookingReferenceError:
        # 🔒 نفس رد "غير موجود" لحجز الغير — لا نكشف وجوده.
        return _error(404, "booking_not_found", "Booking not found.")
    except ValidationError as exc:
        return _validation_error(exc)
    except svc.SupportError as exc:
        return _error(422, exc.code, str(exc))

    return 201, _serialize(support_request)


# ------------------------------------------------------------
# GET /support-requests
# ------------------------------------------------------------
@router.get(
    "",
    response={200: list[SupportRequestOut], 403: ErrorOut},
    summary="List own support requests (all of them for an admin)",
    description=(
        "**Who may call:** any authenticated user. The list is scoped to the "
        "caller's own requests, newest first; an `ADMIN` receives every "
        "request from every user.\n\n"
        "**Paging:** `limit` (default 100, max 200) and `offset` query "
        "parameters; the response is still a plain array.\n\n"
        "**Side effects:** none — read-only."
    ),
    openapi_extra={"responses": {403: {"description": "No valid token was supplied."}}},
)
def list_support_requests(
    request, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)
):
    try:
        requests = svc.list_support_requests(request.user)
    except svc.SupportPermissionError as exc:
        return _error(403, exc.code, str(exc))

    return 200, [_serialize(r) for r in requests[offset : offset + limit]]


# ------------------------------------------------------------
# GET /support-requests/{id}
# ------------------------------------------------------------
@router.get(
    "/{request_id}",
    response={200: SupportRequestOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve one support request (owner or admin)",
    description=(
        "**Who may call:** the user who opened the request, or an `ADMIN`.\n\n"
        "**Side effects:** none — read-only.\n\n"
        "A request belonging to another user returns the same `404` as one that "
        "does not exist."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "No valid token was supplied."},
            404: {
                "description": (
                    "No such support request, or it belongs to another user."
                )
            },
        }
    },
)
def retrieve_support_request(request, request_id: uuid.UUID):
    """🔒 طلب الغير وطلب غير موجود يعيدان 404 نفسه."""
    try:
        support_request = svc.get_support_request(request.user, request_id)
    except (svc.SupportPermissionError, svc.SupportNotFoundError):
        return _not_found()

    return 200, _serialize(support_request)
