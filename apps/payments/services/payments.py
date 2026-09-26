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

🔒 المزوّد يُستدعى خارج أي معاملة قاعدة بيانات: سجل PENDING يُثبَّت أولًا،
   ثم يُستدعى المزوّد، ثم تُحدَّث النتيجة. لو كان الاستدعاء داخل المعاملة
   لأزال أي استثناء (timeout بعد خصم فعلي مثلًا) سجل الدفعة كليًا — مال
   تحرّك بلا أي أثر في النظام.

   استثناء من المزوّد يُبقي الدفعة PENDING مع سبب مسجَّل: النتيجة مجهولة
   (قد يكون الخصم تم)، فلا تُعلَّم FAILED ولا يُعاد الشحن تلقائيًا —
   تحتاج مطابقة مع المزوّد (webhook أو مراجعة إدارية).
"""

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.bookings.models import (
    Booking,
    BookingStatus,
    DispatchOfferStatus,
    DispatchStatus,
)

from ..adapters import get_payment_adapter
from ..models import (
    RETRYABLE_PAYMENT_STATUSES,
    Payment,
    PaymentMethod,
    PaymentStatus,
)

logger = logging.getLogger(__name__)

# عملة المنصة — السوق أسترالي حصرًا
CURRENCY = "AUD"


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


def build_idempotency_key(booking, attempt_number=1):
    """
    مفتاح تكرار لكل محاولة (§16).

    ⚠️ كان ثابتًا للحجز كله، وكان ذلك يمنع محاولة مشروعة بطريقة دفع
       مختلفة: المزوّد يرى المفتاح نفسه فيعيد نتيجة المحاولة الفاشلة
       السابقة بدل تنفيذ محاولة جديدة.

    📌 يبقى حتميًا داخل المحاولة الواحدة: إعادة إرسال الطلب نفسه (انقطاع
       شبكة) تصل بالمفتاح نفسه فلا تُنتج شحنًا مزدوجًا (Infra §14/§16).
    """
    return f"booking-{booking.id}-payment-attempt-{attempt_number}"


class PaymentNotRetryableError(PaymentError):
    """
    الدفعة ليست في حالة تسمح بإعادة المحاولة.

    409: الطلب سليم شكلًا والرفض بسبب حالة المورد.
    """

    code = "payment_not_retryable"


class OfferNoLongerReservedError(PaymentError):
    """
    العرض المقبول لم يعد محجوزًا، فلا مبلغ مجمَّد يُعاد شحنه.

    ⚠️ سلوك ما بعد انتهاء نافذة إعادة المحاولة قرار منتج مفتوح (§16):
       الحالة تُكشف ولا يُلغى الحجز تلقائيًا.
    """

    code = "offer_no_longer_reserved"


class PaymentActionNotAvailableError(PaymentError):
    code = "payment_action_not_available"


def _accepted_offer(booking):
    """العرض الذي يحجز هذا الحجز حاليًا، أو None."""
    return booking.dispatch_offers.filter(
        status__in=(
            DispatchOfferStatus.ACCEPTED_PENDING_PAYMENT,
            DispatchOfferStatus.ACCEPTED,
        )
    ).first()


@transaction.atomic
def start_charge_for_booking(booking):
    """
    يبدأ محاولة شحن بعد قبول المقاول — ولا يُسنِد أحدًا (§12).

    📌 المبلغ من العرض المقبول المجمَّد لا من الكتالوج: ما رآه المقاول هو
       ما يُشحن (§10).

    🔒 الإسناد وإنشاء المهمة لا يقعان هنا: يقعان في confirm_payment بعد
       تأكيد النجاح وحده (§14).

    🔒 سجل PROCESSING يُثبَّت في معاملة قصيرة **قبل** لمس المزوّد، والمزوّد
       يُستدعى خارجها: لو كان داخلها لمحا أي استثناء (timeout بعد خصم فعلي)
       سجل الدفعة كليًا — مال تحرّك بلا أثر في النظام.
    """
    with transaction.atomic():
        # 🔒 قفل الحجز: بدءان متزامنان لا يُنشئان دفعتين
        booking = Booking.objects.select_for_update().get(pk=booking.pk)

        offer = _accepted_offer(booking)
        if offer is None or offer.total_amount is None:
            raise MissingPriceError(
                "Booking has no accepted offer with a frozen price; nothing to charge."
            )

        existing = Payment.objects.filter(booking=booking).first()
        if existing is not None:
            # 📌 تكرارية: بدء الشحن مرتين للحجز نفسه لا يُنتج دفعتين.
            if existing.status == PaymentStatus.SUCCEEDED:
                raise PaymentAlreadyExistsError("This booking has already been paid.")
            return existing

        payment = Payment(
            booking=booking,
            amount=offer.total_amount,
            method=_method_for(booking),
            status=PaymentStatus.PROCESSING,
            attempt_number=1,
        )
        payment.full_clean()

        try:
            with transaction.atomic():
                payment.save()
        except IntegrityError as exc:
            raise PaymentAlreadyExistsError(
                "This booking already has a payment; it cannot be charged twice."
            ) from exc

    return _attempt_charge(payment, booking)


def _method_for(booking):
    """
    طريقة الدفع المختارة للحجز.

    📌 المرجع المبهم يُخزَّن على الحجز؛ النوع يبقى CARD افتراضيًا حتى
       يُحسم مزوّد يبلّغ النوع فعليًا (§18).
    """
    return PaymentMethod.CARD


def _attempt_charge(payment, booking):
    """
    ينفّذ المحاولة لدى المزوّد ويحدّث الحالة.

    ⚠️ يُستدعى خارج أي معاملة (سجل الدفعة مُثبَّت قبله). استثناء من المزوّد
       يعني "النتيجة مجهولة" — قد يكون الخصم تم — فتبقى الدفعة PROCESSING مع
       السبب مسجَّلًا للمطابقة، ولا تُعلَّم FAILED ولا يُعاد الشحن تلقائيًا.
    """
    try:
        result = get_payment_adapter().charge(
            amount=payment.amount,
            method=payment.method,
            idempotency_key=build_idempotency_key(booking, payment.attempt_number),
            payment_method_reference=booking.payment_method_reference,
            currency=CURRENCY,
            customer_reference=str(booking.customer_id),
        )
    except Exception as exc:  # noqa: BLE001 — النتيجة مجهولة، لا فاشلة
        logger.exception(
            "Payment provider error — outcome unknown, left %s for reconciliation "
            "(payment_id=%s, booking_id=%s)",
            payment.status,
            payment.id,
            booking.id,
        )
        payment.failure_reason = f"provider_error: {type(exc).__name__}"
        payment.save(update_fields=["failure_reason", "updated_at"])
        return payment

    payment.provider_reference = result.provider_reference
    payment.method_summary = result.method_summary
    payment.provider_error_code = result.error_code or ""

    if result.success:
        payment.status = PaymentStatus.SUCCEEDED
        payment.failure_reason = None
        payment.action_payload = None
    elif result.requires_action:
        payment.status = PaymentStatus.REQUIRES_ACTION
        payment.failure_reason = None
        payment.action_payload = result.action_payload
    else:
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = result.failure_reason
        payment.action_payload = None

    payment.save(
        update_fields=[
            "status",
            "provider_reference",
            "failure_reason",
            "provider_error_code",
            "method_summary",
            "action_payload",
            "updated_at",
        ]
    )

    logger.info(
        "Payment %s (payment_id=%s, booking_id=%s, amount=%s, attempt=%s)",
        payment.status,
        payment.id,
        booking.id,
        payment.amount,
        payment.attempt_number,
    )

    # 🔒 النجاح وحده يُسنِد (§14) — بعد الـcommit حتى لا يتعطّل الحسم لو
    #    فشل أي أثر لاحق.
    if payment.status == PaymentStatus.SUCCEEDED:
        transaction.on_commit(lambda: confirm_payment(payment.id), robust=True)
    _emit_payment_outcome(payment)

    return payment


def _emit_payment_outcome(payment):
    """فشل الدفع أو حاجته لتأكيد 3-D Secure يحتاج تدخل العميل فورًا."""
    if payment.status == PaymentStatus.FAILED:
        _emit("payment_failed", payment)
    elif payment.status == PaymentStatus.REQUIRES_ACTION:
        _emit("payment_action_required", payment)


@transaction.atomic
def confirm_payment(payment_id):
    """
    الانتقال الذرّي بعد تأكيد نجاح الدفع (§14).

        Payment    → SUCCEEDED (paid_at)
        Booking    → CONFIRMED + assigned_contractor + computed_price
        Offer      → ACCEPTED
        Job        → ASSIGNED

    🔒 تكرارية بالكامل: إعادة استدعائها (webhook مُعاد الإرسال) لا تُنشئ
       مهمة ثانية ولا تُسنِد مرتين — تخرج فورًا إن كان الحجز مؤكَّدًا.

    📌 مصدر الحقيقة هو تأكيد المزوّد لا استجابة الواجهة: هذه الدالة
       يناديها مسار التأكيد الداخلي أو الـwebhook، لا نداء من العميل.
    """
    payment = (
        Payment.objects.select_for_update()
        .select_related("booking")
        .filter(pk=payment_id)
        .first()
    )
    if payment is None:
        raise PaymentNotFoundError("Payment not found.")

    booking = Booking.objects.select_for_update().get(pk=payment.booking_id)

    # 🔒 حارس التكرارية: الحجز مؤكَّد فعلًا → لا شيء يُعاد.
    if booking.status == BookingStatus.CONFIRMED:
        return payment

    # 🔒 لا يُسند إلا حجز ما زال PENDING. حجز خرج منها (أُلغي قبل الدفع —
    #    والإلغاء يرفض أي دفعة بدأت) لا يُسند أبدًا؛ يُسجَّل للمطابقة
    #    والاسترداد اليدوي.
    if booking.status != BookingStatus.PENDING:
        logger.error(
            "Payment succeeded on a booking that is no longer pending — needs "
            "manual review/refund (payment_id=%s, booking_id=%s, status=%s)",
            payment.id, booking.id, booking.status,
        )
        return payment

    if payment.status != PaymentStatus.SUCCEEDED:
        raise PaymentError("Payment is not successful; assignment cannot proceed.")

    offer = _accepted_offer(booking)
    if offer is None:
        raise OfferNoLongerReservedError(
            "No accepted offer reserves this booking; it cannot be assigned."
        )

    if payment.paid_at is None:
        payment.paid_at = timezone.now()
        payment.save(update_fields=["paid_at", "updated_at"])

    offer.status = DispatchOfferStatus.ACCEPTED
    offer.save(update_fields=["status"])

    booking.status = BookingStatus.CONFIRMED
    booking.assigned_contractor = offer.contractor
    booking.computed_price = offer.total_amount
    booking.dispatch_status = DispatchStatus.ASSIGNED
    booking.pricing_version = offer.pricing_version
    booking.save(
        update_fields=[
            "status",
            "assigned_contractor",
            "computed_price",
            "dispatch_status",
            "pricing_version",
            "updated_at",
        ]
    )

    logger.info(
        "Payment confirmed and contractor assigned (payment_id=%s, booking_id=%s, "
        "contractor_id=%s, amount=%s)",
        payment.id,
        booking.id,
        offer.contractor_id,
        payment.amount,
    )

    # إنشاء المهمة بعد الـcommit: فشلها لا يجوز أن يتراجع عن دفعة ناجحة.
    transaction.on_commit(lambda: _create_job_after_commit(booking), robust=True)

    # إشعار الطرفين: العميل (وُجد عامل) والمقاول (العميل دفع)
    _emit("booking_confirmed", booking)
    _emit("job_confirmed", booking)

    return payment


def _create_job_after_commit(booking):
    """ينشئ مهمة التنفيذ بحالة ASSIGNED — تكرارية بحكم OneToOne."""
    from apps.jobs.services.jobs import JobAlreadyExistsError, create_job_for_booking

    try:
        create_job_for_booking(booking)
    except JobAlreadyExistsError:
        # 🔒 webhook مُعاد الإرسال — المهمة موجودة فعلًا. ليست خطأً.
        logger.info("Job already exists for booking (booking_id=%s)", booking.id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Job creation failed after payment confirmation (booking_id=%s)",
            booking.id,
        )


def retry_payment(user, payment_id, payment_method_reference=None):
    """
    يعيد محاولة دفعة فاشلة بالمبلغ المجمَّد نفسه (§16).

    🔒 المبلغ لا يُعاد حسابه أبدًا: يبقى إجمالي العرض المقبول المجمَّد.
    🔒 مالك الحجز وحده — لا الإدارة ولا المقاول.
    🔒 الانتقال إلى PROCESSING يُثبَّت أولًا (مع القفل)، والمزوّد خارج
       المعاملة — نفس سبب start_charge_for_booking.
    """
    with transaction.atomic():
        payment, booking = _prepare_retry(user, payment_id, payment_method_reference)
    return _attempt_charge(payment, booking)


def _prepare_retry(user, payment_id, payment_method_reference):
    payment = (
        Payment.objects.select_for_update()
        .select_related("booking")
        .filter(pk=payment_id)
        .first()
    )

    if payment is None or payment.booking.customer_id != user.id:
        # 🔒 دفعة الغير كغير الموجودة.
        raise PaymentNotFoundError("Payment not found.")

    if payment.status == PaymentStatus.SUCCEEDED:
        raise PaymentNotRetryableError("This booking has already been paid.")

    if payment.status not in RETRYABLE_PAYMENT_STATUSES:
        raise PaymentNotRetryableError(
            f"Payment is {payment.status} and cannot be retried."
        )

    booking = payment.booking
    offer = _accepted_offer(booking)
    if offer is None or offer.total_amount is None:
        raise OfferNoLongerReservedError(
            "The accepted offer is no longer reserved; this payment cannot be retried."
        )

    if payment_method_reference:
        booking.payment_method_reference = payment_method_reference
        booking.save(update_fields=["payment_method_reference", "updated_at"])

    # 📌 محاولة جديدة = مفتاح تكرار جديد، وإلا أعاد المزوّد نتيجة المحاولة
    #    الفاشلة السابقة ولم تُنفَّذ المحاولة بطريقة الدفع الجديدة (§16).
    payment.attempt_number += 1
    payment.status = PaymentStatus.PROCESSING
    payment.amount = offer.total_amount
    payment.save(update_fields=["attempt_number", "status", "amount", "updated_at"])

    return payment, booking


@transaction.atomic
def confirm_payment_action(user, payment_id):
    """Confirm a provider action after the customer completes 3-D Secure."""
    payment = (
        Payment.objects.select_for_update()
        .select_related("booking")
        .filter(pk=payment_id)
        .first()
    )
    if payment is None or payment.booking.customer_id != user.id:
        raise PaymentNotFoundError("Payment not found.")

    if payment.status != PaymentStatus.REQUIRES_ACTION:
        raise PaymentActionNotAvailableError(
            f"Payment is {payment.status} and does not require customer action."
        )
    if not payment.provider_reference:
        raise PaymentActionNotAvailableError("Payment has no provider action reference.")

    result = get_payment_adapter().confirm(payment.provider_reference)
    payment.provider_error_code = result.error_code or ""
    payment.provider_reference = result.provider_reference or payment.provider_reference
    payment.method_summary = result.method_summary or payment.method_summary

    if result.success:
        payment.status = PaymentStatus.SUCCEEDED
        payment.failure_reason = None
        payment.action_payload = None
    elif result.requires_action:
        payment.action_payload = result.action_payload
    else:
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = result.failure_reason
        payment.action_payload = None

    payment.save(
        update_fields=[
            "status",
            "provider_reference",
            "provider_error_code",
            "method_summary",
            "failure_reason",
            "action_payload",
            "updated_at",
        ]
    )
    if payment.status == PaymentStatus.SUCCEEDED:
        transaction.on_commit(lambda: confirm_payment(payment.id), robust=True)
    _emit_payment_outcome(payment)
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

    is_admin = user.has_admin_access()
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


def _emit(event, *args):
    """إشعار بعد نجاح المعاملة (apps/notifications/hooks.py) — استيراد كسول."""
    from apps.notifications.hooks import emit_on_commit

    emit_on_commit(event, *args)
