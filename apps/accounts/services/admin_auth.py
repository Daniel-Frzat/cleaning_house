"""
Admin Authentication — دخول الإدارة بعاملين.

    1) البريد (أو الهاتف) + كلمة السر
    2) رمز SMS على هاتف الأدمن المسجَّل (OTPPurpose.ADMIN_LOGIN)
       — يُعفى منه جهاز موثوق لمدة ADMIN_TRUSTED_DEVICE_DAYS.

🔒 مسارات دخول العملاء (OTP الهاتف وحده، Apple/Google، أرقام الاختبار)
   مغلقة أمام حسابات الأدمن: رسالة SMS وحدها لا تكفي لصلاحيات الإدارة.

🔒 لا كشف للحسابات: بريد غير موجود، أو ليس لأدمن، أو كلمة سر خاطئة — كلها
   invalid_credentials بالرسالة نفسها.

🔒 قفل مؤقت بعد ADMIN_MAX_FAILED_LOGINS محاولة خاطئة متتالية.

📌 تُستعمل من مسارين: الـAPI (/api/admin/auth/*) لموقع لوحة التحكم، ونموذج
   دخول لوحة Django — القواعد نفسها حرفيًا.
"""

import hashlib
import logging
import secrets

from django.conf import settings
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import AdminLoginChallenge, OTPPurpose, TrustedDevice, User, UserStatus
from . import otp as otp_service
from .tokens import issue_tokens_for_user

logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# الأخطاء
# ------------------------------------------------------------
class AdminAuthError(Exception):
    code = "admin_auth_error"
    status = 400


class InvalidCredentialsError(AdminAuthError):
    code = "invalid_credentials"
    status = 401


class AccountLockedError(AdminAuthError):
    code = "account_locked"
    status = 429

    def __init__(self, retry_after_seconds):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Too many failed attempts. Try again later.")


class AccountInactiveError(AdminAuthError):
    code = "account_inactive"
    status = 403


class NoPhoneForSecondFactorError(AdminAuthError):
    """الأدمن بلا هاتف صالح — لا يمكن إرسال العامل الثاني."""

    code = "second_factor_unavailable"
    status = 409


class ChallengeInvalidError(AdminAuthError):
    code = "challenge_invalid"
    status = 400


class PasswordPolicyError(AdminAuthError):
    code = "password_invalid"
    status = 422


class WrongPasswordError(AdminAuthError):
    code = "wrong_password"
    status = 400


class AdminManagementError(AdminAuthError):
    code = "admin_management_error"
    status = 409


# ------------------------------------------------------------
# السياسة
# ------------------------------------------------------------
def _policy():
    return {
        "max_failed": getattr(settings, "ADMIN_MAX_FAILED_LOGINS", 5),
        "lock_minutes": getattr(settings, "ADMIN_LOCKOUT_MINUTES", 15),
        "device_days": getattr(settings, "ADMIN_TRUSTED_DEVICE_DAYS", 30),
        "challenge_seconds": getattr(settings, "OTP_EXPIRY_SECONDS", 300),
    }


def _hash_device_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def mask_phone(phone):
    """+61400000123 → +614•••••123 — ليعرف الأدمن أين وصل الرمز دون كشفه."""
    if not phone or len(phone) < 7:
        return ""
    return phone[:4] + "•" * (len(phone) - 7) + phone[-3:]


def _find_admin(identifier):
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    if "@" in identifier:
        user = User.objects.filter(email__iexact=identifier).first()
    else:
        try:
            phone = otp_service.normalize_phone(identifier)
        except otp_service.OTPInvalidPhoneError:
            return None
        user = User.objects.filter(phone=phone).first()
    if user is None or not user.has_admin_access():
        return None
    return user


def _has_real_phone(user):
    try:
        otp_service.normalize_phone(user.phone)
        return True
    except otp_service.OTPInvalidPhoneError:
        return False


def _trusted_device(user, raw_token):
    if not raw_token:
        return None
    device = TrustedDevice.objects.filter(
        user=user, token_hash=_hash_device_token(raw_token)
    ).first()
    if device is None or not device.is_valid():
        return None
    return device


# ------------------------------------------------------------
# الخطوة 1: كلمة السر
# ------------------------------------------------------------
def check_password_step(identifier, password):
    """
    يتحقق من كلمة السر مع القفل المؤقت. يعيد المستخدم أو يرفع خطأ.

    ⚠️ ليست atomic ككل عمدًا: عدّاد المحاولات الخاطئة يجب أن يُحفظ رغم
       رفع الاستثناء (نفس سبب otp.verify).
    """
    policy = _policy()
    now = timezone.now()

    with transaction.atomic():
        user = _find_admin(identifier)
        if user is not None:
            user = User.objects.select_for_update().get(pk=user.pk)

        if user is None:
            # كلفة زمنية مماثلة لحساب موجود — لا يكشف التوقيت وجود البريد
            User().set_password(password or "")
            outcome = "invalid"
        elif user.is_login_locked(now):
            outcome = "locked"
        elif not user.check_password(password or ""):
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= policy["max_failed"]:
                user.locked_until = now + timezone.timedelta(minutes=policy["lock_minutes"])
                user.failed_login_attempts = 0
                logger.warning("Admin account locked after failed logins (user_id=%s)", user.id)
            user.save(update_fields=["failed_login_attempts", "locked_until", "updated_at"])
            outcome = "invalid"
        else:
            if user.failed_login_attempts or user.locked_until:
                user.failed_login_attempts = 0
                user.locked_until = None
                user.save(update_fields=["failed_login_attempts", "locked_until", "updated_at"])
            outcome = "ok"

    if outcome == "locked":
        raise AccountLockedError(max(int((user.locked_until - now).total_seconds()), 1))
    if outcome == "invalid":
        raise InvalidCredentialsError("Invalid email or password.")
    if not user.is_active or user.status != UserStatus.ACTIVE:
        raise AccountInactiveError("This account is not active.")
    return user


def begin_login(identifier, password, device_token=None, ip=None):
    """
    الخطوة الأولى. يعيد أحد شكلين:
        {"state": "authenticated", "user": user}           — جهاز موثوق
        {"state": "otp_required", "challenge": ch, "phone_hint": "+614•••123"}
    """
    user = check_password_step(identifier, password)

    device = _trusted_device(user, device_token)
    if device is not None:
        device.last_used_at = timezone.now()
        device.save(update_fields=["last_used_at"])
        logger.info("Admin login via trusted device (user_id=%s)", user.id)
        return {"state": "authenticated", "user": user, "device": device}

    return {"state": "otp_required", **_open_challenge(user, ip)}


def _open_challenge(user, ip):
    if not _has_real_phone(user):
        raise NoPhoneForSecondFactorError(
            "This admin account has no valid phone number for the SMS code. "
            "Ask a superuser to add one."
        )
    # يرفع OTPResendCooldownError / OTPRateLimitError / OTPDeliveryError
    otp_service.generate_and_send(user.phone, purpose=OTPPurpose.ADMIN_LOGIN, ip=ip)
    challenge = AdminLoginChallenge.objects.create(
        user=user,
        ip_address=ip,
        expires_at=timezone.now() + timezone.timedelta(seconds=_policy()["challenge_seconds"]),
    )
    return {"challenge": challenge, "phone_hint": mask_phone(user.phone)}


def _open_challenge_or_error(challenge_id):
    challenge = (
        AdminLoginChallenge.objects.select_related("user").filter(pk=challenge_id).first()
    )
    if challenge is None or not challenge.is_open():
        raise ChallengeInvalidError("This login attempt has expired. Start again.")
    return challenge


def resend_code(challenge_id, ip=None):
    """يعيد إرسال رمز SMS للتحدي نفسه (التهدئة والحدود سارية)."""
    challenge = _open_challenge_or_error(challenge_id)
    otp_service.generate_and_send(challenge.user.phone, purpose=OTPPurpose.ADMIN_LOGIN, ip=ip)
    challenge.expires_at = timezone.now() + timezone.timedelta(
        seconds=_policy()["challenge_seconds"]
    )
    challenge.save(update_fields=["expires_at"])
    return {"challenge": challenge, "phone_hint": mask_phone(challenge.user.phone)}


# ------------------------------------------------------------
# الخطوة 2: رمز SMS
# ------------------------------------------------------------
def complete_login(challenge_id, code, remember_device=False, device_label="", ip=None):
    """
    يتحقق من رمز SMS ويستهلك التحدي. يعيد {"user", "device_token"|None}.

    يرفع OTPError المناسب عند رمز خاطئ/منتهٍ (عدّاد المحاولات محفوظ).
    """
    challenge = _open_challenge_or_error(challenge_id)
    user = challenge.user

    otp_service.verify(user.phone, code, purpose=OTPPurpose.ADMIN_LOGIN)

    with transaction.atomic():
        updated = AdminLoginChallenge.objects.filter(
            pk=challenge.pk, consumed_at__isnull=True
        ).update(consumed_at=timezone.now())
        if not updated:  # سباق: استُهلك في طلب متزامن
            raise ChallengeInvalidError("This login attempt was already used.")

        user.refresh_from_db()
        if not user.is_active or user.status != UserStatus.ACTIVE or not user.has_admin_access():
            raise AccountInactiveError("This account is not active.")

        raw_device = None
        if remember_device:
            raw_device = secrets.token_urlsafe(32)
            TrustedDevice.objects.create(
                user=user,
                token_hash=_hash_device_token(raw_device),
                label=(device_label or "")[:255],
                ip_address=ip,
                last_used_at=timezone.now(),
                expires_at=timezone.now() + timezone.timedelta(days=_policy()["device_days"]),
            )
    logger.info("Admin login completed (user_id=%s, remembered=%s)", user.id, bool(raw_device))
    return {"user": user, "device_token": raw_device}


def issue_admin_tokens(user):
    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    return issue_tokens_for_user(user)


# ------------------------------------------------------------
# كلمة السر والأجهزة والجلسات
# ------------------------------------------------------------
def validate_new_password(password, user=None):
    try:
        password_validation.validate_password(password, user=user)
    except ValidationError as exc:
        raise PasswordPolicyError(" ".join(exc.messages)) from exc


from .sessions import revoke_all_sessions  # noqa: E402 — المصدر الوحيد


@transaction.atomic
def change_own_password(user, current_password, new_password, request=None):
    """
    تغيير الأدمن كلمة سره بنفسه — يُنهي must_change_password.

    🔒 يبطل كل الجلسات والأجهزة الموثوقة الأخرى، ويعيد توكنات جديدة لهذه
       الجلسة وحدها.
    """
    from apps.audit.services.audit import record

    if not user.check_password(current_password or ""):
        raise WrongPasswordError("The current password is incorrect.")
    if current_password == new_password:
        raise PasswordPolicyError("The new password must differ from the current one.")
    validate_new_password(new_password, user)

    user.set_password(new_password)
    user.must_change_password = False
    user.password_changed_at = timezone.now()
    user.save(update_fields=["password", "must_change_password", "password_changed_at", "updated_at"])
    revoke_all_sessions(user)
    record(user, "admin_account.password_change", target=user, request=request)
    return issue_tokens_for_user(user)


def list_trusted_devices(user):
    return TrustedDevice.objects.filter(
        user=user, revoked_at__isnull=True, expires_at__gt=timezone.now()
    )


def revoke_trusted_device(user, device_id, request=None):
    from apps.audit.services.audit import record

    device = TrustedDevice.objects.filter(pk=device_id, user=user, revoked_at__isnull=True).first()
    if device is None:
        return False
    device.revoked_at = timezone.now()
    device.save(update_fields=["revoked_at"])
    record(user, "admin_account.device_revoke", target=device, request=request)
    return True


# ------------------------------------------------------------
# إدارة حسابات الأدمن — Superuser حصرًا (قرار PO — 2026-09-25)
# ------------------------------------------------------------
def _assert_superuser(actor):
    if actor is None or not actor.is_authenticated or not actor.is_superuser:
        raise AdminManagementError("Only a superuser can manage administrators.")


def _temporary_password():
    # 16 حرفًا عشوائيًا — يُعرض مرة واحدة ويجب تغييره عند أول دخول
    return secrets.token_urlsafe(12)


def list_admins(actor):
    _assert_superuser(actor)
    from apps.accounts.roles import ConfirmedRole

    return User.objects.filter(role=ConfirmedRole.ADMIN).order_by("date_joined")


def get_admin(actor, admin_id):
    _assert_superuser(actor)
    from apps.accounts.roles import ConfirmedRole

    return User.objects.filter(pk=admin_id, role=ConfirmedRole.ADMIN).first()


def _normalized_admin_fields(email=None, phone=None):
    out = {}
    if email is not None:
        email = User.objects.normalize_email(email.strip())
        if not email or "@" not in email:
            raise AdminManagementError("A valid email address is required.")
        out["email"] = email
    if phone is not None:
        try:
            out["phone"] = otp_service.normalize_phone(phone)
        except otp_service.OTPInvalidPhoneError as exc:
            raise AdminManagementError("A valid phone number is required for the SMS code.") from exc
    return out


def _assert_unique(email=None, phone=None, exclude_pk=None):
    qs = User.objects.all()
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    if email and qs.filter(email__iexact=email).exists():
        raise AdminManagementError("This email is already used by another account.")
    if phone and qs.filter(phone=phone).exists():
        raise AdminManagementError("This phone number is already used by another account.")


@transaction.atomic
def create_admin(actor, email, phone, full_name="", is_superuser=False, request=None):
    """
    ينشئ حساب أدمن بكلمة سر مؤقتة تُعاد **مرة واحدة**.

    🔒 must_change_password=True: الحساب لا يستعمل أي مسار حتى يغيّرها.
    """
    from apps.accounts.roles import ConfirmedRole
    from apps.audit.services.audit import record

    _assert_superuser(actor)
    fields = _normalized_admin_fields(email=email, phone=phone)
    _assert_unique(**fields)

    temporary = _temporary_password()
    admin = User.objects.create_user(
        phone=fields["phone"],
        email=fields["email"],
        password=temporary,
        full_name=(full_name or "").strip(),
        role=ConfirmedRole.ADMIN,
    )
    admin.is_superuser = bool(is_superuser)
    admin.must_change_password = True
    admin.save()
    record(
        actor, "admin_account.create", target=admin,
        details={"email": admin.email, "is_superuser": admin.is_superuser}, request=request,
    )
    return admin, temporary


def _active_superusers_excluding(pk):
    from apps.accounts.roles import ConfirmedRole

    return (
        User.objects.filter(role=ConfirmedRole.ADMIN, is_superuser=True, status=UserStatus.ACTIVE, is_active=True)
        .exclude(pk=pk)
        .exists()
    )


@transaction.atomic
def update_admin(actor, admin_id, request=None, **changes):
    """
    يعدّل حساب أدمن: الاسم، البريد، الهاتف، الحالة، صلاحية Superuser.

    🔒 لا يعطّل Superuser نفسه ولا يسحب صلاحيته عن نفسه، ولا يُترك النظام
       بلا Superuser نشط.
    """
    from apps.audit.services.audit import record

    _assert_superuser(actor)
    admin = get_admin(actor, admin_id)
    if admin is None:
        return None
    admin = User.objects.select_for_update().get(pk=admin.pk)

    allowed = {"full_name", "email", "phone", "status", "is_superuser"}
    unknown = set(changes) - allowed
    if unknown:
        raise AdminManagementError(f"Cannot change: {', '.join(sorted(unknown))}.")

    normalized = _normalized_admin_fields(email=changes.get("email"), phone=changes.get("phone"))
    _assert_unique(**normalized, exclude_pk=admin.pk)

    before = {k: getattr(admin, k) for k in changes}
    if "status" in changes:
        if changes["status"] not in UserStatus.values:
            raise AdminManagementError("Unknown status.")
        if admin.pk == actor.pk and changes["status"] != UserStatus.ACTIVE:
            raise AdminManagementError("You cannot deactivate your own account.")
    demoting = "is_superuser" in changes and not changes["is_superuser"] and admin.is_superuser
    deactivating = "status" in changes and changes["status"] != UserStatus.ACTIVE and admin.is_superuser
    if demoting and admin.pk == actor.pk:
        raise AdminManagementError("You cannot remove your own superuser access.")
    if (demoting or deactivating) and not _active_superusers_excluding(admin.pk):
        raise AdminManagementError("At least one active superuser must remain.")

    if "full_name" in changes:
        admin.full_name = (changes["full_name"] or "").strip()
    admin.email = normalized.get("email", admin.email)
    admin.phone = normalized.get("phone", admin.phone)
    if "status" in changes:
        admin.status = changes["status"]
    if "is_superuser" in changes:
        admin.is_superuser = bool(changes["is_superuser"])
    admin.save()

    if admin.status != UserStatus.ACTIVE or "phone" in normalized:
        # حساب معطّل، أو هاتف العامل الثاني تغيّر → لا جلسة ولا جهاز موثوق يبقى
        revoke_all_sessions(admin)

    after = {k: getattr(admin, k) for k in changes}
    record(
        actor, "admin_account.update", target=admin,
        details={"before": {k: str(v) for k, v in before.items()},
                 "after": {k: str(v) for k, v in after.items()}},
        request=request,
    )
    return admin


@transaction.atomic
def reset_admin_password(actor, admin_id, request=None):
    """
    Superuser يعيد تعيين كلمة سر أدمن (قرار PO — 2026-09-25).

    يعيد كلمة سر مؤقتة مرة واحدة، ويُجبر الأدمن على تغييرها، ويبطل كل
    جلساته وأجهزته الموثوقة، ويفك أي قفل.
    """
    from apps.audit.services.audit import record

    _assert_superuser(actor)
    admin = get_admin(actor, admin_id)
    if admin is None:
        return None, None
    temporary = _temporary_password()
    admin.set_password(temporary)
    admin.must_change_password = True
    admin.failed_login_attempts = 0
    admin.locked_until = None
    admin.password_changed_at = timezone.now()
    admin.save()
    revoke_all_sessions(admin)
    record(actor, "admin_account.password_reset", target=admin, request=request)
    return admin, temporary


@transaction.atomic
def revoke_admin_sessions(actor, admin_id, request=None):
    from apps.audit.services.audit import record

    _assert_superuser(actor)
    admin = get_admin(actor, admin_id)
    if admin is None:
        return None
    revoke_all_sessions(admin)
    record(actor, "admin_account.sessions_revoke", target=admin, request=request)
    return admin
