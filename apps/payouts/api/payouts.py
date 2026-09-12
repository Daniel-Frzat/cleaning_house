"""
Payouts API — Payout Domain (Change Set §36.5)

نقطة النهاية (محمية بـJWT):
    GET /api/bookings/{id}/payout   حالة دفع المقاول لحجز

🔒 الصلاحية: المقاول المستحِق، أو ADMIN. غيرهما 404 — نفس سياسة بقية
   النطاقات: لا نكشف وجود مورد لغير أصحابه.

🔒 العميل لا يرى دفعة المقاول إطلاقًا: ما يدفعه العميل شأنه (Payment)،
   وما يستلمه المقاول شأن المقاول والإدارة.

🔒 provider_reference للإدارة وحدها — والحجب **هيكلي**: يُختار شكل
   المخرجات حسب الدور قبل الإرجاع (PayoutAdminOut للإدارة، PayoutOut
   العام لغيرها)، فالمفتاح غائب كليًا من JSON لغير الإدارة لا مجرد null.

⚠️ لا نقطة نهاية لإطلاق الدفع: الدفع يقع تلقائيًا لحظة تأكيد العميل
   (apps/jobs/services/jobs.py) ولا يُطلقه المقاول ولا الإدارة يدويًا.

⚠️ لا استعلام ORM في هذا الملف: كل شيء عبر طبقة الخدمة (راجع §43).
"""

from typing import Union

from ninja import Router
from ninja_jwt.authentication import JWTAuth

from apps.accounts.roles import ConfirmedRole

from ..services import payouts as svc
from .schemas import ErrorOut, PayoutAdminOut, PayoutOut

router = Router(tags=["Payouts"], auth=JWTAuth())


def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


def _not_found():
    """رد موحّد: لا حجز، أو لا دفعة، أو ليست لك — لا تمييز بينها."""
    return _error(404, "payout_not_found", "No payout found for this booking.")


def _serialize(payout, *, as_admin):
    """
    يبني نسخة الشكل المناسب للدور — الاختيار يسبق الإرجاع.

    🔒 غير الإدارة يحصل على PayoutOut الذي لا يُعرِّف provider_reference
       إطلاقًا، فالمفتاح غائب من JSON لا موجودًا بقيمة null.
    """
    common = {
        "id": payout.id,
        "booking_id": payout.booking_id,
        "contractor_id": payout.contractor_id,
        "amount": payout.amount,
        "status": payout.status,
        "failure_reason": payout.failure_reason,
        "created_at": payout.created_at,
        "updated_at": payout.updated_at,
    }

    if as_admin:
        return PayoutAdminOut(
            **common, provider_reference=payout.provider_reference
        )

    return PayoutOut(**common)


@router.get(
    "/{booking_id}/payout",
    # ⚠️ الشكل العام **أولًا** في الاتحاد: Pydantic يجرّب الأعضاء بالترتيب،
    #    فلو سبق PayoutAdminOut لَوسّع نسخة الشكل العام وأعاد إضافة
    #    provider_reference بقيمة null — وهو بالضبط العيب المُصلَح هنا.
    response={200: Union[PayoutOut, PayoutAdminOut], 404: ErrorOut},
    summary="Retrieve the contractor payout for a booking (payee contractor or admin)",
)
def retrieve_payout(request, booking_id: str):
    """
    🔒 404 موحّد لكل حالات التعذّر — لا يكشف الرد وجود حجز لغير أصحابه.
    """
    try:
        payout = svc.get_payout_by_booking_id(request.user, booking_id)
    except (svc.PayoutPermissionError, svc.PayoutNotFoundError):
        return _not_found()

    is_admin = request.user.role == ConfirmedRole.ADMIN
    return 200, _serialize(payout, as_admin=is_admin)
