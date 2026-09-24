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

import uuid

from typing import Union

from ninja import Router
from apps.accounts.authentication import ActiveUserJWTAuth

from apps.accounts.roles import ConfirmedRole

from ..services import payments as svc
from .schemas import ErrorOut, PaymentActionOut, PaymentAdminOut, PaymentOut

router = Router(tags=["Payments"], auth=ActiveUserJWTAuth())


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


def _serialize_action(payment):
    return PaymentActionOut(
        id=payment.id,
        booking_id=payment.booking_id,
        amount=payment.amount,
        method=payment.method,
        status=payment.status,
        failure_reason=payment.failure_reason,
        created_at=payment.created_at,
        updated_at=payment.updated_at,
        attempt_number=payment.attempt_number,
        action_payload=payment.action_payload,
    )


@router.post(
    "/payments/{payment_id}/retry",
    response={200: PaymentActionOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut},
    summary="Retry a failed payment (booking owner only)",
    description=(
        "Retries a FAILED or REQUIRES_ACTION payment using the frozen booking "
        "amount. The optional payment method reference is an opaque provider "
        "token; raw card data is never accepted or stored."
    ),
)
def retry_payment(request, payment_id: uuid.UUID, payment_method_reference: str = ""):
    try:
        payment = svc.retry_payment(
            request.user,
            payment_id,
            payment_method_reference=payment_method_reference or None,
        )
    except svc.PaymentNotFoundError as exc:
        return _not_found()
    except svc.PaymentNotRetryableError as exc:
        return _error(409, exc.code, str(exc))
    except svc.OfferNoLongerReservedError as exc:
        return _error(409, exc.code, str(exc))
    except svc.PaymentError as exc:
        return _error(422, exc.code, str(exc))

    return 200, _serialize_action(payment)


@router.post(
    "/payments/{payment_id}/confirm-action",
    response={200: PaymentActionOut, 404: ErrorOut, 409: ErrorOut, 501: ErrorOut},
    summary="Confirm a payment authentication action (booking owner only)",
)
def confirm_payment_action(request, payment_id: uuid.UUID):
    try:
        payment = svc.confirm_payment_action(request.user, payment_id)
    except svc.PaymentNotFoundError:
        return _not_found()
    except svc.PaymentActionNotAvailableError as exc:
        return _error(409, exc.code, str(exc))
    except NotImplementedError as exc:
        return _error(501, "payment_action_not_supported", str(exc))

    return 200, _serialize_action(payment)


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
def retrieve_payment(request, booking_id: uuid.UUID):
    """
    🔒 404 موحّد لكل حالات التعذّر — لا يكشف الرد وجود حجز لغير صاحبه.
    """
    try:
        payment = svc.get_payment_by_booking_id(request.user, booking_id)
    except (svc.PaymentPermissionError, svc.PaymentNotFoundError):
        return _not_found()

    is_admin = request.user.has_admin_access()
    return 200, _serialize(payment, as_admin=is_admin)
