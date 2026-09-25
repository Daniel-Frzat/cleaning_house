"""
مصادقة الإدارة — بريد/هاتف + كلمة سر + رمز SMS، أجهزة موثوقة، قفل،
تغيير كلمة السر، إدارة حسابات الأدمن (Superuser)، ولوحة Django بخطوتين.
"""

import json

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import AdminLoginChallenge, TrustedDevice, User, UserStatus
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.audit.models import AuditLog
from tests.test_auth_hardening import CapturingSMSAdapter

PW = "Correct-Horse-Battery-9"
NEW_PW = "Another-Strong-Pass-7"


@pytest.fixture(autouse=True)
def _sms(settings):
    settings.SMS_ADAPTER = "tests.test_auth_hardening.CapturingSMSAdapter"
    settings.OTP_RESEND_COOLDOWN_SECONDS = 0
    settings.OTP_TEST_NUMBERS = {}
    # كلمات سر الاختبار تُضبط مباشرة، والتحقق من السياسة مختبَر صراحةً أدناه
    CapturingSMSAdapter.sent = []
    yield
    CapturingSMSAdapter.sent = []


def make_admin(phone="+61400800001", email="ops@example.com", superuser=False, password=PW):
    user = User.objects.create_user(phone=phone, email=email, role=ConfirmedRole.ADMIN, password=password)
    user.is_superuser = superuser
    user.save()
    return user


def post(client, url, payload=None, **extra):
    return client.post(url, data=json.dumps(payload or {}), content_type="application/json", **extra)


def bearer(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def last_code():
    return CapturingSMSAdapter.sent[-1]["code"]


def full_login(client, identifier="ops@example.com", password=PW, remember=False):
    r = post(client, "/api/admin/auth/login", {"identifier": identifier, "password": password})
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["state"] == "otp_required"
    r = post(client, "/api/admin/auth/verify",
             {"challenge_id": body["challenge_id"], "code": last_code(), "remember_device": remember})
    assert r.status_code == 200, r.content
    return r.json()


@pytest.fixture
def client():
    return Client()


# ============================================================
# 1) الدخول بعاملين
# ============================================================
@pytest.mark.django_db
def test_password_then_sms_code_issues_admin_tokens(client):
    admin = make_admin()
    r = post(client, "/api/admin/auth/login", {"identifier": "OPS@example.com", "password": PW})
    body = r.json()
    assert r.status_code == 200
    assert body["state"] == "otp_required"
    assert body["tokens"] is None
    assert body["phone_hint"].startswith("+614") and body["phone_hint"].endswith("001")
    assert CapturingSMSAdapter.sent[-1]["phone_number"] == admin.phone

    r = post(client, "/api/admin/auth/verify", {"challenge_id": body["challenge_id"], "code": last_code()})
    assert r.status_code == 200
    access = r.json()["tokens"]["access"]
    me = client.get("/api/admin/auth/me", **bearer(access)).json()
    assert me["email"] == "ops@example.com"
    assert client.get("/api/admin/services", **bearer(access)).status_code == 200


@pytest.mark.django_db
def test_phone_works_as_identifier(client):
    make_admin()
    full_login(client, identifier="+61 400 800 001")


@pytest.mark.django_db
@pytest.mark.parametrize("identifier,password", [
    ("ops@example.com", "wrong-password"),
    ("nobody@example.com", PW),
])
def test_wrong_password_and_unknown_account_look_identical(client, identifier, password):
    make_admin()
    r = post(client, "/api/admin/auth/login", {"identifier": identifier, "password": password})
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"
    assert CapturingSMSAdapter.sent == []


@pytest.mark.django_db
def test_non_admin_accounts_cannot_use_admin_login(client):
    User.objects.create_user(phone="+61400800009", email="cust@example.com", password=PW)
    r = post(client, "/api/admin/auth/login", {"identifier": "cust@example.com", "password": PW})
    assert r.status_code == 401


@pytest.mark.django_db
def test_wrong_sms_code_is_rejected_and_challenge_is_single_use(client):
    make_admin()
    body = post(client, "/api/admin/auth/login", {"identifier": "ops@example.com", "password": PW}).json()
    r = post(client, "/api/admin/auth/verify", {"challenge_id": body["challenge_id"], "code": "000000"})
    assert r.status_code == 400 and r.json()["code"] == "otp_invalid_code"

    code = last_code()
    assert post(client, "/api/admin/auth/verify", {"challenge_id": body["challenge_id"], "code": code}).status_code == 200
    r = post(client, "/api/admin/auth/verify", {"challenge_id": body["challenge_id"], "code": code})
    assert r.status_code == 400 and r.json()["code"] == "challenge_invalid"


@pytest.mark.django_db
def test_lockout_after_repeated_wrong_passwords(client, settings):
    settings.ADMIN_MAX_FAILED_LOGINS = 3
    make_admin()
    for _ in range(3):
        post(client, "/api/admin/auth/login", {"identifier": "ops@example.com", "password": "nope"})
    r = post(client, "/api/admin/auth/login", {"identifier": "ops@example.com", "password": PW})
    assert r.status_code == 429
    assert r.json()["code"] == "account_locked"
    assert r.json()["retry_after_seconds"] > 0


@pytest.mark.django_db
def test_suspended_admin_cannot_log_in(client):
    admin = make_admin()
    admin.status = UserStatus.SUSPENDED
    admin.save()
    r = post(client, "/api/admin/auth/login", {"identifier": "ops@example.com", "password": PW})
    assert r.status_code == 403 and r.json()["code"] == "account_inactive"


@pytest.mark.django_db
def test_admin_without_valid_phone_gets_clear_error(client):
    make_admin(phone="social:google:abc123")
    r = post(client, "/api/admin/auth/login", {"identifier": "ops@example.com", "password": PW})
    assert r.status_code == 409 and r.json()["code"] == "second_factor_unavailable"


@pytest.mark.django_db
def test_resend_sends_a_new_code(client):
    make_admin()
    body = post(client, "/api/admin/auth/login", {"identifier": "ops@example.com", "password": PW}).json()
    assert post(client, "/api/admin/auth/resend", {"challenge_id": body["challenge_id"]}).status_code == 200
    assert len(CapturingSMSAdapter.sent) == 2


@pytest.mark.django_db
def test_otp_test_numbers_never_bypass_the_admin_second_factor(client, settings, monkeypatch):
    from apps.accounts.services import otp as otp_service

    monkeypatch.setattr(otp_service, "generate_code", lambda length=None: "999999")
    make_admin()
    settings.OTP_TEST_NUMBERS = {"+61400800001": "123456"}
    body = post(client, "/api/admin/auth/login", {"identifier": "ops@example.com", "password": PW}).json()
    assert CapturingSMSAdapter.sent[-1]["code"] == "999999", "a real, random code must be sent"
    r = post(client, "/api/admin/auth/verify", {"challenge_id": body["challenge_id"], "code": "123456"})
    assert r.status_code == 400


# ============================================================
# 2) الجهاز الموثوق
# ============================================================
@pytest.mark.django_db
def test_trusted_device_skips_sms_but_still_needs_password(client):
    make_admin()
    device_token = full_login(client, remember=True)["device_token"]
    assert device_token
    CapturingSMSAdapter.sent = []

    r = post(client, "/api/admin/auth/login",
             {"identifier": "ops@example.com", "password": PW, "device_token": device_token})
    assert r.status_code == 200 and r.json()["state"] == "authenticated"
    assert r.json()["tokens"]["access"]
    assert CapturingSMSAdapter.sent == []

    r = post(client, "/api/admin/auth/login",
             {"identifier": "ops@example.com", "password": "wrong", "device_token": device_token})
    assert r.status_code == 401


@pytest.mark.django_db
def test_device_token_of_another_admin_is_ignored(client):
    make_admin()
    make_admin(phone="+61400800002", email="other@example.com")
    token = full_login(client, identifier="other@example.com", remember=True)["device_token"]
    r = post(client, "/api/admin/auth/login",
             {"identifier": "ops@example.com", "password": PW, "device_token": token})
    assert r.json()["state"] == "otp_required"


@pytest.mark.django_db
def test_expired_or_revoked_device_requires_sms_again(client):
    admin = make_admin()
    token = full_login(client, remember=True)["device_token"]
    TrustedDevice.objects.filter(user=admin).update(expires_at=timezone.now())
    r = post(client, "/api/admin/auth/login",
             {"identifier": "ops@example.com", "password": PW, "device_token": token})
    assert r.json()["state"] == "otp_required"


@pytest.mark.django_db
def test_admin_lists_and_revokes_own_devices(client):
    make_admin()
    body = full_login(client, remember=True)
    access = body["tokens"]["access"]
    devices = client.get("/api/admin/auth/devices", **bearer(access)).json()
    assert len(devices) == 1
    assert client.delete(f"/api/admin/auth/devices/{devices[0]['id']}", **bearer(access)).status_code == 204
    assert client.get("/api/admin/auth/devices", **bearer(access)).json() == []


# ============================================================
# 3) تغيير كلمة السر
# ============================================================
@pytest.mark.django_db
def test_change_password_revokes_other_sessions_and_devices(client):
    admin = make_admin()
    first = full_login(client, remember=True)
    second = full_login(client)
    r = post(client, "/api/admin/auth/password",
             {"current_password": PW, "new_password": NEW_PW}, **bearer(second["tokens"]["access"]))
    assert r.status_code == 200
    admin.refresh_from_db()
    assert admin.check_password(NEW_PW)
    assert admin.password_changed_at is not None
    # الجلسة الأخرى لا تتجدد، والجهاز الموثوق أُلغي
    assert post(client, "/api/auth/token/refresh", {"refresh": first["tokens"]["refresh"]}).status_code == 401
    assert not TrustedDevice.objects.filter(user=admin, revoked_at__isnull=True).exists()
    assert AuditLog.objects.filter(action="admin_account.password_change").exists()


@pytest.mark.django_db
def test_change_password_requires_current_password_and_policy(client):
    make_admin()
    access = full_login(client)["tokens"]["access"]
    r = post(client, "/api/admin/auth/password",
             {"current_password": "wrong", "new_password": NEW_PW}, **bearer(access))
    assert r.status_code == 400 and r.json()["code"] == "wrong_password"
    r = post(client, "/api/admin/auth/password",
             {"current_password": PW, "new_password": "short"}, **bearer(access))
    assert r.status_code == 422 and r.json()["code"] == "password_invalid"
    r = post(client, "/api/admin/auth/password",
             {"current_password": PW, "new_password": "password123456"}, **bearer(access))
    assert r.status_code == 422


# ============================================================
# 4) مسارات العملاء مغلقة أمام الأدمن
# ============================================================
@pytest.mark.django_db
def test_admin_cannot_log_in_with_sms_alone(client):
    make_admin()
    post(client, "/api/auth/otp/request", {"phone": "+61400800001"})
    r = post(client, "/api/auth/otp/verify", {"phone": "+61400800001", "code": last_code()})
    assert r.status_code == 403
    assert r.json()["code"] == "admin_login_required"


@pytest.mark.django_db
def test_non_admin_token_is_refused_on_admin_routes(client):
    customer = User.objects.create_user(phone="+61400800010")
    access = issue_tokens_for_user(customer)["access"]
    r = client.get("/api/admin/auth/me", **bearer(access))
    assert r.status_code == 403 and r.json()["code"] == "admin_required"


# ============================================================
# 5) إدارة حسابات الأدمن — Superuser
# ============================================================
@pytest.fixture
def superuser_token(client):
    make_admin(phone="+61400800100", email="root@example.com", superuser=True)
    return full_login(client, identifier="root@example.com")["tokens"]["access"]


@pytest.mark.django_db
def test_superuser_creates_admin_who_must_change_password(client, superuser_token):
    r = post(client, "/api/admin/admins",
             {"email": "new@example.com", "phone": "+61400800200", "full_name": "New Admin"},
             **bearer(superuser_token))
    assert r.status_code == 201, r.content
    body = r.json()
    temporary = body["temporary_password"]
    assert body["admin"]["must_change_password"] is True
    new = User.objects.get(email="new@example.com")
    assert new.has_admin_access() and new.is_staff and not new.is_superuser

    other = Client()
    tokens = full_login(other, identifier="new@example.com", password=temporary)
    access = tokens["tokens"]["access"]
    assert tokens["must_change_password"] is True
    r = other.get("/api/admin/services", **bearer(access))
    assert r.status_code == 403 and r.json()["code"] == "password_change_required"
    assert other.get("/api/admin/auth/me", **bearer(access)).status_code == 200

    fresh = post(other, "/api/admin/auth/password",
                 {"current_password": temporary, "new_password": NEW_PW}, **bearer(access)).json()
    assert other.get("/api/admin/services", **bearer(fresh["access"])).status_code == 200
    assert AuditLog.objects.filter(action="admin_account.create").exists()


@pytest.mark.django_db
def test_plain_admin_cannot_manage_admins(client):
    make_admin()
    access = full_login(client)["tokens"]["access"]
    r = client.get("/api/admin/admins", **bearer(access))
    assert r.status_code == 403 and r.json()["code"] == "superuser_required"


@pytest.mark.django_db
def test_duplicate_email_or_phone_is_refused(client, superuser_token):
    make_admin()
    r = post(client, "/api/admin/admins", {"email": "ops@example.com", "phone": "+61400800300"},
             **bearer(superuser_token))
    assert r.status_code == 409
    r = post(client, "/api/admin/admins", {"email": "x@example.com", "phone": "+61400800001"},
             **bearer(superuser_token))
    assert r.status_code == 409


@pytest.mark.django_db
def test_superuser_resets_password_and_signs_admin_out(client, superuser_token):
    admin = make_admin()
    their = full_login(Client())
    r = post(client, f"/api/admin/admins/{admin.id}/reset-password", **bearer(superuser_token))
    assert r.status_code == 200
    temporary = r.json()["temporary_password"]
    admin.refresh_from_db()
    assert admin.must_change_password and admin.check_password(temporary)
    assert post(client, "/api/auth/token/refresh", {"refresh": their["tokens"]["refresh"]}).status_code == 401
    assert AuditLog.objects.filter(action="admin_account.password_reset", target_id=str(admin.id)).exists()


@pytest.mark.django_db
def test_superuser_suspends_admin_and_cannot_lock_themselves_out(client, superuser_token):
    admin = make_admin()
    root = User.objects.get(email="root@example.com")
    r = client.patch(f"/api/admin/admins/{admin.id}", data={"status": "SUSPENDED"},
                     content_type="application/json", **bearer(superuser_token))
    assert r.status_code == 200 and r.json()["status"] == "SUSPENDED"

    r = client.patch(f"/api/admin/admins/{root.id}", data={"status": "SUSPENDED"},
                     content_type="application/json", **bearer(superuser_token))
    assert r.status_code == 409
    r = client.patch(f"/api/admin/admins/{root.id}", data={"is_superuser": False},
                     content_type="application/json", **bearer(superuser_token))
    assert r.status_code == 409


@pytest.mark.django_db
def test_list_and_retrieve_admins(client, superuser_token):
    admin = make_admin()
    body = client.get("/api/admin/admins", **bearer(superuser_token)).json()
    assert body["count"] == 2
    assert client.get(f"/api/admin/admins/{admin.id}", **bearer(superuser_token)).status_code == 200
    customer = User.objects.create_user(phone="+61400800011")
    assert client.get(f"/api/admin/admins/{customer.id}", **bearer(superuser_token)).status_code == 404


# ============================================================
# 6) الدور مصدر حقيقة واحد
# ============================================================
@pytest.mark.django_db
def test_role_drives_staff_and_superuser_flags():
    user = User.objects.create_user(phone="+61400800020")
    user.is_staff = user.is_superuser = True
    user.save()
    user.refresh_from_db()
    assert (user.is_staff, user.is_superuser) == (False, False)

    user.role = ConfirmedRole.ADMIN
    user.save()
    user.refresh_from_db()
    assert user.is_staff is True


# ============================================================
# 7) لوحة Django — خطوتان
# ============================================================
@pytest.mark.django_db
def test_django_admin_two_step_login_and_trusted_cookie():
    make_admin()
    c = Client()
    r = c.post("/admin/login/", {"identifier": "ops@example.com", "password": PW})
    assert r.status_code == 302 and r["Location"].endswith("/admin/login/verify/")
    assert c.get("/admin/").status_code == 302  # لم يدخل بعد

    r = c.post("/admin/login/verify/", {"code": last_code(), "remember_device": "on"})
    assert r.status_code == 302
    assert c.get("/admin/").status_code == 200
    assert "ch_admin_device" in r.cookies

    # جلسة جديدة بالكوكي نفسه: بلا رمز
    c2 = Client()
    c2.cookies["ch_admin_device"] = r.cookies["ch_admin_device"].value
    CapturingSMSAdapter.sent = []
    r = c2.post("/admin/login/", {"identifier": "ops@example.com", "password": PW})
    assert r.status_code == 302 and CapturingSMSAdapter.sent == []
    assert c2.get("/admin/").status_code == 200


@pytest.mark.django_db
def test_django_admin_rejects_wrong_password_and_customers():
    make_admin()
    User.objects.create_user(phone="+61400800030", email="c@example.com", password=PW)
    c = Client()
    r = c.post("/admin/login/", {"identifier": "ops@example.com", "password": "bad"})
    assert r.status_code == 200 and b"Invalid email or password" in r.content
    r = c.post("/admin/login/", {"identifier": "c@example.com", "password": PW})
    assert r.status_code == 200 and b"Invalid email or password" in r.content


@pytest.mark.django_db
def test_django_admin_forces_password_change():
    admin = make_admin()
    admin.must_change_password = True
    admin.save()
    c = Client()
    c.force_login(admin)
    r = c.get("/admin/")
    assert r.status_code == 302 and "password_change" in r["Location"]
    r = c.post("/admin/password_change/",
               {"old_password": PW, "new_password1": NEW_PW, "new_password2": NEW_PW})
    assert r.status_code == 302
    admin.refresh_from_db()
    assert admin.must_change_password is False
    assert c.get("/admin/").status_code == 200


@pytest.mark.django_db
def test_suspended_admin_loses_panel_access():
    admin = make_admin()
    c = Client()
    c.force_login(admin)
    admin.status = UserStatus.SUSPENDED
    admin.save()
    assert c.get("/admin/").status_code == 302


@pytest.mark.django_db
def test_challenge_rows_are_created_per_login(client):
    make_admin()
    post(client, "/api/admin/auth/login", {"identifier": "ops@example.com", "password": PW})
    assert AdminLoginChallenge.objects.count() == 1


# ============================================================
# 8) وضع التجريب قبل مزوّد SMS
# ============================================================
@pytest.mark.django_db
def test_test_numbers_can_serve_the_admin_second_factor_when_explicitly_allowed(client, settings):
    make_admin()
    settings.OTP_TEST_NUMBERS = {"+61400800001": "123456"}
    settings.OTP_TEST_NUMBERS_ALLOW_ADMIN = True
    body = post(client, "/api/admin/auth/login", {"identifier": "ops@example.com", "password": PW}).json()
    assert CapturingSMSAdapter.sent == []  # لا SMS
    r = post(client, "/api/admin/auth/verify", {"challenge_id": body["challenge_id"], "code": "123456"})
    assert r.status_code == 200
    # كلمة السر ما زالت مطلوبة
    assert post(client, "/api/admin/auth/login", {"identifier": "ops@example.com", "password": "bad"}).status_code == 401


@pytest.mark.django_db
def test_test_mode_expires_automatically(client, settings):
    from apps.accounts.services import otp as otp_service

    settings.OTP_TEST_NUMBERS = {"+61400800050": "123456"}
    settings.OTP_TEST_MODE_UNTIL = "2020-01-01"
    assert otp_service.test_numbers() == {}
    settings.OTP_TEST_MODE_UNTIL = "not-a-date"
    assert otp_service.test_numbers() == {}  # تاريخ غير صالح = منتهٍ
    settings.OTP_TEST_MODE_UNTIL = "2999-01-01"
    assert otp_service.test_numbers() == {"+61400800050": "123456"}


def test_deploy_warning_while_test_mode_is_on(settings):
    from apps.accounts.apps import otp_test_mode_check

    settings.DEBUG = False
    settings.OTP_TEST_NUMBERS = {"+61400800050": "123456"}
    settings.OTP_TEST_NUMBERS_ALLOW_ADMIN = True
    settings.OTP_TEST_MODE_UNTIL = ""
    [warning] = otp_test_mode_check()
    assert warning.id == "accounts.W001" and "admin" in warning.msg
    settings.OTP_TEST_NUMBERS = {}
    assert otp_test_mode_check() == []
