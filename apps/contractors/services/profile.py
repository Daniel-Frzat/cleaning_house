"""
Contractor Profile Service — Contractors Domain

كل وصول إلى ملفات المقاولين يمر من هنا. طبقة الـAPI لا تستدعي `.objects`
مباشرة — نفس النمط المعتمد في apps/properties و apps/services.

طبقتان منفصلتان عمدًا (نفس روح apps/properties):
  1) بوابة الدور: CONTRACTOR للمسارات الذاتية، ADMIN لمسارات الإدارة.
  2) فحص الملكية على مستوى الكائن: الملف يخص هذا المستخدم تحديدًا.
لا يُستغنى عن أي منهما: الدور يحمي نقطة النهاية، والملكية تحمي الكائن.

⚠️ لا يوجد هنا أي منطق Dispatch/matching/مسافة، ولا استدعاء لأي adapter
   (gps_distance / address_validation) — كلاهما تجريدي حسب §34/§4.
   هذه الطبقة تخزّن البيانات التي سيستهلكها Dispatch لاحقًا، لا أكثر.
"""

import logging

from django.db import transaction

from apps.accounts.roles import ConfirmedRole

from ..models import AvailabilityStatus, ContractorProfile


def _clean_profile(profile):
    """
    full_clean + اتساق الرمز البريدي/الولاية (نفس قاعدة PropertyAddress).

    ⚠️ هنا لا في models.py: نموذج المقاول لا يستورد من apps.properties عمدًا
       (راجع docstring النموذج). الحقلان اختياريان (ملف قيد الإكمال)،
       فالفحص حين يوجد الاثنان.
    """
    from django.core.exceptions import ValidationError

    from apps.properties.services.postcodes import postcode_state_error

    profile.full_clean()
    if profile.postcode and profile.state:
        error = postcode_state_error(profile.postcode, profile.state)
        if error:
            raise ValidationError({"postcode": error})


logger = logging.getLogger(__name__)


class ContractorProfileError(Exception):
    """أصل أخطاء نطاق ملفات المقاولين."""

    code = "contractor_profile_error"


class ContractorProfilePermissionError(ContractorProfileError):
    """المستخدم لا يملك هذا الملف."""

    code = "contractor_profile_forbidden"


class ContractorProfileNotFoundError(ContractorProfileError):
    code = "contractor_profile_not_found"


class InvalidContractorRoleError(ContractorProfileError):
    """الدور غير مسموح له بامتلاك ملف مقاول."""

    code = "invalid_contractor_role"


class AdminRoleRequiredError(ContractorProfileError):
    """مسارات الإدارة تتطلب دور ADMIN."""

    code = "admin_role_required"


class ProfileAlreadyExistsError(ContractorProfileError):
    """المستخدم له ملف واحد فقط (OneToOne)."""

    code = "contractor_profile_exists"


# الحقول المسموح تعديلها عبر التعديل العام — قائمة بيضاء صريحة.
# ⚠️ availability_status ليست هنا عمدًا: لها مسارها المخصّص وحده
#    (update_availability)، فهي تُستدعى بكثرة ويجب أن تبقى خفيفة ومنفصلة.
UPDATABLE_FIELDS = {
    "business_name",
    "street_address",
    "suburb",
    "state",
    "postcode",
    "latitude",
    "longitude",
}


# ------------------------------------------------------------
# فحوص الصلاحية
# ------------------------------------------------------------
def assert_is_contractor(user):
    """
    امتلاك ملف مقاول صلاحية CONTRACTOR حصرًا.

    ⚠️ هذا هو الإنفاذ الفعلي للقاعدة "لا ملف إلا لمقاول". قاعدة البيانات
       لا تستطيع فرضها لأن الدور حقل على جدول آخر.
    """
    if user is None or not user.is_authenticated:
        raise ContractorProfilePermissionError("Authentication required.")
    if not user.has_contractor_access():
        raise InvalidContractorRoleError("Only contractors can have a contractor profile.")


def assert_is_admin(user):
    """مسارات الإدارة — ADMIN حصرًا."""
    if user is None or not user.is_authenticated:
        raise ContractorProfilePermissionError("Authentication required.")
    if not user.has_admin_access():
        logger.warning(
            "Contractor admin access denied (user_id=%s, role=%s)", user.id, user.role
        )
        raise AdminRoleRequiredError("Only admins can access contractor records.")


def assert_owns(user, profile):
    """
    فحص الملكية على مستوى الكائن — نقطة الإنفاذ الوحيدة.

    تُستدعى في كل قراءة/تعديل، حتى لو كان الاستعلام مُرشَّحًا أصلًا
    (نفس نمط properties/services/properties.py:assert_owns).
    """
    if user is None or not user.is_authenticated:
        raise ContractorProfilePermissionError("Authentication required.")
    if profile.user_id != user.id:
        # لا نكشف وجود ملف لغير مالكه
        logger.warning(
            "Contractor profile ownership check failed (user_id=%s, profile_id=%s)",
            user.id,
            profile.id,
        )
        raise ContractorProfilePermissionError("You do not have access to this profile.")


# ------------------------------------------------------------
# العمليات الذاتية (المقاول على ملفه)
# ------------------------------------------------------------
@transaction.atomic
def create_profile(user, **fields):
    """
    ينشئ ملف المقاول للمستخدم المُمرَّر (وله فقط) — "Work with Cleano".

    📌 هذه نقطة الانضمام: زبون قائم ينشئ ملف عامل من حسابه نفسه، فيُمنح
       صلاحية العامل (is_contractor) ضمن المعاملة ذاتها. لا حساب جديد،
       ولا فقدان لحجوزاته ولا عقاراته — نفس user_id ونفس الرقم.

    🔒 منح الصلاحية ليس اعتمادًا: الملف يبدأ UNAVAILABLE بلا وثائق
       معتمدة، فلا يصله أي عرض حتى تُراجَع وثيقتاه إداريًا.

    ⚠️ ADMIN مستثنى: الإدارة لا تعمل كمقاول (الدور حصري بقرار معماري).

    ⚠️ availability_status لا تُقبل هنا: الملف يبدأ UNAVAILABLE دائمًا،
       والإتاحة فعل صريح لاحق عبر update_availability.
    """
    if user is None or not user.is_authenticated:
        raise ContractorProfilePermissionError("Authentication required.")

    if user.has_admin_access():
        raise InvalidContractorRoleError("Admins cannot work as contractors.")

    if ContractorProfile.objects.filter(user=user).exists():
        raise ProfileAlreadyExistsError("This user already has a contractor profile.")

    # لا يُسمح بتمرير الجاهزية عند الإنشاء مهما أُرسل
    fields.pop("availability_status", None)
    fields.pop("country", None)

    unknown = set(fields) - UPDATABLE_FIELDS
    if unknown:
        raise ContractorProfileError(
            f"Field(s) {sorted(unknown)} cannot be set here."
        )

    profile = ContractorProfile(user=user, **fields)
    _clean_profile(profile)
    profile.save()

    # 📌 منح صلاحية العامل مع إنشاء الملف — في المعاملة نفسها، فلا يبقى
    #    ملف بلا صلاحية ولا صلاحية بلا ملف.
    # ⚠️ role لا يُمس: الحساب يبقى CUSTOMER ويحتفظ بكل وصوله كزبون.
    if not user.is_contractor:
        user.is_contractor = True
        user.save(update_fields=["is_contractor", "updated_at"])

    logger.info(
        "Contractor profile created (profile_id=%s, user_id=%s, role=%s)",
        profile.id,
        user.id,
        user.role,
    )
    return profile


def get_own_profile(user):
    """يعيد ملف المستخدم الحالي، أو يرفع NotFound إن لم يُنشئه بعد."""
    assert_is_contractor(user)

    profile = ContractorProfile.objects.filter(user=user).select_related("user").first()
    if profile is None:
        raise ContractorProfileNotFoundError("Contractor profile not found.")

    # فحص الملكية صراحةً حتى لا يعتمد الأمان على ترشيح الاستعلام وحده
    assert_owns(user, profile)
    return profile


@transaction.atomic
def update_profile(user, **fields):
    """
    يعدّل ملف المستخدم الحالي.

    ⚠️ الجاهزية غير قابلة للتعديل من هنا — لها مسارها المخصّص.
    """
    profile = get_own_profile(user)

    fields.pop("country", None)

    if "availability_status" in fields:
        raise ContractorProfileError(
            "availability_status is updated via the dedicated availability endpoint."
        )

    unknown = set(fields) - UPDATABLE_FIELDS
    if unknown:
        raise ContractorProfileError(
            f"Field(s) {sorted(unknown)} cannot be updated here."
        )

    for key, value in fields.items():
        setattr(profile, key, value)

    _clean_profile(profile)
    profile.save()

    logger.info(
        "Contractor profile updated (profile_id=%s, fields=%s)",
        profile.id,
        sorted(fields),
    )
    return profile


@transaction.atomic
def update_availability(user, availability_status):
    """
    يبدّل جاهزية المقاول — مسار مخصّص وخفيف.

    يُستدعى بكثرة (المقاول يبدّل جاهزيته عدة مرات يوميًا)، لذلك يكتب
    حقلًا واحدًا فقط عبر update_fields بدل حفظ الصف كله.
    """
    profile = get_own_profile(user)

    if availability_status not in AvailabilityStatus.values:
        raise ContractorProfileError(
            f"Invalid availability status: {availability_status}."
        )

    profile.availability_status = availability_status
    profile.save(update_fields=["availability_status", "updated_at"])

    logger.info(
        "Contractor availability changed (profile_id=%s, status=%s)",
        profile.id,
        availability_status,
    )
    return profile


# ------------------------------------------------------------
# عمليات الإدارة
# ------------------------------------------------------------
def list_profiles(user):
    """كل ملفات المقاولين — ADMIN فقط."""
    assert_is_admin(user)
    return ContractorProfile.objects.select_related("user").all()


def get_profile_by_id(user, profile_id):
    """يعيد أي ملف بمعرّفه — ADMIN فقط، بلا فحص ملكية."""
    assert_is_admin(user)

    profile = (
        ContractorProfile.objects.filter(pk=profile_id).select_related("user").first()
    )
    if profile is None:
        raise ContractorProfileNotFoundError("Contractor profile not found.")

    return profile
