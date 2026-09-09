"""
OTP Service — Identity Domain (Phase 1)

يحتوي كامل منطق توليد رموز OTP والتحقق منها. الـModel يبقى بيانات وحالة فقط.

🔒 قواعد أمنية مطبَّقة هنا:
  - الرمز الخام لا يُخزَّن ولا يُعاد في أي استجابة API.
  - الرمز يُولَّد بـsecrets (CSPRNG) وليس random.
  - المقارنة عبر constant-time comparison لتفادي timing attacks.
  - الرمز الخام يظهر فقط عبر DevConsoleSMSAdapter في بيئة التطوير.

⚠️ أرقام السياسة (expiry / max attempts / cooldown) تأتي من settings وهي
   defaults آمنة بانتظار تأكيد Product Owner — ليست قواعد عمل محسومة.
"""

import hashlib
import hmac
import logging
import secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from adapters.sms import get_sms_adapter

from ..models import OTPPurpose, OTPStatus, OTPVerification

logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# أخطاء الـDomain (تُترجَم لاحقًا إلى استجابات API في طبقة الـviews)
# ------------------------------------------------------------
class OTPError(Exception):
    """أصل كل أخطاء OTP."""

    code = "otp_error"


class OTPResendCooldownError(OTPError):
    code = "otp_resend_cooldown"

    def __init__(self, retry_after_seconds):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Please wait {retry_after_seconds}s before requesting another code."
        )


class OTPNotFoundError(OTPError):
    code = "otp_not_found"


class OTPExpiredError(OTPError):
    code = "otp_expired"


class OTPMaxAttemptsError(OTPError):
    code = "otp_max_attempts"


class OTPInvalidCodeError(OTPError):
    code = "otp_invalid_code"

    def __init__(self, remaining_attempts):
        self.remaining_attempts = remaining_attempts
        super().__init__("Invalid code.")


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _policy():
    """يقرأ سياسة OTP من settings عند كل استدعاء (يسمح بالتجاوز في الاختبارات)."""
    return {
        "expiry_seconds": getattr(settings, "OTP_EXPIRY_SECONDS", 300),
        "max_attempts": getattr(settings, "OTP_MAX_ATTEMPTS", 5),
        "cooldown_seconds": getattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 60),
        "code_length": getattr(settings, "OTP_CODE_LENGTH", 6),
    }


def _normalize_phone(phone):
    return phone.strip() if isinstance(phone, str) else phone


def generate_code(length=None):
    """رمز رقمي عشوائي آمن (CSPRNG). يسمح بأصفار بادئة."""
    length = length or _policy()["code_length"]
    return "".join(secrets.choice("0123456789") for _ in range(length))


def hash_code(code, phone):
    """
    Hash للرمز مربوط بالهاتف وبـSECRET_KEY.

    يُستخدم HMAC-SHA256 لا Django password hashers: الرمز قصير وقصير العمر،
    والتحقق يجب أن يكون سريعًا، والربط بالهاتف يمنع إعادة استخدام hash
    عبر أرقام مختلفة.
    """
    msg = f"{_normalize_phone(phone)}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def _verify_hash(code, phone, expected_hash):
    """مقارنة ثابتة الزمن."""
    return hmac.compare_digest(hash_code(code, phone), expected_hash)


def _active_pending(phone, purpose):
    """آخر رمز PENDING لهذا الرقم/الغرض."""
    return (
        OTPVerification.objects.filter(
            phone=_normalize_phone(phone), purpose=purpose, status=OTPStatus.PENDING
        )
        .order_by("-created_at")
        .first()
    )


# ------------------------------------------------------------
# API الخدمة
# ------------------------------------------------------------
@transaction.atomic
def generate_and_send(phone, purpose=OTPPurpose.LOGIN):
    """
    ينشئ رمزًا جديدًا، يخزّن hash فقط، ويرسله عبر SMS Adapter المُعرَّف
    في settings.

    يُبطل أي رمز PENDING سابق لنفس الرقم/الغرض (رمز واحد فعّال في كل لحظة).

    يرفع OTPResendCooldownError إذا طُلب رمز جديد قبل انتهاء فترة التهدئة.

    يعيد OTPVerification — بدون الرمز الخام إطلاقًا.
    """
    phone = _normalize_phone(phone)
    policy = _policy()
    now = timezone.now()

    # 1) فحص فترة التهدئة (Rate limiting — متطلب أمني إلزامي)
    last = (
        OTPVerification.objects.filter(phone=phone, purpose=purpose)
        .order_by("-created_at")
        .first()
    )
    if last is not None:
        elapsed = (now - last.created_at).total_seconds()
        if elapsed < policy["cooldown_seconds"]:
            retry_after = int(policy["cooldown_seconds"] - elapsed)
            raise OTPResendCooldownError(max(retry_after, 1))

    # 2) إبطال الرمز المعلّق السابق — الرمز القديم لا يعود صالحًا
    OTPVerification.objects.filter(
        phone=phone, purpose=purpose, status=OTPStatus.PENDING
    ).update(status=OTPStatus.EXPIRED)

    # 3) توليد وتخزين الـhash فقط
    code = generate_code(policy["code_length"])
    otp = OTPVerification.objects.create(
        phone=phone,
        code_hash=hash_code(code, phone),
        purpose=purpose,
        status=OTPStatus.PENDING,
        expires_at=now + timezone.timedelta(seconds=policy["expiry_seconds"]),
        max_attempts=policy["max_attempts"],
    )

    # 4) الإرسال عبر الـAdapter (الرمز الخام يعيش هنا فقط، ولا يُسجَّل)
    get_sms_adapter().send_otp(phone_number=phone, code=code)

    # 🔒 لا يُسجَّل الرمز — فقط معرّف السجل
    logger.info("OTP generated (id=%s, purpose=%s)", otp.id, purpose)
    return otp


def verify(phone, code, purpose=OTPPurpose.LOGIN):
    """
    يتحقق من الرمز.

    يحدّث الحالة إلى VERIFIED / EXPIRED / FAILED حسب النتيجة، ويزيد
    attempts_count عند كل محاولة خاطئة.

    Idempotent ضد إعادة الاستخدام (replay): الرمز المُتحقَّق منه مسبقًا
    ينتقل خارج حالة PENDING، فلا يُقبل مرة أخرى.

    يعيد OTPVerification عند النجاح، ويرفع OTPError المناسب عند الفشل.

    ⚠️ ملاحظة معمارية مهمة: هذه الدالة ليست atomic ككل عمدًا.
    تسجيل المحاولات الفاشلة وتغيير الحالة (EXPIRED/FAILED) يجب أن *يبقى*
    محفوظًا رغم رفع الاستثناء. لو غلّفنا الدالة بـtransaction.atomic
    لأدى الاستثناء إلى التراجع عن عدّاد المحاولات نفسه، وهو ما يُبطل
    الحماية من التخمين (brute force). لذلك تُستخدم معاملات ذرّية قصيرة
    داخليًا فقط حول القراءة/الكتابة لكل خطوة.
    """
    phone = _normalize_phone(phone)

    # قفل السجل وتحديث الحالة داخل معاملة قصيرة، ثم رفع الاستثناء خارجها
    # حتى لا يتراجع التحديث.
    with transaction.atomic():
        # select_for_update يمنع التحقق المتزامن من تجاوز عدّاد المحاولات
        otp = (
            OTPVerification.objects.select_for_update()
            .filter(phone=phone, purpose=purpose, status=OTPStatus.PENDING)
            .order_by("-created_at")
            .first()
        )

        # لا يوجد رمز فعّال — يشمل إعادة استخدام رمز مُتحقَّق منه سابقًا
        if otp is None:
            outcome = ("not_found", None)

        # 1) انتهاء الصلاحية له الأولوية على أي فحص آخر
        elif otp.is_expired():
            otp.status = OTPStatus.EXPIRED
            otp.save(update_fields=["status"])
            outcome = ("expired", otp)

        # 2) استُنفدت المحاولات مسبقًا
        elif otp.attempts_exhausted():
            otp.status = OTPStatus.FAILED
            otp.save(update_fields=["status"])
            outcome = ("max_attempts", otp)

        # 3) مقارنة ثابتة الزمن
        elif not _verify_hash(code, phone, otp.code_hash):
            otp.attempts_count += 1
            if otp.attempts_exhausted():
                # استُنفدت المحاولات بهذه المحاولة الخاطئة → قفل نهائي
                otp.status = OTPStatus.FAILED
                otp.save(update_fields=["attempts_count", "status"])
                outcome = ("max_attempts", otp)
            else:
                otp.save(update_fields=["attempts_count"])
                outcome = ("invalid", otp)

        # 4) نجاح
        else:
            otp.status = OTPStatus.VERIFIED
            otp.save(update_fields=["status"])
            outcome = ("verified", otp)

    # الاستثناءات تُرفع بعد إغلاق المعاملة → التحديثات أعلاه محفوظة
    kind, otp = outcome
    if kind == "not_found":
        raise OTPNotFoundError("No active verification code for this phone number.")
    if kind == "expired":
        raise OTPExpiredError("This verification code has expired.")
    if kind == "max_attempts":
        raise OTPMaxAttemptsError("Too many incorrect attempts.")
    if kind == "invalid":
        raise OTPInvalidCodeError(otp.max_attempts - otp.attempts_count)

    logger.info("OTP verified (id=%s)", otp.id)
    return otp
