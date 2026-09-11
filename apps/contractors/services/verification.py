"""
Contractor Verification Service — Contractors Domain (Change Set §6، Infra §5)

مراجعة يدوية بالكامل. كل الطفرات تمر من هنا — طبقة الـAPI لا تستدعي
`.objects` مباشرة (نفس نمط properties/services/contractors.profile).

⚠️ ما لا يوجد هنا، عمدًا:
   - أي استدعاء لـadapters/business_registry — يبقى تجريديًا دون استخدام.
     التحقق من الـABN شكلي فقط (11 رقمًا) ويعيش في الـModel validator.
   - أي جدولة إعادة تحقق أو مهمة Celery عند انتهاء صلاحية التأمين
     (Infra §5 — "policy إعادة التحقق" بند مفتوح). الانتهاء يُقرأ لحظيًا
     داخل is_contractor_eligible عند كل استدعاء.
   - أي منطق Dispatch: الأهلية تُحسب هنا، ومن يستهلكها شأن مرحلة لاحقة.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import ConfirmedRole

from ..models import BusinessRegistration, InsuranceDocument, VerificationStatus
from .profile import (  # noqa: F401 — نعيد استخدام نفس بوابات الدور
    AdminRoleRequiredError,
    ContractorProfileNotFoundError,
    ContractorProfilePermissionError,
    InvalidContractorRoleError,
    assert_is_admin,
    assert_is_contractor,
    get_own_profile,
)

logger = logging.getLogger(__name__)


class VerificationError(Exception):
    """أصل أخطاء نطاق التحقق."""

    code = "verification_error"


class VerificationNotFoundError(VerificationError):
    code = "verification_not_found"


class MissingRejectionReasonError(VerificationError):
    """
    🔒 الرفض بلا سبب ممنوع.

    السبب ليس تجميلًا: المقاول المرفوض يحتاج معرفة ما يصحّحه قبل إعادة
    التقديم، والمراجعة الإدارية بلا تعليل غير قابلة للمساءلة.
    """

    code = "rejection_reason_required"


class InvalidReviewStatusError(VerificationError):
    """المراجعة تنتهي إلى VERIFIED أو REJECTED فقط."""

    code = "invalid_review_status"


# الحالات التي تُنهي المراجعة. PENDING ليست قرارًا — لا يُعاد إليها سجل.
TERMINAL_REVIEW_STATUSES = {
    VerificationStatus.VERIFIED,
    VerificationStatus.REJECTED,
}


# ------------------------------------------------------------
# تقديم المستندات (المقاول على نفسه)
# ------------------------------------------------------------
@transaction.atomic
def submit_business_registration(user, abn, business_name):
    """
    يقدّم تسجيل نشاط جديدًا — يبدأ PENDING دائمًا.

    ⚠️ التعدد مسموح عمدًا: إعادة التقديم بعد رفض، أو تحديث بيانات. لا
       قيد يمنع وجود عدة سجلات لنفس المقاول.
    """
    profile = get_own_profile(user)

    registration = BusinessRegistration(
        contractor=profile,
        abn=abn,
        business_name=business_name,
        status=VerificationStatus.PENDING,
    )
    registration.full_clean()
    registration.save()

    logger.info(
        "Business registration submitted (id=%s, contractor_id=%s)",
        registration.id,
        profile.id,
    )
    return registration


@transaction.atomic
def submit_insurance_document(user, document_reference, expiry_date):
    """
    يقدّم وثيقة تأمين جديدة — تبدأ PENDING دائمًا.

    ⚠️ document_reference نص وليس ملفًا مرفوعًا (Infra §7).
    """
    profile = get_own_profile(user)

    document = InsuranceDocument(
        contractor=profile,
        document_reference=document_reference,
        expiry_date=expiry_date,
        status=VerificationStatus.PENDING,
    )
    document.full_clean()
    document.save()

    logger.info(
        "Insurance document submitted (id=%s, contractor_id=%s)", document.id, profile.id
    )
    return document


def list_own_business_registrations(user):
    """سجلات المقاول الحالي فقط — الأحدث أولًا."""
    profile = get_own_profile(user)
    return BusinessRegistration.objects.filter(contractor=profile)


def list_own_insurance_documents(user):
    """وثائق المقاول الحالي فقط — الأحدث أولًا."""
    profile = get_own_profile(user)
    return InsuranceDocument.objects.filter(contractor=profile)


# ------------------------------------------------------------
# المراجعة الإدارية
# ------------------------------------------------------------
def list_pending(user):
    """
    كل ما ينتظر المراجعة — تسجيلات نشاط ووثائق تأمين معًا.

    تُعاد كقاموس بمفتاحين بدل قائمة مدمجة: النوعان كيانان مختلفان
    بحقول مختلفة، ودمجهما في قائمة واحدة كان سيفرض شكلًا هجينًا.
    """
    assert_is_admin(user)

    return {
        "business_registrations": BusinessRegistration.objects.filter(
            status=VerificationStatus.PENDING
        ).select_related("contractor"),
        "insurance_documents": InsuranceDocument.objects.filter(
            status=VerificationStatus.PENDING
        ).select_related("contractor"),
    }


def _review(user, instance, status, rejection_reason):
    """
    منطق المراجعة المشترك بين النوعين.

    🔒 يفرض: الدور ADMIN، الحالة نهائية، والسبب موجود عند الرفض.
    """
    assert_is_admin(user)

    if status not in TERMINAL_REVIEW_STATUSES:
        raise InvalidReviewStatusError(
            "Review status must be VERIFIED or REJECTED."
        )

    reason = (rejection_reason or "").strip()

    if status == VerificationStatus.REJECTED and not reason:
        raise MissingRejectionReasonError(
            "A rejection reason is required when rejecting."
        )

    instance.status = status
    # سبب الرفض لا يُحتفظ به عند الاعتماد — لا يبقى تعليل رفض على سجل معتمد
    instance.rejection_reason = reason if status == VerificationStatus.REJECTED else None
    instance.reviewed_by = user
    instance.reviewed_at = timezone.now()

    instance.full_clean()
    instance.save()

    logger.info(
        "Verification reviewed (model=%s, id=%s, status=%s, by=%s)",
        type(instance).__name__,
        instance.id,
        status,
        user.id,
    )
    return instance


@transaction.atomic
def review_business_registration(user, registration_id, status, rejection_reason=None):
    """يعتمد أو يرفض تسجيل نشاط — ADMIN فقط."""
    assert_is_admin(user)

    registration = BusinessRegistration.objects.filter(pk=registration_id).first()
    if registration is None:
        raise VerificationNotFoundError("Business registration not found.")

    return _review(user, registration, status, rejection_reason)


@transaction.atomic
def review_insurance_document(user, document_id, status, rejection_reason=None):
    """يعتمد أو يرفض وثيقة تأمين — ADMIN فقط."""
    assert_is_admin(user)

    document = InsuranceDocument.objects.filter(pk=document_id).first()
    if document is None:
        raise VerificationNotFoundError("Insurance document not found.")

    return _review(user, document, status, rejection_reason)


# ------------------------------------------------------------
# الأهلية — دالة محسوبة، لا حقل مخزَّن
# ------------------------------------------------------------
def latest_business_registration(contractor_profile):
    """أحدث تسجيل نشاط بحسب created_at، أو None."""
    return (
        BusinessRegistration.objects.filter(contractor=contractor_profile)
        .order_by("-created_at")
        .first()
    )


def latest_insurance_document(contractor_profile):
    """أحدث وثيقة تأمين بحسب created_at، أو None."""
    return (
        InsuranceDocument.objects.filter(contractor=contractor_profile)
        .order_by("-created_at")
        .first()
    )


def is_contractor_eligible(contractor_profile) -> bool:
    """
    أهلية تشغيلية محسوبة لحظيًا (Change Set §6).

    True فقط إذا اجتمعت الثلاثة:
      - أحدث BusinessRegistration حالته VERIFIED
      - أحدث InsuranceDocument حالته VERIFIED
      - تاريخ انتهاء تلك الوثيقة >= اليوم

    ⚠️ ليست حقلًا مخزَّنًا عمدًا: لو خُزِّنت، لأصبحت كذبة صامتة في اليوم
       التالي لانتهاء التأمين ما لم تُحدّثها مهمة خلفية — وتلك المهمة بند
       مفتوح صراحةً (Infra §5). الحساب اللحظي يجعل الانتهاء يسري من تلقائه.

    ⚠️ "الأحدث" يُحسم بـcreated_at وحده: سجل مرفوض أحدث يُبطل الأهلية حتى
       لو وُجد سجل معتمد أقدم — وهو السلوك الصحيح، فالأحدث يعكس الوضع الحالي.
    """
    if contractor_profile is None:
        return False

    registration = latest_business_registration(contractor_profile)
    if registration is None or registration.status != VerificationStatus.VERIFIED:
        return False

    insurance = latest_insurance_document(contractor_profile)
    if insurance is None or insurance.status != VerificationStatus.VERIFIED:
        return False

    # الانتهاء يُقارَن باليوم المحلي — الوثيقة المنتهية اليوم ما زالت سارية
    if insurance.expiry_date < timezone.localdate():
        return False

    return True
