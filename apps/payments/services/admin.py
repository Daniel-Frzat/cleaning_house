"""
Back-office — دفعات العملاء (ADMIN) ومطابقتها (Superuser).

📌 المطابقة (reconcile) لا تستدعي المزوّد ولا تشحن: تسجّل نتيجة تحقّق منها
   الـSuperuser لدى المزوّد يدويًا لدفعة بقيت PROCESSING لأن المزوّد رمى
   استثناءً (failure_reason يبدأ بـprovider_error — النتيجة مجهولة).

⚠️ ما لا يوجد هنا عمدًا:
   - إعادة محاولة تلقائية أو إدارية: سياسة الإعادة قرار مفتوح. العميل
     وحده يعيد المحاولة من مساره بعد FAILED.

📌 الاسترداد: الأدمن يقرر كاملًا أو جزئيًا بعد تنظيف غير مُرضٍ (قرار PO —
   2026-09-27)، عبر المزوّد، مع إشعار للعميل وتدقيق.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.audit.services.audit import record
from apps.audit.services.backoffice import (
    PROVIDER_ERROR_PREFIX,
    assert_admin,
    assert_superuser,
    filter_date_range,
    is_unknown_outcome,
)

from ..models import Payment, PaymentStatus
from .payments import PaymentError, confirm_payment

logger = logging.getLogger(__name__)


class PaymentAdminError(Exception):
    code = "payment_admin_error"


class PaymentNotFoundError(PaymentAdminError):
    code = "payment_not_found"


class NotReconcilableError(PaymentAdminError):
    """الدفعة ليست PROCESSING بنتيجة مجهولة — لا شيء يُطابَق."""

    code = "not_reconcilable"


class NotRefundableError(PaymentAdminError):
    """Only a succeeded payment with an unrefunded balance can be refunded."""

    code = "not_refundable"


class InvalidRefundAmountError(PaymentAdminError):
    """The refund amount must be positive and at most the unrefunded balance."""

    code = "invalid_refund_amount"


class RefundFailedError(PaymentAdminError):
    """The payment provider refused or could not process the refund."""

    code = "refund_failed"


class ProviderReferenceRequiredError(PaymentAdminError):
    """نتيجة SUCCEEDED بلا مرجع المزوّد — لا دليل يُستند إليه."""

    code = "provider_reference_required"


RECONCILE_OUTCOMES = (PaymentStatus.SUCCEEDED, PaymentStatus.FAILED)


def _needs_reconciliation_q():
    return Q(status=PaymentStatus.PROCESSING, failure_reason__startswith=PROVIDER_ERROR_PREFIX)


def list_payments(
    actor,
    status=None,
    booking_id=None,
    needs_reconciliation=None,
    created_from=None,
    created_to=None,
):
    assert_admin(actor)

    qs = Payment.objects.select_related("booking").order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    if booking_id is not None:
        qs = qs.filter(booking_id=booking_id)
    if needs_reconciliation is True:
        qs = qs.filter(_needs_reconciliation_q())
    elif needs_reconciliation is False:
        qs = qs.exclude(_needs_reconciliation_q())
    return filter_date_range(qs, "created_at", created_from, created_to)


def get_payment(actor, payment_id):
    assert_admin(actor)

    payment = Payment.objects.select_related("booking").filter(pk=payment_id).first()
    if payment is None:
        raise PaymentNotFoundError("Payment not found.")
    return payment


def reconcile_payment(actor, payment_id, outcome, note, provider_reference=None, request=None):
    """
    يسجّل نتيجة دفعة مجهولة النتيجة — Superuser فقط.

    SUCCEEDED: status + provider_reference + paid_at، ثم confirm_payment
               (إسناد المقاول، CONFIRMED، إنشاء المهمة) كما لو وصل تأكيد
               المزوّد.
    FAILED:    status=FAILED و failure_reason = الملاحظة. العميل يستطيع
               إعادة المحاولة من مساره — لا إعادة تلقائية هنا.

    ⚠️ إن لم يعد العرض محجوزًا (confirm_payment يرفض)، تبقى الدفعة SUCCEEDED
       — المال تحرّك فعلًا — ولا يُسنَد أحد. ما بعد ذلك (استرداد؟ إسناد
       يدوي؟) قرار مفتوح (#12/#16)، والرد يكشف booking_confirmed=False.

    Returns: (payment, booking_confirmed)
    """
    assert_superuser(actor)

    if outcome not in RECONCILE_OUTCOMES:
        raise NotReconcilableError("Outcome must be SUCCEEDED or FAILED.")
    if outcome == PaymentStatus.SUCCEEDED and not (provider_reference or "").strip():
        raise ProviderReferenceRequiredError(
            "provider_reference is required to record a successful payment."
        )

    with transaction.atomic():
        payment = (
            Payment.objects.select_for_update()
            .select_related("booking")
            .filter(pk=payment_id)
            .first()
        )
        if payment is None:
            raise PaymentNotFoundError("Payment not found.")

        if payment.status != PaymentStatus.PROCESSING or not is_unknown_outcome(
            payment.failure_reason
        ):
            raise NotReconcilableError(
                f"Payment is {payment.status} and is not awaiting reconciliation."
            )

        previous_reason = payment.failure_reason
        booking_confirmed = False
        assignment_error = None

        if outcome == PaymentStatus.SUCCEEDED:
            payment.status = PaymentStatus.SUCCEEDED
            payment.provider_reference = provider_reference
            payment.failure_reason = None
            payment.action_payload = None
            payment.paid_at = payment.paid_at or timezone.now()
            payment.save(
                update_fields=[
                    "status",
                    "provider_reference",
                    "failure_reason",
                    "action_payload",
                    "paid_at",
                    "updated_at",
                ]
            )
            try:
                # savepoint: فشل الإسناد لا يمحو حقيقة أن المال تحرّك
                with transaction.atomic():
                    confirm_payment(payment.id)
                booking_confirmed = True
            except PaymentError as exc:
                assignment_error = getattr(exc, "code", "payment_error")
                logger.warning(
                    "Reconciled payment could not be confirmed (payment_id=%s, code=%s)",
                    payment.id,
                    assignment_error,
                )
        else:
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = note
            payment.action_payload = None
            payment.save(
                update_fields=["status", "failure_reason", "action_payload", "updated_at"]
            )
            # العميل يستطيع إعادة المحاولة بطريقة دفع أخرى — يجب أن يعلم
            from apps.notifications.hooks import emit_on_commit

            emit_on_commit("payment_failed", payment)

        details = {
            "outcome": outcome,
            "note": note,
            "previous_failure_reason": previous_reason,
            "booking_id": str(payment.booking_id),
            "amount": str(payment.amount),
        }
        if outcome == PaymentStatus.SUCCEEDED:
            details["provider_reference"] = provider_reference
            details["booking_confirmed"] = booking_confirmed
            if assignment_error:
                details["assignment_error"] = assignment_error

        record(actor, "payment.reconcile", target=payment, details=details, request=request)

    payment.refresh_from_db()
    return payment, booking_confirmed


@transaction.atomic
def refund_payment(actor, payment_id, reason, amount=None, request=None):
    """
    يسترد مبلغًا لعميل بقرار الأدمن. amount=None = كل المتبقي.

    🔒 قفل الصف قبل الحساب: استردادان متزامنان لا يتجاوزان المدفوع.
    📌 كامل → REFUNDED؛ جزئي → يبقى SUCCEEDED مع refunded_amount متراكم.
    ⚠️ دفعة المقاول لا تُمس: الاسترداد من المنصّة للعميل، واسترجاع شيء من
       المقاول قرار غير محسوم.
    """
    from apps.notifications.hooks import emit_on_commit

    from ..adapters import get_payment_adapter

    assert_admin(actor)
    payment = Payment.objects.select_for_update().select_related("booking").filter(pk=payment_id).first()
    if payment is None:
        raise PaymentNotFoundError("Payment not found.")
    if payment.status != PaymentStatus.SUCCEEDED:
        raise NotRefundableError(f"Payment is {payment.status}; only a succeeded payment can be refunded.")

    remaining = payment.amount - payment.refunded_amount
    amount = remaining if amount is None else Decimal(amount)
    if amount <= 0 or amount > remaining:
        raise InvalidRefundAmountError(f"Refund must be more than 0 and at most {remaining}.")

    sequence = int(payment.refunded_amount > 0) + 1
    result = get_payment_adapter().refund(
        payment.provider_reference or "",
        amount,
        idempotency_key=f"refund:{payment.pk}:{payment.refunded_amount}:{amount}",
        currency=payment.booking.currency,
    )
    if not result.success:
        logger.warning("Refund refused by provider (payment_id=%s, reason=%s)", payment.pk, result.failure_reason)
        raise RefundFailedError(result.failure_reason or "The payment provider could not process the refund.")

    before = payment.status
    payment.refunded_amount += amount
    payment.refunded_at = timezone.now()
    payment.refund_reason = reason
    if payment.refunded_amount >= payment.amount:
        payment.status = PaymentStatus.REFUNDED
    payment.save(update_fields=["refunded_amount", "refunded_at", "refund_reason", "status", "updated_at"])

    record(
        actor, "payment.refund", target=payment,
        details={
            "amount": str(amount),
            "refunded_total": str(payment.refunded_amount),
            "status": {"from": before, "to": payment.status},
            "provider_reference": result.provider_reference,
            "reason": reason,
            "sequence": sequence,
        },
        request=request,
    )
    emit_on_commit("payment_refunded", payment, amount)
    logger.info("Payment refunded (payment_id=%s, amount=%s, by=%s)", payment.pk, amount, actor.pk)
    return payment


def summary_counts(now=None):
    """عدّادات لوحة المؤشرات للدفعات والإيراد الإجمالي."""
    now = now or timezone.now()
    local_now = timezone.localtime(now)
    start_of_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

    def _since(moment):
        # paid_at مصدر الحقيقة؛ الصفوف القديمة بلا paid_at تُؤرَّخ بإنشائها
        return Q(paid_at__gte=moment) | Q(paid_at__isnull=True, created_at__gte=moment)

    succeeded = Payment.objects.filter(status=PaymentStatus.SUCCEEDED)
    cents = Decimal("0.01")

    def _revenue_since(moment):
        # 📌 quantize: SQLite يعيد مجموع DECIMAL بدقة عائمة؛ القيمة المالية
        #    تُعاد بخانتين دائمًا (Postgres دقيق أصلًا)
        total = succeeded.filter(_since(moment)).aggregate(s=Sum("amount"))["s"]
        return Decimal(total or 0).quantize(cents)

    revenue = {
        "today": _revenue_since(start_of_today),
        "last_7_days": _revenue_since(now - timedelta(days=7)),
        "last_30_days": _revenue_since(now - timedelta(days=30)),
    }

    counts = Payment.objects.aggregate(
        needs_reconciliation=Count("id", filter=_needs_reconciliation_q()),
        failed_last_30_days=Count(
            "id",
            filter=Q(status=PaymentStatus.FAILED, updated_at__gte=now - timedelta(days=30)),
        ),
    )
    return {
        "needs_reconciliation": counts["needs_reconciliation"],
        "failed_last_30_days": counts["failed_last_30_days"],
        "gross_revenue": revenue,
    }
