"""
Offer Response API — Booking Domain (Change Set §36.1، §36.6)

نقاط النهاية (محمية بـJWT، وكلها CONTRACTOR فقط):
    POST /api/contractor/offers/{id}/accept    قبول عرض موجَّه إليه
    POST /api/contractor/offers/{id}/decline   رفضه (يُطلق التتابع)

🔒 فحص الملكية على مستوى الكائن: العرض يجب أن يكون موجَّهًا لملف مقاول
   هذا المستخدم تحديدًا — 403 وإلا، كما تنص المواصفة.

🔒 العرض المنتهي أو المُجاب عليه سابقًا يُرفض صراحةً بـ409 وليس قبولًا
   صامتًا: المقاول يجب أن يعرف أن ردّه لم يُسجَّل.

📌 القبول هو لحظة كشف السعر (§36.1) — قبلها لا سعر في أي رد.
"""

from django.core.exceptions import ValidationError
from ninja import Router
from ninja_jwt.authentication import JWTAuth

from ..services import offers as svc
from .schemas import ErrorOut, OfferOut, OfferResponseOut

router = Router(tags=["Contractor Offers"], auth=JWTAuth())


def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


def _serialize_offer(offer):
    """
    ⚠️ لا يكشف سعرًا: العرض لا يحمل سعرًا أصلًا، والسعر يعيش على الحجز
       بعد القبول وحده.
    """
    return {
        "id": offer.id,
        "booking_id": offer.booking_id,
        "contractor_id": offer.contractor_id,
        "status": offer.status,
        "distance_km": offer.distance_km,
        "offered_at": offer.offered_at,
        "responded_at": offer.responded_at,
        "expires_at": offer.expires_at,
    }


def _handle_offer_errors(exc):
    """يوحّد تحويل أخطاء العروض إلى ردود HTTP."""
    if isinstance(exc, svc.InvalidContractorRoleError):
        return _error(403, exc.code, str(exc))
    if isinstance(exc, svc.OfferPermissionError):
        return _error(403, exc.code, str(exc))
    if isinstance(exc, svc.SelfAssignmentError):
        # 403 لا 409: المنع دائم لهذا المستخدم على هذا الحجز، ولا تغيّره
        # إعادة المحاولة ولا تبدّل حالة المورد.
        return _error(403, exc.code, str(exc))
    if isinstance(exc, svc.OfferNotFoundError):
        return _error(404, exc.code, "Offer not found.")
    if isinstance(exc, svc.OfferNotActionableError):
        # 409: تعارض مع حالة المورد الحالية — ليس خطأ في صيغة الطلب
        return _error(409, exc.code, str(exc))
    return None


# ------------------------------------------------------------
# POST /contractor/offers/{id}/accept
# ------------------------------------------------------------
@router.post(
    "/offers/{offer_id}/accept",
    response={200: OfferResponseOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut},
    summary="Accept a dispatch offer (contractor only)",
    description=(
        "**Who may call:** `CONTRACTOR` only, and only the contractor the offer "
        "was addressed to. Another contractor's offer returns `403`.\n\n"
        "**Preconditions:** the offer must still be actionable — neither already "
        "answered nor expired.\n\n"
        "**This is the pivotal moment of the booking lifecycle.** On success:\n\n"
        "1. The price is calculated and **frozen** onto the booking. It is never "
        "recalculated afterwards, even if catalog prices change.\n"
        "2. The booking moves to `CONFIRMED`, the contractor is assigned, and the "
        "price becomes visible to the customer — this is the first moment it is.\n"
        "3. The customer is charged directly (there is no escrow).\n"
        "4. The job is created and starts `IN_PROGRESS`.\n\n"
        "The distance used for pricing is the one recorded on **this offer**, not "
        "a freshly measured one, so moving the contractor's profile between offer "
        "and acceptance does not change the price.\n\n"
        "Steps 3 and 4 run after the confirmation is committed and are isolated "
        "from it: if the charge or the job creation fails, the booking stays "
        "validly confirmed and the failure is recorded rather than returned as an "
        "error here.\n\n"
        "`next_offer` is always `null` in this response — the cascade stops at "
        "acceptance."
    ),
    openapi_extra={
        "responses": {
            403: {
                "description": "The caller is not a `CONTRACTOR`, the offer is addressed to someone else, or the caller is the booking's own customer (a user cannot take their own booking)."
            },
            404: {"description": "No offer with this id."},
            409: {"description": "The offer was already answered or has expired."},
            422: {"description": "The offer could not be accepted — validation failed."},
        }
    },
)
def accept_offer(request, offer_id: str):
    """
    📌 عند النجاح: يُحسب السعر ويُثبَّت على الحجز، ويصبح CONFIRMED.
       المسافة المستخدمة هي مسافة هذا العرض المخزَّنة، لا مسافة مُعاد حسابها.
    """
    try:
        offer = svc.accept_offer(request.user, offer_id)
    except (
        svc.InvalidContractorRoleError,
        svc.OfferPermissionError,
        svc.SelfAssignmentError,
        svc.OfferNotFoundError,
        svc.OfferNotActionableError,
    ) as exc:
        return _handle_offer_errors(exc)
    except ValidationError as exc:
        return _error(422, "validation_error", "; ".join(exc.messages))
    except svc.OfferError as exc:
        return _error(422, exc.code, str(exc))

    return 200, {"offer": _serialize_offer(offer), "next_offer": None}


# ------------------------------------------------------------
# POST /contractor/offers/{id}/decline
# ------------------------------------------------------------
@router.post(
    "/offers/{offer_id}/decline",
    response={200: OfferResponseOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut},
    summary="Decline a dispatch offer (contractor only)",
    description=(
        "**Who may call:** `CONTRACTOR` only, and only the contractor the offer "
        "was addressed to.\n\n"
        "**Preconditions:** the offer must still be actionable — neither already "
        "answered nor expired.\n\n"
        "**Side effects:** declining immediately cascades the booking to the next "
        "nearest available contractor, exactly as an expiry would. The new "
        "offer's id is returned in `next_offer`, with no details about the "
        "contractor behind it.\n\n"
        "If no further contractor is eligible, `next_offer` is `null` and the "
        "booking stays `PENDING` with no active offer. That is a normal outcome, "
        "not an error; nothing cancels the booking automatically.\n\n"
        "No price is calculated or revealed by declining."
    ),
    openapi_extra={
        "responses": {
            403: {
                "description": "The caller is not a `CONTRACTOR`, the offer is addressed to someone else, or the caller is the booking's own customer (a user cannot take their own booking)."
            },
            404: {"description": "No offer with this id."},
            409: {"description": "The offer was already answered or has expired."},
            422: {"description": "The offer could not be declined."},
        }
    },
)
def decline_offer(request, offer_id: str):
    """
    ⚠️ يُطلق التتابع فورًا. إن لم يوجد مقاول تالٍ يبقى الحجز PENDING
       بلا عرض نشط (القرار المفتوح #16) — وهذا ليس خطأ.

    🔒 next_offer في الرد يحمل معرّف العرض التالي فقط دون تفاصيل المقاول
       — المقاول الرافض لا يحتاج معرفة من خلفه.
    """
    try:
        offer, next_offer = svc.decline_offer(request.user, offer_id)
    except (
        svc.InvalidContractorRoleError,
        svc.OfferPermissionError,
        svc.SelfAssignmentError,
        svc.OfferNotFoundError,
        svc.OfferNotActionableError,
    ) as exc:
        return _handle_offer_errors(exc)
    except svc.OfferError as exc:
        return _error(422, exc.code, str(exc))

    return 200, {
        "offer": _serialize_offer(offer),
        "next_offer": str(next_offer.id) if next_offer else None,
    }
