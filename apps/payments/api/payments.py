"""
Payments API — Payment Domain (Change Set §36.4)

نقطة النهاية (محمية بـJWT):
    GET /api/bookings/{id}/payment   حالة الدفع لحجز

🔒 الصلاحية: CUSTOMER مالك الحجز، أو ADMIN. غيرهما 404 — نفس سياسة
   apps/bookings: لا نكشف وجود حجز/دفعة لغير صاحبها.

🔒 provider_reference لا يُكشف للعميل إطلاقًا (تفصيل تشخيصي داخلي):
   يُحجب بناءً على دور الطالب، لا على وجود القيمة.

⚠️ ConfirmedRole مستورد هنا لغرض العرض (الحجب) لا للتحكم في الوصول:
   قرار الوصول كله في طبقة الخدمة. لا استعلام ORM في هذا الملف.

⚠️ لا نقطة نهاية للشحن هنا: الشحن يقع تلقائيًا لحظة تأكيد الحجز
   (apps/bookings/services/offers.py) ولا يُطلقه العميل يدويًا.
"""

from ninja import Router
from ninja_jwt.authentication import JWTAuth

from apps.accounts.roles import ConfirmedRole

from ..services import payments as svc
from .schemas import ErrorOut, PaymentOut

router = Router(tags=["Payments"], auth=JWTAuth())


def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


def _not_found():
    """رد موحّد: لا حجز، أو لا دفعة، أو ليس لك — لا تمييز بينها."""
    return _error(404, "payment_not_found", "No payment found for this booking.")


def _serialize(payment, *, include_provider_reference):
    """
    🔒 provider_reference يُملأ فقط للإدارة. للعميل يبقى None دائمًا.
    """
    return {
        "id": payment.id,
        "booking_id": payment.booking_id,
        "amount": payment.amount,
        "method": payment.method,
        "status": payment.status,
        "provider_reference": (
            payment.provider_reference if include_provider_reference else None
        ),
        "failure_reason": payment.failure_reason,
        "created_at": payment.created_at,
        "updated_at": payment.updated_at,
    }


@router.get(
    "/{booking_id}/payment",
    response={200: PaymentOut, 404: ErrorOut},
    summary="Retrieve the payment for a booking (owner customer or admin)",
)
def retrieve_payment(request, booking_id: str):
    """
    🔒 404 موحّد لكل حالات التعذّر — لا يكشف الرد وجود حجز لغير صاحبه.
    """
    try:
        payment = svc.get_payment_by_booking_id(request.user, booking_id)
    except (svc.PaymentPermissionError, svc.PaymentNotFoundError):
        return _not_found()

    is_admin = request.user.role == ConfirmedRole.ADMIN
    return 200, _serialize(payment, include_provider_reference=is_admin)
