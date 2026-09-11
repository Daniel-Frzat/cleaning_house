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
