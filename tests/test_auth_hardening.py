"""
Auth hardening — أرقام اختبار OTP، حدود الطلب، تجديد التوكن وتسجيل الخروج،
حجب الحسابات الموقوفة، وحصرية ADMIN.
"""

import json
import pathlib
import re

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import Client, RequestFactory

from adapters.sms.dev_console import DevConsoleSMSAdapter
from apps.accounts.api.auth import _client_ip
from apps.accounts.models import OTPVerification, User, UserStatus
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from config.settings.base import _parse_otp_test_numbers

TEST_PHONE = "+61400000001"
TEST_CODE = "246810"


class CapturingSMSAdapter(DevConsoleSMSAdapter):
    sent = []

    def send_otp(self, phone_number, code, *args, **kwargs):
        type(self).sent.append({"phone_number": phone_number, "code": code})
        return {"delivered": True}


class BrokenSMSAdapter:
    """يحاكي DevConsoleSMSAdapter على سيرفر DEBUG=False: يرفض الإنشاء."""

    def __init__(self):
        raise ImproperlyConfigured("no SMS provider")


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.SMS_ADAPTER = "tests.test_auth_hardening.CapturingSMSAdapter"
    settings.OTP_TEST_NUMBERS = {TEST_PHONE: TEST_CODE}
    settings.OTP_RESEND_COOLDOWN_SECONDS = 0
    CapturingSMSAdapter.sent = []
    yield
    CapturingSMSAdapter.sent = []


@pytest.fixture
def client():
    return Client()


def post(client, url, payload, **extra):
    return client.post(url, data=json.dumps(payload), content_type="application/json", **extra)


def bearer(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


# ============================================================
# 1) أرقام الاختبار
# ============================================================
@pytest.mark.django_db
def test_test_number_logs_in_with_fixed_code_without_sms(client):
    r = post(client, "/api/auth/otp/request", {"phone": TEST_PHONE})
    assert r.status_code == 200
    assert CapturingSMSAdapter.sent == []

    r = post(client, "/api/auth/otp/verify", {"phone": TEST_PHONE, "code": TEST_CODE})
    assert r.status_code == 200
    assert r.json()["user"]["phone"] == TEST_PHONE


@pytest.mark.django_db
def test_test_number_still_rejects_a_wrong_code(client):
    post(client, "/api/auth/otp/request", {"phone": TEST_PHONE})
    r = post(client, "/api/auth/otp/verify", {"phone": TEST_PHONE, "code": "000000"})
    assert r.status_code == 400
    assert r.json()["code"] == "otp_invalid_code"


@pytest.mark.django_db
def test_test_number_works_even_when_no_sms_provider_is_configured(client, settings):
    """الحالة الفعلية على Railway: لا مزوّد SMS، ومع ذلك رقم الاختبار يدخل."""
    settings.SMS_ADAPTER = "tests.test_auth_hardening.BrokenSMSAdapter"
    assert post(client, "/api/auth/otp/request", {"phone": TEST_PHONE}).status_code == 200
    r = post(client, "/api/auth/otp/verify", {"phone": TEST_PHONE, "code": TEST_CODE})
    assert r.status_code == 200


@pytest.mark.django_db
def test_other_numbers_are_unaffected_by_test_numbers(client, monkeypatch):
    from apps.accounts.services import otp as otp_service

    monkeypatch.setattr(otp_service, "generate_code", lambda length=None: "111111")
    phone = "+61400000099"
    post(client, "/api/auth/otp/request", {"phone": phone})
    assert CapturingSMSAdapter.sent[-1] == {"phone_number": phone, "code": "111111"}
    # الرمز الثابت لرقم الاختبار لا يفتح رقمًا آخر
    r = post(client, "/api/auth/otp/verify", {"phone": phone, "code": TEST_CODE})
    assert r.status_code == 400


def test_otp_test_numbers_setting_is_validated():
    assert _parse_otp_test_numbers("") == {}
    assert _parse_otp_test_numbers(" +61400000001:123456 , +61400000002:654321 ") == {
        "+61400000001": "123456",
        "+61400000002": "654321",
    }
    for bad in ("+61400000001", "+61400000001:12ab56", "+61400000001:123"):
        with pytest.raises(ImproperlyConfigured):
            _parse_otp_test_numbers(bad)


def test_no_accept_any_code_switch_exists():
    """الخيار القديم كان يفتح كل الحسابات — يجب ألا يعود."""
    root = pathlib.Path(__file__).resolve().parent.parent
    for folder in ("apps", "config", "adapters"):
        for path in (root / folder).rglob("*.py"):
            assert "OTP_ACCEPT_ANY_CODE" not in path.read_text(encoding="utf-8"), path


# ============================================================
# 2) فشل المزوّد ورقم غير صالح وحدود الطلب
# ============================================================
@pytest.mark.django_db
def test_sms_provider_failure_returns_503_and_stores_nothing(client, settings):
    settings.SMS_ADAPTER = "tests.test_auth_hardening.BrokenSMSAdapter"
    r = post(client, "/api/auth/otp/request", {"phone": "+61400000050"})
    assert r.status_code == 503
    assert r.json()["code"] == "otp_delivery_failed"
    assert OTPVerification.objects.count() == 0


@pytest.mark.django_db
def test_invalid_phone_is_rejected(client):
    r = post(client, "/api/auth/otp/request", {"phone": "hello-world"})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_phone"


@pytest.mark.django_db
def test_phone_formatting_is_normalised_to_one_account(client):
    post(client, "/api/auth/otp/request", {"phone": "+61 400 000 001"})
    r = post(client, "/api/auth/otp/verify", {"phone": "+61-400-000-001", "code": TEST_CODE})
    assert r.status_code == 200
    assert r.json()["user"]["phone"] == TEST_PHONE


@pytest.mark.django_db
def test_daily_cap_per_phone(client, settings):
    settings.OTP_MAX_REQUESTS_PER_PHONE_PER_DAY = 3
    for _ in range(3):
        assert post(client, "/api/auth/otp/request", {"phone": "+61400000060"}).status_code == 200
    r = post(client, "/api/auth/otp/request", {"phone": "+61400000060"})
    assert r.status_code == 429
    assert r.json()["code"] == "otp_rate_limited"
    assert r.json()["retry_after_seconds"] > 0


@pytest.mark.django_db
def test_hourly_cap_per_ip(client, settings):
    settings.OTP_MAX_REQUESTS_PER_IP_PER_HOUR = 2
    for i in range(2):
        assert post(client, "/api/auth/otp/request", {"phone": f"+6140000007{i}"}).status_code == 200
    r = post(client, "/api/auth/otp/request", {"phone": "+61400000079"})
    assert r.status_code == 429
    assert r.json()["code"] == "otp_rate_limited"


def test_client_ip_uses_rightmost_trusted_forwarded_entry(settings):
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9", REMOTE_ADDR="10.0.0.1")
    settings.NUM_TRUSTED_PROXIES = 0
    assert _client_ip(req) == "10.0.0.1"
    settings.NUM_TRUSTED_PROXIES = 1
    # العنصر المزوّر من العميل (6.6.6.6) يُتجاهل
    assert _client_ip(req) == "203.0.113.9"


# ============================================================
# 3) تجديد التوكن وتسجيل الخروج
# ============================================================
@pytest.fixture
def user(db):
    return User.objects.create_user(phone="+61400000100", role=ConfirmedRole.CUSTOMER)


@pytest.mark.django_db
def test_refresh_rotates_and_old_token_cannot_be_reused(client, user):
    tokens = issue_tokens_for_user(user)

    r = post(client, "/api/auth/token/refresh", {"refresh": tokens["refresh"]})
    assert r.status_code == 200
    new = r.json()
    assert new["refresh"] != tokens["refresh"]
    assert client.get("/api/auth/me", **bearer(new["access"])).status_code == 200

    r = post(client, "/api/auth/token/refresh", {"refresh": tokens["refresh"]})
    assert r.status_code == 401
    assert r.json()["code"] == "token_not_valid"


@pytest.mark.django_db
def test_refresh_rejects_garbage_and_access_tokens(client, user):
    tokens = issue_tokens_for_user(user)
    assert post(client, "/api/auth/token/refresh", {"refresh": "nope"}).status_code == 401
    assert post(client, "/api/auth/token/refresh", {"refresh": tokens["access"]}).status_code == 401


@pytest.mark.django_db
def test_suspended_user_cannot_refresh(client, user):
    tokens = issue_tokens_for_user(user)
    user.status = UserStatus.SUSPENDED
    user.save()
    assert post(client, "/api/auth/token/refresh", {"refresh": tokens["refresh"]}).status_code == 401


@pytest.mark.django_db
def test_logout_revokes_refresh_and_is_idempotent(client, user):
    tokens = issue_tokens_for_user(user)
    assert post(client, "/api/auth/logout", {"refresh": tokens["refresh"]}).status_code == 204
    assert post(client, "/api/auth/logout", {"refresh": tokens["refresh"]}).status_code == 204
    assert post(client, "/api/auth/logout", {"refresh": "garbage"}).status_code == 204
    assert post(client, "/api/auth/token/refresh", {"refresh": tokens["refresh"]}).status_code == 401


# ============================================================
# 4) الحساب الموقوف يفقد الوصول فورًا
# ============================================================
@pytest.mark.django_db
@pytest.mark.parametrize("status", [UserStatus.SUSPENDED, UserStatus.INACTIVE])
def test_live_access_token_of_non_active_user_is_rejected(client, user, status):
    access = issue_tokens_for_user(user)["access"]
    assert client.get("/api/auth/me", **bearer(access)).status_code == 200
    user.status = status
    user.save()
    assert client.get("/api/auth/me", **bearer(access)).status_code == 401
    assert client.get("/api/bookings", **bearer(access)).status_code == 401


def test_no_router_uses_the_plain_jwt_auth():
    """JWTAuth وحده يتجاهل status — كل router يجب أن يستخدم ActiveUserJWTAuth."""
    root = pathlib.Path(__file__).resolve().parent.parent / "apps"
    offenders = []
    for path in root.glob("*/api/*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bJWTAuth\(\)", text) or "from ninja_jwt.authentication import JWTAuth" in text:
            offenders.append(str(path))
    assert offenders == []


# ============================================================
# 5) ADMIN حصري
# ============================================================
@pytest.mark.django_db
def test_admin_never_has_contractor_access_even_with_stale_flag():
    admin = User.objects.create_user(phone="+61400000200", role=ConfirmedRole.ADMIN)
    User.objects.filter(pk=admin.pk).update(is_contractor=True)  # بقايا ترقية قديمة
    admin.refresh_from_db()
    assert admin.has_contractor_access() is False
    assert admin.active_roles() == [ConfirmedRole.ADMIN]


@pytest.mark.django_db
def test_admin_with_contractor_flag_fails_validation():
    admin = User(phone="+61400000201", role=ConfirmedRole.ADMIN, is_contractor=True)
    with pytest.raises(ValidationError):
        admin.full_clean(exclude=["password", "last_login"])
