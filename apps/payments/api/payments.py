"""
Payments API — Payment Domain (Change Set §36.4)

نقطة النهاية (محمية بـJWT):
    GET /api/bookings/{id}/payment   حالة الدفع لحجز

🔒 الصلاحية: CUSTOMER مالك الحجز، أو ADMIN. غيرهما 404 — نفس سياسة
   apps/bookings: لا نكشف وجود حجز/دفعة لغير صاحبها.

🔒 provider_reference لا يُكشف للعميل إطلاقًا — والحجب **هيكلي**: يُختار
   شكل المخرجات حسب الدور قبل الإرجاع (PaymentAdminOut للإدارة،
   PaymentOut العام لغيرها)، فالمفتاح غائب كليًا من JSON لغير الإدارة
   لا مجرد null. (تصحيح رجعي لعيب §43.)

⚠️ ConfirmedRole مستورد هنا لغرض العرض (الحجب) لا للتحكم في الوصول:
   قرار الوصول كله في طبقة الخدمة. لا استعلام ORM في هذا الملف.

⚠️ لا نقطة نهاية للشحن هنا: الشحن يقع تلقائيًا لحظة تأكيد الحجز
   (apps/bookings/services/offers.py) ولا يُطلقه العميل يدويًا.
"""

from typing import Union

from ninja import Router
from ninja_jwt.authentication import JWTAuth

from apps.accounts.roles import ConfirmedRole

from ..services import payments as svc
from .schemas import ErrorOut, PaymentAdminOut, PaymentOut

router = Router(tags=["Payments"], auth=JWTAuth())


def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


def _not_found():
    """رد موحّد: لا حجز، أو لا دفعة، أو ليس لك — لا تمييز بينها."""
    return _error(404, "payment_not_found", "No payment found for this booking.")


def _serialize(payment, *, as_admin):
    """
    يبني نسخة الشكل المناسب للدور — الاختيار يسبق الإرجاع.

    🔒 غير الإدارة يحصل على PaymentOut الذي لا يُعرِّف provider_reference
       إطلاقًا، فالمفتاح غائب من JSON لا موجودًا بقيمة null.
    """
    common = {
        "id": payment.id,
        "booking_id": payment.booking_id,
        "amount": payment.amount,
        "method": payment.method,
        "status": payment.status,
        "failure_reason": payment.failure_reason,
        "created_at": payment.created_at,
        "updated_at": payment.updated_at,
    }

    if as_admin:
        return PaymentAdminOut(
            **common, provider_reference=payment.provider_reference
        )

    return PaymentOut(**common)


@router.get(
    "/{booking_id}/payment",
    # ⚠️ الشكل العام **أولًا** في الاتحاد: Pydantic يجرّب الأعضاء بالترتيب،
    #    فلو سبق PaymentAdminOut لَوسّع نسخة الشكل العام وأعاد إضافة
    #    provider_reference بقيمة null — وهو بالضبط العيب المُصلَح هنا.
    response={200: Union[PaymentOut, PaymentAdminOut], 404: ErrorOut},
    summary="Retrieve the payment for a booking (owner customer or admin)",
    description=(
        "**Who may call:** the booking's own customer, or an `ADMIN`.\n\n"
        "**Preconditions:** a payment record exists only once a contractor has "
        "accepted the offer — the customer is charged directly at confirmation. "
        "There is no escrow and no separate capture step, so there is no endpoint "
        "to start or retry a payment; this one is read-only.\n\n"
        "A `FAILED` payment means the charge did not go through while the booking "
        "remains confirmed.\n\n"
        "**Side effects:** none — read-only.\n\n"
        "Administrators additionally receive `provider_reference`; for the "
        "customer that field is absent from the response body altogether, not "
        "merely blank."
    ),
    openapi_extra={
        "responses": {
            404: {
                "description": (
                    "No such booking, no payment for it, or the caller is not "
                    "entitled to see it — deliberately indistinguishable."
                )
            }
        }
    },
)
def retrieve_payment(request, booking_id: str):
    """
    🔒 404 موحّد لكل حالات التعذّر — لا يكشف الرد وجود حجز لغير صاحبه.
    """
    try:
        payment = svc.get_payment_by_booking_id(request.user, booking_id)
    except (svc.PaymentPermissionError, svc.PaymentNotFoundError):
        return _not_found()

    is_admin = request.user.has_admin_access()
    return 200, _serialize(payment, as_admin=is_admin)
