"""
Back-office — دفعات المقاولين (ADMIN) ومطابقتها (Superuser).

📌 المطابقة لا تستدعي المزوّد ولا تحوّل مالًا: تسجّل نتيجة تحقّق منها
   الـSuperuser لدفعة بقيت PENDING لأن المزوّد رمى استثناءً
   (failure_reason يبدأ بـprovider_error — النتيجة مجهولة).

⚠️ لا إعادة محاولة ولا إطلاق يدوي: سياسة فشل الدفع للمقاول قرار مفتوح،
   والتكرارية الصارمة (Payout واحد لكل حجز) تبقى كما هي.
"""

import logging

from django.db import transaction
from django.db.models import Count, Q

from apps.audit.services.audit import record
from apps.audit.services.backoffice import (
    PROVIDER_ERROR_PREFIX,
    assert_admin,
    assert_superuser,
    filter_date_range,
    is_unknown_outcome,
)

from ..models import Payout, PayoutStatus

logger = logging.getLogger(__name__)


class PayoutAdminError(Exception):
    code = "payout_admin_error"


class PayoutNotFoundError(PayoutAdminError):
    code = "payout_not_found"


class NotReconcilableError(PayoutAdminError):
    code = "not_reconcilable"


class ProviderReferenceRequiredError(PayoutAdminError):
    code = "provider_reference_required"


RECONCILE_OUTCOMES = (PayoutStatus.SUCCEEDED, PayoutStatus.FAILED)


def _needs_reconciliation_q():
    return Q(status=PayoutStatus.PENDING, failure_reason__startswith=PROVIDER_ERROR_PREFIX)


def list_payouts(
    actor,
    status=None,
    contractor_id=None,
    needs_reconciliation=None,
    created_from=None,
    created_to=None,
):
    """
    📌 contractor_id معرّف ملف المقاول (ContractorProfile.id) — موحَّد مع
       بقية القوائم الإدارية، رغم أن Payout يشير إلى حساب المستخدم.
    """
    assert_admin(actor)

    qs = Payout.objects.select_related("booking", "contractor").order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    if contractor_id is not None:
        qs = qs.filter(contractor__contractor_profile__id=contractor_id)
    if needs_reconciliation is True:
        qs = qs.filter(_needs_reconciliation_q())
    elif needs_reconciliation is False:
        qs = qs.exclude(_needs_reconciliation_q())
    return filter_date_range(qs, "created_at", created_from, created_to)


def get_payout(actor, payout_id):
    assert_admin(actor)

    payout = (
        Payout.objects.select_related("booking", "contractor").filter(pk=payout_id).first()
    )
    if payout is None:
        raise PayoutNotFoundError("Payout not found.")
    return payout


def reconcile_payout(actor, payout_id, outcome, note, provider_reference=None, request=None):
    """
    يسجّل نتيجة دفعة مقاول مجهولة النتيجة — Superuser فقط.

    SUCCEEDED: status + provider_reference.
    FAILED:    status=FAILED و failure_reason = الملاحظة. لا إعادة محاولة.

    ⚠️ لا يمسّ Job ولا Booking — نفس قاعدة release_payout_for_booking.
    """
    assert_superuser(actor)

    if outcome not in RECONCILE_OUTCOMES:
        raise NotReconcilableError("Outcome must be SUCCEEDED or FAILED.")
    if outcome == PayoutStatus.SUCCEEDED and not (provider_reference or "").strip():
        raise ProviderReferenceRequiredError(
            "provider_reference is required to record a successful payout."
        )

    with transaction.atomic():
        payout = (
            Payout.objects.select_for_update()
            .select_related("booking", "contractor")
            .filter(pk=payout_id)
            .first()
        )
        if payout is None:
            raise PayoutNotFoundError("Payout not found.")

        if payout.status != PayoutStatus.PENDING or not is_unknown_outcome(
            payout.failure_reason
        ):
            raise NotReconcilableError(
                f"Payout is {payout.status} and is not awaiting reconciliation."
            )

        previous_reason = payout.failure_reason
        if outcome == PayoutStatus.SUCCEEDED:
            payout.status = PayoutStatus.SUCCEEDED
            payout.provider_reference = provider_reference
            payout.failure_reason = None
        else:
            payout.status = PayoutStatus.FAILED
            payout.failure_reason = note
        payout.save(
            update_fields=["status", "provider_reference", "failure_reason", "updated_at"]
        )
        if payout.status == PayoutStatus.SUCCEEDED:
            from apps.notifications.hooks import emit_on_commit

            emit_on_commit("payout_sent", payout)

        details = {
            "outcome": outcome,
            "note": note,
            "previous_failure_reason": previous_reason,
            "booking_id": str(payout.booking_id),
            "amount": str(payout.amount),
        }
        if outcome == PayoutStatus.SUCCEEDED:
            details["provider_reference"] = provider_reference
        record(actor, "payout.reconcile", target=payout, details=details, request=request)

    logger.info("Payout reconciled (payout_id=%s, outcome=%s, by=%s)", payout.id, outcome, actor.id)
    return payout


def summary_counts():
    return Payout.objects.aggregate(
        pending=Count("id", filter=Q(status=PayoutStatus.PENDING)),
        failed=Count("id", filter=Q(status=PayoutStatus.FAILED)),
        needs_reconciliation=Count("id", filter=_needs_reconciliation_q()),
    )
