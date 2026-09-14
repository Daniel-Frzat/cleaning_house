"""
Identity Resolution — Identity Domain (Phase 1)

حل هوية المستخدم بعد نجاح التحقق (OTP)، بنفس قاعدة Social Login:
    المستخدم الجديد يُنشأ بدور CUSTOMER فقط.

⚠️ قرار محسوم: أدوار CONTRACTOR/ADMIN لا تُمنح عبر التسجيل الذاتي —
   تتطلب مسارات تحقق منفصلة لم تُبنَ بعد.
"""

import logging

from django.db import IntegrityError, transaction

from ..models import User, UserStatus
from ..roles import ConfirmedRole

logger = logging.getLogger(__name__)


class InactiveUserError(Exception):
    """الحساب موجود لكنه معطّل/موقوف."""

    code = "inactive_user"


@transaction.atomic
def get_or_create_user_by_phone(phone):
    """
    يعيد (user, created) للرقم المُتحقَّق منه عبر OTP.

    يُنشئ الحساب عند أول تحقق ناجح فقط.
    """
    phone = phone.strip()
    user = User.objects.filter(phone=phone).first()

    if user is None:
        user = User.objects.create_user(phone=phone, role=ConfirmedRole.CUSTOMER)
        logger.info("User created after first OTP verification (user_id=%s)", user.id)
        return user, True

    assert_user_can_login(user)
    return user, False


def assert_user_can_login(user):
    """يمنع منح دخول لحساب معطّل أو موقوف."""
    if not user.is_active or user.status != UserStatus.ACTIVE:
        raise InactiveUserError("This account is not active.")


# ============================================================
# الملف الشخصي — تعديل ذاتي
# ============================================================
class ProfileUpdateError(Exception):
    """أصل أخطاء تعديل الملف الشخصي."""

    code = "profile_update_error"


class EmailAlreadyUsedError(ProfileUpdateError):
    """البريد مستخدَم في حساب آخر."""

    code = "email_already_used"


# 🔒 قائمة بيضاء صريحة. كل ما عداها ممنوع التعديل عبر هذا المسار:
#    - phone: معرّف الدخول نفسه؛ تغييره يتطلب تحقق OTP للرقم الجديد.
#    - role/status/is_active/is_staff: حقول امتياز — تُدار من الإدارة
#      وحدها، وتمكين المستخدم من رفع دوره كان سيكون تصعيد صلاحيات.
UPDATABLE_PROFILE_FIELDS = {"full_name", "email"}


@transaction.atomic
def update_own_profile(user, **fields):
    """
    يعدّل الحقول الشخصية للمستخدم الحالي.

    ⚠️ لا يقبل إلا full_name و email. أي حقل آخر يرفع ProfileUpdateError
       بدل تجاهله صامتًا: العميل يجب أن يعرف أن طلبه لم يُنفَّذ.

    🔒 البريد فريد على مستوى قاعدة البيانات. نفحصه مسبقًا لرسالة مفهومة،
       ونلتقط IntegrityError أيضًا تحسّبًا لسباق بين الفحص والحفظ.
    """
    if user is None or not user.is_authenticated:
        raise ProfileUpdateError("Authentication required.")

    for key in fields:
        if key not in UPDATABLE_PROFILE_FIELDS:
            raise ProfileUpdateError(f"Field '{key}' cannot be updated here.")

    if "full_name" in fields:
        user.full_name = (fields["full_name"] or "").strip()

    if "email" in fields:
        raw = (fields["email"] or "").strip()
        # الفراغ يعني "امسح البريد": clean() يحوّله إلى NULL، وهو المطلوب
        # حتى لا يتعارض حسابان بقيمة "" في حقل فريد.
        normalized = User.objects.normalize_email(raw) if raw else None

        if normalized is not None:
            taken = (
                User.objects.filter(email__iexact=normalized)
                .exclude(pk=user.pk)
                .exists()
            )
            if taken:
                raise EmailAlreadyUsedError("This email is already in use.")

        user.email = normalized

    user.full_clean(exclude=["password", "last_login"])

    try:
        user.save(update_fields=["full_name", "email", "updated_at"])
    except IntegrityError as exc:
        # سباق: سجّل حساب آخر البريد نفسه بين الفحص أعلاه والحفظ
        raise EmailAlreadyUsedError("This email is already in use.") from exc

    logger.info("Profile updated (user_id=%s, fields=%s)", user.id, sorted(fields))
    return user
