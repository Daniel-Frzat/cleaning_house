"""
OTP Service Tests — Identity Domain (Phase 1)

يغطي: التوليد والإرسال، التحقق الناجح، استنفاد المحاولات، انتهاء الصلاحية،
فترة التهدئة، ومنع إعادة الاستخدام (replay).
"""

import pytest
from django.utils import timezone

from adapters.sms.base import BaseSMSAdapter
from adapters.sms.dev_console import DevConsoleSMSAdapter
from apps.accounts.models import OTPPurpose, OTPStatus, OTPVerification
from apps.accounts.services import otp as otp_service


# ------------------------------------------------------------
# Spy adapter — يلتقط ما استلمه الـadapter فعليًا
# ------------------------------------------------------------
class SpySMSAdapter(DevConsoleSMSAdapter):
    """يرث DevConsoleSMSAdapter ويسجّل الاستدعاءات للفحص."""

    calls = []

    def send_otp(self, phone_number: str, code: str, *args, **kwargs):
        result = super().send_otp(phone_number, code, *args, **kwargs)
        type(self).calls.append({"phone_number": phone_number, "code": code})
        return result


@pytest.fixture
def spy_sms(settings):
    SpySMSAdapter.calls = []
    settings.SMS_ADAPTER = "tests.test_otp_service.SpySMSAdapter"
    return SpySMSAdapter


@pytest.fixture(autouse=True)
def _otp_policy(settings):
    """سياسة صريحة ومستقرة للاختبارات (لا تعتمد على قيم البيئة)."""
    settings.OTP_EXPIRY_SECONDS = 300
    settings.OTP_MAX_ATTEMPTS = 5
    settings.OTP_RESEND_COOLDOWN_SECONDS = 60
    settings.OTP_CODE_LENGTH = 6


PHONE = "+96550001234"


def _wrong_code(correct):
    """رمز مختلف عن الصحيح بنفس الطول."""
    return "000000" if correct != "000000" else "111111"


# ============================================================
# 1) generate_and_send
# ============================================================
@pytest.mark.django_db
def test_generate_and_send_creates_pending_otp_and_calls_adapter(spy_sms):
    otp = otp_service.generate_and_send(PHONE)

    # سجل PENDING أُنشئ
    assert otp.status == OTPStatus.PENDING
    assert otp.purpose == OTPPurpose.LOGIN
    assert otp.phone == PHONE
    assert otp.attempts_count == 0
    assert otp.max_attempts == 5
    assert otp.expires_at > timezone.now()
    assert OTPVerification.objects.filter(status=OTPStatus.PENDING).count() == 1

    # الـadapter استلم نفس الهاتف والرمز الصحيح
    assert len(spy_sms.calls) == 1
    call = spy_sms.calls[0]
    assert call["phone_number"] == PHONE
    assert len(call["code"]) == 6
    assert call["code"].isdigit()

    # الرمز المُرسَل يطابق الـhash المخزَّن
    assert otp_service.hash_code(call["code"], PHONE) == otp.code_hash


@pytest.mark.django_db
def test_raw_code_is_never_stored(spy_sms):
    otp = otp_service.generate_and_send(PHONE)
    code = spy_sms.calls[0]["code"]

    # الرمز الخام غير موجود في أي حقل
    assert otp.code_hash != code
    assert code not in otp.code_hash
    values = OTPVerification.objects.filter(pk=otp.pk).values().get()
    assert code not in str(values)
    # لا يوجد حقل يحمل الرمز الخام
    assert not hasattr(otp, "code")


@pytest.mark.django_db
def test_adapter_is_resolved_from_settings(spy_sms):
    from adapters.sms import get_sms_adapter

    adapter = get_sms_adapter()
    assert isinstance(adapter, BaseSMSAdapter)
    assert isinstance(adapter, SpySMSAdapter)


# ============================================================
# 2) verify — نجاح
# ============================================================
@pytest.mark.django_db
def test_verify_with_correct_code_within_expiry(spy_sms):
    otp_service.generate_and_send(PHONE)
    code = spy_sms.calls[0]["code"]

    verified = otp_service.verify(PHONE, code)

    assert verified.status == OTPStatus.VERIFIED
    verified.refresh_from_db()
    assert verified.status == OTPStatus.VERIFIED


@pytest.mark.django_db
def test_verified_code_cannot_be_replayed(spy_sms):
    """Idempotent ضد replay: الرمز الصحيح لا يعمل مرتين."""
    otp_service.generate_and_send(PHONE)
    code = spy_sms.calls[0]["code"]

    otp_service.verify(PHONE, code)

    with pytest.raises(otp_service.OTPNotFoundError):
        otp_service.verify(PHONE, code)


@pytest.mark.django_db
def test_verify_without_any_code_raises(spy_sms):
    with pytest.raises(otp_service.OTPNotFoundError):
        otp_service.verify(PHONE, "123456")


# ============================================================
# 3) verify — محاولات خاطئة حتى الاستنفاد
# ============================================================
@pytest.mark.django_db
def test_wrong_code_fails_after_max_attempts_then_rejects_correct_code(spy_sms):
    otp_service.generate_and_send(PHONE)
    correct = spy_sms.calls[0]["code"]
    wrong = _wrong_code(correct)

    # المحاولات الخاطئة قبل الأخيرة: بعد المحاولة رقم n يتبقى 5 - n
    for attempt_number in range(1, 5):
        with pytest.raises(otp_service.OTPInvalidCodeError) as exc:
            otp_service.verify(PHONE, wrong)
        assert exc.value.remaining_attempts == 5 - attempt_number

    otp = OTPVerification.objects.get(phone=PHONE)
    assert otp.attempts_count == 4
    assert otp.status == OTPStatus.PENDING

    # المحاولة الخامسة تستنفد الحد → FAILED
    with pytest.raises(otp_service.OTPMaxAttemptsError):
        otp_service.verify(PHONE, wrong)

    otp.refresh_from_db()
    assert otp.status == OTPStatus.FAILED
    assert otp.attempts_count == 5

    # حتى الرمز الصحيح يُرفض بعد الاستنفاد
    with pytest.raises(otp_service.OTPError):
        otp_service.verify(PHONE, correct)

    otp.refresh_from_db()
    assert otp.status == OTPStatus.FAILED


# ============================================================
# 4) verify — انتهاء الصلاحية
# ============================================================
@pytest.mark.django_db
def test_verify_after_expiry_marks_expired_and_rejects(spy_sms):
    otp_service.generate_and_send(PHONE)
    code = spy_sms.calls[0]["code"]

    # دفع وقت الانتهاء إلى الماضي
    otp = OTPVerification.objects.get(phone=PHONE)
    otp.expires_at = timezone.now() - timezone.timedelta(seconds=1)
    otp.save(update_fields=["expires_at"])

    with pytest.raises(otp_service.OTPExpiredError):
        otp_service.verify(PHONE, code)

    otp.refresh_from_db()
    assert otp.status == OTPStatus.EXPIRED

    # ولا يُقبل بعد ذلك
    with pytest.raises(otp_service.OTPError):
        otp_service.verify(PHONE, code)


@pytest.mark.django_db
def test_expiry_uses_configured_setting(settings, spy_sms):
    settings.OTP_EXPIRY_SECONDS = 120
    before = timezone.now()
    otp = otp_service.generate_and_send(PHONE)
    delta = (otp.expires_at - before).total_seconds()
    assert 115 <= delta <= 125


# ============================================================
# 5) resend cooldown
# ============================================================
@pytest.mark.django_db
def test_resend_before_cooldown_is_rejected(spy_sms):
    otp_service.generate_and_send(PHONE)

    with pytest.raises(otp_service.OTPResendCooldownError) as exc:
        otp_service.generate_and_send(PHONE)

    assert exc.value.retry_after_seconds > 0
    # لم يُرسل رمز ثانٍ
    assert len(spy_sms.calls) == 1
    assert OTPVerification.objects.count() == 1


@pytest.mark.django_db
def test_resend_after_cooldown_succeeds_and_invalidates_previous(spy_sms):
    first = otp_service.generate_and_send(PHONE)
    first_code = spy_sms.calls[0]["code"]

    # محاكاة انقضاء فترة التهدئة
    OTPVerification.objects.filter(pk=first.pk).update(
        created_at=timezone.now() - timezone.timedelta(seconds=61)
    )

    second = otp_service.generate_and_send(PHONE)
    second_code = spy_sms.calls[1]["code"]

    assert second.pk != first.pk
    assert second.status == OTPStatus.PENDING

    # الرمز السابق أُبطل
    first.refresh_from_db()
    assert first.status == OTPStatus.EXPIRED

    # الرمز القديم لم يعد يعمل
    with pytest.raises(otp_service.OTPError):
        otp_service.verify(PHONE, first_code)

    # الرمز الجديد يعمل
    verified = otp_service.verify(PHONE, second_code)
    assert verified.status == OTPStatus.VERIFIED


# ============================================================
# سلوك عام
# ============================================================
@pytest.mark.django_db
def test_codes_are_isolated_per_phone(spy_sms):
    other = "+96550009999"
    otp_service.generate_and_send(PHONE)
    code_a = spy_sms.calls[0]["code"]
    otp_service.generate_and_send(other)
    code_b = spy_sms.calls[1]["code"]

    # رمز رقم آخر لا يُقبل هنا (إلا إذا تطابقا عشوائيًا)
    if code_a != code_b:
        with pytest.raises(otp_service.OTPInvalidCodeError):
            otp_service.verify(other, code_a)

    assert otp_service.verify(other, code_b).status == OTPStatus.VERIFIED


@pytest.mark.django_db
def test_hash_is_bound_to_phone():
    """نفس الرمز لرقمين مختلفين يعطي hash مختلفًا."""
    assert otp_service.hash_code("123456", "+96550000001") != otp_service.hash_code(
        "123456", "+96550000002"
    )


@pytest.mark.django_db
def test_purpose_defaults_to_login_and_is_extensible(spy_sms):
    otp = otp_service.generate_and_send(PHONE)
    assert otp.purpose == OTPPurpose.LOGIN
    assert "LOGIN" in OTPPurpose.values


def test_generate_code_length_and_charset():
    code = otp_service.generate_code(6)
    assert len(code) == 6 and code.isdigit()
    assert len({otp_service.generate_code(6) for _ in range(50)}) > 1


@pytest.mark.django_db
def test_str_representation(spy_sms):
    otp = otp_service.generate_and_send(PHONE)
    assert str(otp) == f"OTP LOGIN for {PHONE} (PENDING)"
