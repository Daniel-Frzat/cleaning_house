"""
Auth API Integration Tests — Identity Domain (Phase 1)

يغطي الدورة الكاملة: طلب رمز → تحقق → JWT → /me، إضافة إلى الدخول
الاجتماعي، وحالات الرفض (بدون توكن / توكن غير صالح / تجاوز الحد).
"""

import json

import pytest
from django.test import Client

from adapters.social_auth import ProviderProfile
from adapters.social_auth.fake import FakeSocialAuthAdapter
from adapters.sms.dev_console import DevConsoleSMSAdapter
from apps.accounts.models import OTPVerification, SocialProvider, User, UserStatus
from apps.accounts.roles import ConfirmedRole

PHONE = "+96550120001"


# ------------------------------------------------------------
# Adapter يلتقط الرمز المُرسَل (نفس آلية الاستبدال عبر الإعدادات)
# ------------------------------------------------------------
class CapturingSMSAdapter(DevConsoleSMSAdapter):
    sent = []

    def send_otp(self, phone_number: str, code: str, *args, **kwargs):
        result = super().send_otp(phone_number, code, *args, **kwargs)
        type(self).sent.append({"phone_number": phone_number, "code": code})
        return result


@pytest.fixture(autouse=True)
def _adapters(settings):
    settings.SMS_ADAPTER = "tests.test_auth_api.CapturingSMSAdapter"
    settings.SMS_DEV_ALLOW_INSECURE = True
    settings.SOCIAL_AUTH_ADAPTER = "adapters.social_auth.fake.FakeSocialAuthAdapter"
    settings.SOCIAL_AUTH_ALLOW_FAKE = True
    settings.OTP_EXPIRY_SECONDS = 300
    settings.OTP_MAX_ATTEMPTS = 5
    settings.OTP_RESEND_COOLDOWN_SECONDS = 60
    CapturingSMSAdapter.sent = []
    FakeSocialAuthAdapter.reset()
    yield
    CapturingSMSAdapter.sent = []
    FakeSocialAuthAdapter.reset()


@pytest.fixture
def client():
    return Client()


def post(client, url, payload, **extra):
    return client.post(url, data=json.dumps(payload), content_type="application/json", **extra)


def last_code():
    return CapturingSMSAdapter.sent[-1]["code"]


def bearer(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


# ============================================================
# 1) الدورة الكاملة: request → verify → JWT → /me
# ============================================================
@pytest.mark.django_db
def test_full_otp_round_trip_to_authenticated_me(client):
    # --- طلب الرمز ---
    r = post(client, "/api/auth/otp/request", {"phone": PHONE})
    assert r.status_code == 200
    body = r.json()
    assert body["detail"] == "Verification code sent."
    assert body["expires_in_seconds"] == 300

    # 🔒 الرمز لا يظهر في الاستجابة إطلاقًا
    code = last_code()
    assert code not in r.content.decode()
    assert "code" not in body

    # --- التحقق ---
    r = post(client, "/api/auth/otp/verify", {"phone": PHONE, "code": code})
    assert r.status_code == 200, r.content
    data = r.json()

    assert "access" in data["tokens"] and "refresh" in data["tokens"]
    assert data["user"]["phone"] == PHONE
    assert data["user"]["role"] == ConfirmedRole.CUSTOMER
    assert data["user"]["status"] == UserStatus.ACTIVE

    # أُنشئ المستخدم عند أول تحقق ناجح
    assert User.objects.filter(phone=PHONE).count() == 1

    # --- /me بالتوكن ---
    access = data["tokens"]["access"]
    r = client.get("/api/auth/me", **bearer(access))
    assert r.status_code == 200, r.content
    me = r.json()
    assert me["phone"] == PHONE
    assert me["role"] == ConfirmedRole.CUSTOMER
    assert me["status"] == UserStatus.ACTIVE
    assert me["id"] == data["user"]["id"]


@pytest.mark.django_db
def test_role_is_present_in_jwt_claims(client):
    """الدور مطلوب في الـclaims لتتمكن الـDomains اللاحقة من التحقق."""
    from ninja_jwt.tokens import AccessToken

    post(client, "/api/auth/otp/request", {"phone": PHONE})
    r = post(client, "/api/auth/otp/verify", {"phone": PHONE, "code": last_code()})
    access = r.json()["tokens"]["access"]

    claims = AccessToken(access)
    assert claims["role"] == ConfirmedRole.CUSTOMER
    assert claims["status"] == UserStatus.ACTIVE
    assert claims["phone"] == PHONE


@pytest.mark.django_db
def test_existing_user_is_not_duplicated_on_verify(client):
    existing = User.objects.create_user(phone=PHONE, full_name="Existing")

    post(client, "/api/auth/otp/request", {"phone": PHONE})
    r = post(client, "/api/auth/otp/verify", {"phone": PHONE, "code": last_code()})

    assert r.status_code == 200
    assert User.objects.filter(phone=PHONE).count() == 1
    assert r.json()["user"]["id"] == str(existing.id)


@pytest.mark.django_db
def test_verify_with_wrong_code_is_rejected(client):
    post(client, "/api/auth/otp/request", {"phone": PHONE})
    correct = last_code()
    wrong = "000000" if correct != "000000" else "111111"

    r = post(client, "/api/auth/otp/verify", {"phone": PHONE, "code": wrong})
    assert r.status_code == 400
    assert r.json()["code"] == "otp_invalid_code"
    # لم يُنشأ مستخدم
    assert User.objects.filter(phone=PHONE).count() == 0


@pytest.mark.django_db
def test_inactive_user_cannot_complete_otp_login(client):
    User.objects.create_user(phone=PHONE, status=UserStatus.SUSPENDED)
    post(client, "/api/auth/otp/request", {"phone": PHONE})

    r = post(client, "/api/auth/otp/verify", {"phone": PHONE, "code": last_code()})
    assert r.status_code == 403
    assert r.json()["code"] == "inactive_user"


# ============================================================
# 2) /me بدون توكن → 401
# ============================================================
@pytest.mark.django_db
def test_me_without_token_returns_401(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


# ============================================================
# 3) /me بتوكن غير صالح/منتهٍ → 401
# ============================================================
@pytest.mark.django_db
def test_me_with_malformed_token_returns_401(client):
    r = client.get("/api/auth/me", **bearer("not-a-real-token"))
    assert r.status_code == 401


@pytest.mark.django_db
def test_me_with_expired_token_returns_401(client):
    from datetime import timedelta

    from ninja_jwt.tokens import AccessToken

    user = User.objects.create_user(phone=PHONE)
    token = AccessToken.for_user(user)
    # دفع صلاحية التوكن إلى الماضي
    token.set_exp(from_time=token.current_time - timedelta(days=2), lifetime=timedelta(seconds=1))

    r = client.get("/api/auth/me", **bearer(str(token)))
    assert r.status_code == 401


@pytest.mark.django_db
def test_me_with_tampered_signature_returns_401(client):
    post(client, "/api/auth/otp/request", {"phone": PHONE})
    r = post(client, "/api/auth/otp/verify", {"phone": PHONE, "code": last_code()})
    access = r.json()["tokens"]["access"]

    tampered = access[:-3] + ("abc" if not access.endswith("abc") else "xyz")
    r = client.get("/api/auth/me", **bearer(tampered))
    assert r.status_code == 401


@pytest.mark.django_db
def test_refresh_token_is_not_accepted_as_access_token(client):
    post(client, "/api/auth/otp/request", {"phone": PHONE})
    r = post(client, "/api/auth/otp/verify", {"phone": PHONE, "code": last_code()})
    refresh = r.json()["tokens"]["refresh"]

    r = client.get("/api/auth/me", **bearer(refresh))
    assert r.status_code == 401


# ============================================================
# 4) الدخول الاجتماعي → JWT صالح
# ============================================================
@pytest.mark.django_db
def test_social_login_round_trip_issues_valid_jwt(client):
    FakeSocialAuthAdapter.register_token(
        "tok-google",
        ProviderProfile(
            provider=SocialProvider.GOOGLE,
            provider_user_id="google-sub-api-1",
            email="social@example.com",
            full_name="Social User",
            raw={"sub": "google-sub-api-1", "secret_field": "must-not-leak"},
        ),
    )

    r = post(client, "/api/auth/social/GOOGLE", {"token": "tok-google"})
    assert r.status_code == 200, r.content
    data = r.json()

    assert data["user"]["role"] == ConfirmedRole.CUSTOMER
    # 🔒 لا تتسرّب الحمولة الخام من المزوّد
    assert "raw_profile" not in r.content.decode()
    assert "must-not-leak" not in r.content.decode()

    # التوكن الصادر يعمل فعليًا على /me
    r = client.get("/api/auth/me", **bearer(data["tokens"]["access"]))
    assert r.status_code == 200
    assert r.json()["id"] == data["user"]["id"]


@pytest.mark.django_db
def test_social_login_accepts_lowercase_provider(client):
    FakeSocialAuthAdapter.register_token(
        "tok-apple",
        ProviderProfile(
            provider=SocialProvider.APPLE,
            provider_user_id="apple-sub-api-1",
            email="apple@example.com",
        ),
    )
    r = post(client, "/api/auth/social/apple", {"token": "tok-apple"})
    assert r.status_code == 200


@pytest.mark.django_db
def test_social_login_with_invalid_token_returns_401(client):
    r = post(client, "/api/auth/social/GOOGLE", {"token": "bogus"})
    assert r.status_code == 401
    assert r.json()["code"] == "social_auth_failed"


@pytest.mark.django_db
def test_unsupported_social_provider_returns_400(client):
    r = post(client, "/api/auth/social/FACEBOOK", {"token": "whatever"})
    assert r.status_code == 400
    assert r.json()["code"] == "unsupported_provider"


@pytest.mark.django_db
def test_social_login_reuses_existing_identity(client):
    profile = ProviderProfile(
        provider=SocialProvider.GOOGLE,
        provider_user_id="google-sub-repeat",
        email="repeat@example.com",
    )
    FakeSocialAuthAdapter.register_token("tok-1", profile)

    r1 = post(client, "/api/auth/social/GOOGLE", {"token": "tok-1"})
    r2 = post(client, "/api/auth/social/GOOGLE", {"token": "tok-1"})

    assert r1.json()["user"]["id"] == r2.json()["user"]["id"]
    assert User.objects.count() == 1


# ============================================================
# 5) تكرار طلب الرمز داخل فترة التهدئة → رفض بلا رمز جديد
# ============================================================
@pytest.mark.django_db
def test_second_otp_request_within_cooldown_is_rate_limited(client):
    r1 = post(client, "/api/auth/otp/request", {"phone": PHONE})
    assert r1.status_code == 200
    assert len(CapturingSMSAdapter.sent) == 1

    r2 = post(client, "/api/auth/otp/request", {"phone": PHONE})
    assert r2.status_code == 429
    body = r2.json()
    assert body["code"] == "otp_resend_cooldown"
    assert body["retry_after_seconds"] > 0

    # 🔒 لم يُرسل رمز ثانٍ ولم يُنشأ سجل ثانٍ
    assert len(CapturingSMSAdapter.sent) == 1
    assert OTPVerification.objects.filter(phone=PHONE).count() == 1


@pytest.mark.django_db
def test_cooldown_uses_the_shared_otp_setting(settings, client):
    """الحد يستخدم OTP_RESEND_COOLDOWN_SECONDS نفسه — لا سياسة منفصلة."""
    settings.OTP_RESEND_COOLDOWN_SECONDS = 0

    r1 = post(client, "/api/auth/otp/request", {"phone": PHONE})
    r2 = post(client, "/api/auth/otp/request", {"phone": PHONE})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert len(CapturingSMSAdapter.sent) == 2


# ============================================================
# صحة العقود والأمان العام
# ============================================================
@pytest.mark.django_db
def test_otp_request_validates_payload(client):
    r = post(client, "/api/auth/otp/request", {})
    assert r.status_code == 422


@pytest.mark.django_db
def test_no_password_login_endpoint_exists(client):
    """
    العملاء والمقاولون بلا كلمات سر (OTP + Apple + Google + JWT).

    📌 الاستثناء الوحيد (قرار PO — 2026-09-25): دخول الإدارة بكلمة سر ثم رمز
       SMS، تحت /admin/auth و /admin/admins وحدهما.
    """
    from config.urls import api

    admin_prefixes = ("/admin/auth", "/admin/admins")
    paths = []
    for prefix, router in api._routers:
        for p in router.path_operations:
            paths.append(f"{prefix.rstrip('/')}{p}")

    customer_paths = [p for p in paths if not p.startswith(admin_prefixes)]
    for banned in ("password", "login/password", "token/pair"):
        assert not any(banned in p for p in customer_paths), f"unexpected endpoint containing {banned}"
    assert not any("token/pair" in p for p in paths)


@pytest.mark.django_db
def test_otp_internals_never_appear_in_responses(client):
    post(client, "/api/auth/otp/request", {"phone": PHONE})
    r = post(client, "/api/auth/otp/verify", {"phone": PHONE, "code": last_code()})
    text = r.content.decode()

    for leaked in ("code_hash", "attempts_count", "max_attempts", "expires_at"):
        assert leaked not in text


@pytest.mark.django_db
def test_health_endpoint_still_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
