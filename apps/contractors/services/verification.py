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

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.roles import ConfirmedRole
from apps.audit.services.audit import record

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


class AlreadyReviewedError(VerificationError):
    """
    السجل رُوجع مسبقًا — القرار نهائي.

    🔒 قلب VERIFIED إلى REJECTED (أو العكس) كان يمحو المراجِع الأول
       بلا أثر. التغيير يكون بتقديم جديد من المقاول ثم مراجعته.
    """

    code = "already_reviewed"


class SelfReviewError(VerificationError):
    """المراجِع هو صاحب المستند."""

    code = "self_review_forbidden"


class ExpiredDocumentError(VerificationError):
    """وثيقة تأمين منتهية — لا تُقدَّم ولا تُعتمد."""

    code = "document_expired"


class SubmissionPendingError(VerificationError):
    """تقديم سابق من النوع نفسه ما زال قيد المراجعة."""

    code = "submission_pending"


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

    ⚠️ التعدد مسموح عمدًا: إعادة التقديم بعد رفض، أو تحديث بيانات —
       لكن تقديمًا واحدًا قيد المراجعة في كل لحظة، حتى لا تُغرق طابور
       المراجعة بنسخ متكررة.
    """
    profile = get_own_profile(user)
    _assert_no_pending(BusinessRegistration, profile)

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
    🔒 وثيقة منتهية لا تُقدَّم: اعتمادها لا يمنح أهلية أصلًا.
    """
    profile = get_own_profile(user)
    _assert_no_pending(InsuranceDocument, profile)
    if expiry_date < timezone.localdate():
        raise ExpiredDocumentError("The insurance document has already expired.")

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


def _assert_no_pending(model, profile):
    if model.objects.filter(contractor=profile, status=VerificationStatus.PENDING).exists():
        raise SubmissionPendingError(
            "A previous submission is still under review. Wait for its decision."
        )


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


def list_verifications_for_profile(user, profile_id):
    """
    السجل الكامل لتحقق مقاول واحد — كل التقديمات بكل حالاتها، الأحدث أولًا.

    Raises ContractorProfileNotFoundError إن لم يوجد الملف.
    """
    from ..models import ContractorProfile

    assert_is_admin(user)

    profile = ContractorProfile.objects.filter(pk=profile_id).first()
    if profile is None:
        raise ContractorProfileNotFoundError("Contractor profile not found.")

    return {
        "profile": profile,
        "business_registrations": BusinessRegistration.objects.filter(
            contractor=profile
        ).order_by("-created_at"),
        "insurance_documents": InsuranceDocument.objects.filter(
            contractor=profile
        ).order_by("-created_at"),
    }


# اسم الفعل في سجل التدقيق لكل نوع
_AUDIT_ACTIONS = {
    "BusinessRegistration": "business_registration.review",
    "InsuranceDocument": "insurance_document.review",
}


def _review(user, instance, status, rejection_reason, request=None):
    """
    منطق المراجعة المشترك بين النوعين.

    🔒 يفرض: الدور ADMIN، الحالة نهائية، والسبب موجود عند الرفض، والسجل
       ما زال PENDING (المستدعي قفله)، والمراجِع ليس صاحب المستند، ولا
       اعتماد لوثيقة تأمين منتهية.
    """
    assert_is_admin(user)

    if status not in TERMINAL_REVIEW_STATUSES:
        raise InvalidReviewStatusError(
            "Review status must be VERIFIED or REJECTED."
        )

    if instance.status != VerificationStatus.PENDING:
        raise AlreadyReviewedError(
            f"This submission was already {instance.status.lower()}; decisions are final."
        )

    if instance.contractor.user_id == user.id:
        raise SelfReviewError("You cannot review your own submission.")

    if (
        status == VerificationStatus.VERIFIED
        and getattr(instance, "expiry_date", None) is not None
        and instance.expiry_date < timezone.localdate()
    ):
        raise ExpiredDocumentError("An expired insurance document cannot be approved.")

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

    # 📌 داخل معاملة المستدعي — قرار تراجع لا يترك أثرًا كاذبًا
    record(
        user,
        _AUDIT_ACTIONS[type(instance).__name__],
        target=instance,
        details={
            "from": VerificationStatus.PENDING,
            "to": status,
            "contractor_id": str(instance.contractor_id),
            "rejection_reason": instance.rejection_reason,
        },
        request=request,
    )

    logger.info(
        "Verification reviewed (model=%s, id=%s, status=%s, by=%s)",
        type(instance).__name__,
        instance.id,
        status,
        user.id,
    )
    return instance


@transaction.atomic
def review_business_registration(
    user, registration_id, status, rejection_reason=None, request=None
):
    """يعتمد أو يرفض تسجيل نشاط — ADMIN فقط."""
    assert_is_admin(user)

    # 🔒 قفل: مراجعان متزامنان لا يمرّان معًا على PENDING
    registration = (
        BusinessRegistration.objects.select_for_update()
        .select_related("contractor")
        .filter(pk=registration_id)
        .first()
    )
    if registration is None:
        raise VerificationNotFoundError("Business registration not found.")

    return _review(user, registration, status, rejection_reason, request=request)


@transaction.atomic
def review_insurance_document(user, document_id, status, rejection_reason=None, request=None):
    """يعتمد أو يرفض وثيقة تأمين — ADMIN فقط."""
    assert_is_admin(user)

    document = (
        InsuranceDocument.objects.select_for_update()
        .select_related("contractor")
        .filter(pk=document_id)
        .first()
    )
    if document is None:
        raise VerificationNotFoundError("Insurance document not found.")

    return _review(user, document, status, rejection_reason, request=request)


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


def _latest_reviewed(model, contractor_profile):
    """أحدث سجل صدر فيه قرار — PENDING لا يُحسب."""
    return (
        model.objects.filter(contractor=contractor_profile)
        .exclude(status=VerificationStatus.PENDING)
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

    ⚠️ "الأحدث" = أحدث سجل **صدر فيه قرار** (بـcreated_at): سجل مرفوض أحدث
       يُبطل الأهلية حتى لو وُجد معتمد أقدم — فالأحدث يعكس الوضع الحالي.
       أما التقديم المعلّق (تجديد تأمين قبل انتهائه مثلًا) فلا يُسقط أهلية
       قائمة: المقاول الذي جدّد مبكرًا لا يُعاقَب بالتوقف حتى تُراجَع وثيقته.
    """
    if contractor_profile is None:
        return False

    registration = _latest_reviewed(BusinessRegistration, contractor_profile)
    if registration is None or registration.status != VerificationStatus.VERIFIED:
        return False

    insurance = _latest_reviewed(InsuranceDocument, contractor_profile)
    if insurance is None or insurance.status != VerificationStatus.VERIFIED:
        return False

    # الانتهاء يُقارَن باليوم المحلي — الوثيقة المنتهية اليوم ما زالت سارية
    if insurance.expiry_date < timezone.localdate():
        return False

    return True


# ============================================================
# حالة العامل للعرض — مشتقّة، غير مخزَّنة
# ============================================================
class ContractorStatus(models.TextChoices):
    """
    حالة انضمام الحساب كعامل — للفرونت.

    📌 مشتقّة بالكامل من الملف ووثيقتيه: لا حقل مخزَّن يمثّلها، تمامًا
       كـis_contractor_eligible. تخزينها كان سيصير كذبة صامتة يوم تنتهي
       صلاحية التأمين.

    NONE       : لم يقدّم كعامل — لا ملف أصلًا.
    PENDING    : لديه ملف، ووثائقه لم تكتمل مراجعتها بعد.
    ACTION_REQUIRED : رُفضت إحدى وثيقتيه، أو انتهت صلاحية تأمينه —
                 يحتاج إعادة تقديم.
    APPROVED   : مؤهَّل فعلًا؛ يستطيع استقبال العروض.
    SUSPENDED  : كان عاملًا، لكن حسابه لم يعد نشطًا (status != ACTIVE).
    """

    NONE = "NONE", "Not applied"
    PENDING = "PENDING", "Under review"
    ACTION_REQUIRED = "ACTION_REQUIRED", "Action required"
    APPROVED = "APPROVED", "Approved"
    SUSPENDED = "SUSPENDED", "Suspended"


def get_contractor_status(user) -> str:
    """
    يعيد حالة العامل لهذا الحساب — مشتقّة لحظيًا.

    ⚠️ ترتيب الفحص مقصود: الإيقاف أولًا (يتجاوز كل ما عداه)، ثم غياب
       الملف، ثم الأهلية، ثم التمييز بين "قيد المراجعة" و"يحتاج إجراء".
    """
    from apps.accounts.models import UserStatus

    profile = getattr(user, "contractor_profile", None)

    if profile is None:
        # لا ملف: لم يقدّم بعد. والإيقاف بلا ملف لا معنى له.
        return ContractorStatus.NONE

    # 🔒 حساب موقوف/معطّل لا يعمل مهما كانت وثائقه
    if not user.is_active or user.status != UserStatus.ACTIVE:
        return ContractorStatus.SUSPENDED

    if is_contractor_eligible(profile):
        return ContractorStatus.APPROVED

    registration = latest_business_registration(profile)
    insurance = latest_insurance_document(profile)

    # رفض صريح لأي وثيقة، أو تأمين منتهٍ → المستخدم يحتاج إجراءً
    if (registration is not None
            and registration.status == VerificationStatus.REJECTED):
        return ContractorStatus.ACTION_REQUIRED
    if insurance is not None:
        if insurance.status == VerificationStatus.REJECTED:
            return ContractorStatus.ACTION_REQUIRED
        if (insurance.status == VerificationStatus.VERIFIED
                and insurance.is_expired):
            return ContractorStatus.ACTION_REQUIRED

    # ملف بلا وثائق بعد، أو وثائق ما زالت PENDING
    return ContractorStatus.PENDING
