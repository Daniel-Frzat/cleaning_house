"""
Payment Service — Payment Domain (Change Set §36.4، §8؛ Infra §2، §14/§16)

شحن مباشر لحظة تأكيد الحجز. كل الطفرات تمر من هنا — طبقة الـAPI لا تستدعي
`.objects` مباشرة.

⚠️ سياسة غير محسومة (مُعلَّمة صراحةً): ماذا يعني فشل الدفع بالنسبة للحجز؟
   لا إلغاء تلقائي ولا تغيير لحالة الحجز هنا. الحجز يبقى CONFIRMED والدفعة
   FAILED، والقرار (إعادة محاولة؟ إلغاء؟ مهلة سداد؟) متروك لمرحلة لاحقة.
   لا تُضف سلوكًا افتراضيًا في هذا الملف.

🔒 التكرارية (Infra §14/§16): مفتاح التكرارية مشتق من booking.id وثابت،
   وقيد OneToOne على قاعدة البيانات يمنع وجود دفعتين لحجز واحد. استدعاء
   الدالة مرتين للحجز نفسه يعيد الدفعة القائمة ولا يشحن ثانيةً.
"""

import logging

from django.db import IntegrityError, transaction

from apps.bookings.models import Booking, BookingStatus

from ..adapters import get_payment_adapter
from ..models import Payment, PaymentMethod, PaymentStatus

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    """أصل أخطاء نطاق الدفع."""

    code = "payment_error"


class PaymentPermissionError(PaymentError):
    """لا صلاحية لعرض هذه الدفعة."""

    code = "payment_forbidden"


class PaymentNotFoundError(PaymentError):
    code = "payment_not_found"


class BookingNotConfirmedError(PaymentError):
    """لا شحن قبل تأكيد الحجز (§36.4)."""

    code = "booking_not_confirmed"


class MissingPriceError(PaymentError):
    """لا شحن بلا لقطة سعر مثبَّتة."""

    code = "booking_has_no_price"


class PaymentAlreadyExistsError(PaymentError):
    """الحجز مشحون سابقًا — حجز واحد = دفعة واحدة."""

    code = "payment_already_exists"


def build_idempotency_key(booking):
    """
    مفتاح ثابت مشتق من معرّف الحجز.

    ⚠️ ثابت عمدًا (لا وقت ولا عشوائية): إعادة الاستدعاء للحجز نفسه تصل
       المزوّد بالمفتاح نفسه، فلا يتكرر الشحن حتى لو فشل الاتصال ووصل
       الطلب مرتين (Infra §14/§16).
    """
    return f"booking-{booking.id}"


@transaction.atomic
def charge_for_booking(booking, method=PaymentMethod.CARD):
    """
    يشحن قيمة الحجز مباشرة بعد تأكيده، ويعيد الـPayment في الحالتين.

    ⚠️ يعيد الدفعة سواء نجحت أو فشلت — المستدعي يقرر معنى الفشل. لا
       يلمس booking.status إطلاقًا (سياسة غير محسومة، راجع docstring).

    Raises:
        BookingNotConfirmedError: الحجز ليس CONFIRMED.
        MissingPriceError:        لا لقطة سعر.
        PaymentAlreadyExistsError: للحجز دفعة سابقة.
    """
    if booking.status != BookingStatus.CONFIRMED:
        raise BookingNotConfirmedError(
            f"Booking must be CONFIRMED before charging (currently {booking.status})."
        )

    if booking.computed_price is None:
        raise MissingPriceError("Booking has no computed price; nothing to charge.")

    # دفاع في العمق: فحص في الخدمة + قيد OneToOne في قاعدة البيانات
    existing = Payment.objects.filter(booking=booking).first()
    if existing is not None:
        raise PaymentAlreadyExistsError(
            "This booking already has a payment; it cannot be charged twice."
        )

    payment = Payment(
        booking=booking,
        amount=booking.computed_price,
        method=method,
        status=PaymentStatus.PENDING,
    )
    payment.full_clean()

    try:
        payment.save()
    except IntegrityError as exc:
        # سباق: أنشأ عاملٌ آخر الدفعة بيننا وبين الفحص أعلاه
        raise PaymentAlreadyExistsError(
            "This booking already has a payment; it cannot be charged twice."
        ) from exc

    adapter = get_payment_adapter()
    result = adapter.charge(
        amount=payment.amount,
        method=payment.method,
        idempotency_key=build_idempotency_key(booking),
    )

    if result.success:
        payment.status = PaymentStatus.SUCCEEDED
        payment.provider_reference = result.provider_reference
        payment.failure_reason = None
    else:
        payment.status = PaymentStatus.FAILED
        payment.provider_reference = result.provider_reference
        payment.failure_reason = result.failure_reason

    payment.save(
        update_fields=["status", "provider_reference", "failure_reason", "updated_at"]
    )

    logger.info(
        "Payment %s (payment_id=%s, booking_id=%s, amount=%s)",
        payment.status,
        payment.id,
        booking.id,
        payment.amount,
    )

    # ⚠️ booking.status لا يُمس هنا مهما كانت النتيجة — سياسة مفتوحة.
    return payment


def get_payment_by_booking_id(user, booking_id):
    """
    يعيد دفعة حجز بمعرّف الحجز — بما في ذلك جلب الحجز نفسه.

    🔒 طبقة الـAPI لا تستعلم عن الحجز بنفسها: الاستعلام والصلاحية معًا
       يعيشان هنا. الحجز غير الموجود يرفع PaymentNotFoundError تمامًا
       كالحجز بلا دفعة — فلا تملك الواجهة ما تميّز به بين الحالتين.
    """
    booking = Booking.objects.filter(pk=booking_id).first()
    if booking is None:
        raise PaymentNotFoundError("No payment found for this booking.")

    return get_payment_for_booking(user, booking)


def get_payment_for_booking(user, booking):
    """
    يعيد دفعة حجز بعد فحص الصلاحية.

    🔒 CUSTOMER مالك الحجز، أو ADMIN. أي شخص آخر يُرفض — والتمييز بين
       "لا صلاحية" و"لا دفعة" يتركه المستدعي لطبقة الـAPI.
    """
    from apps.accounts.roles import ConfirmedRole

    if user is None or not user.is_authenticated:
        raise PaymentPermissionError("Authentication required.")

    is_admin = user.role == ConfirmedRole.ADMIN
    is_owner = booking.customer_id == user.id

    if not (is_admin or is_owner):
        logger.warning(
            "Payment access denied (user_id=%s, booking_id=%s)", user.id, booking.id
        )
        raise PaymentPermissionError("You do not have access to this payment.")

    payment = Payment.objects.filter(booking=booking).first()
    if payment is None:
        raise PaymentNotFoundError("No payment exists for this booking.")

    return payment
