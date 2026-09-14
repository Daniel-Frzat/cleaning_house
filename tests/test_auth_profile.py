"""
Profile Tests — Identity Domain

يغطي: كشف full_name/email في /auth/me وفي رد الدخول، والتعديل الذاتي
عبر PATCH /auth/me، وحدوده الأمنية.

🔒 محور الاختبار الأمني: المستخدم لا يستطيع تعديل phone ولا role ولا
   status ولا أي حقل امتياز — لا صراحةً ولا تهريبًا عبر جسم الطلب.
"""

import json

import pytest
from django.test import Client

from apps.accounts.models import User, UserStatus
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services import identity as identity_svc
from apps.accounts.services.tokens import issue_tokens_for_user


@pytest.fixture
def client():
    return Client()


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


def patch(client, url, payload, **extra):
    return client.patch(
        url, data=json.dumps(payload), content_type="application/json", **extra
    )


@pytest.fixture
def customer(db):
    return User.objects.create_user(phone="+61400330001", role=ConfirmedRole.CUSTOMER)


@pytest.fixture
def contractor(db):
    return User.objects.create_user(
        phone="+61400330002", role=ConfirmedRole.CONTRACTOR
    )


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(phone="+61400330003", role=ConfirmedRole.ADMIN)


# ============================================================
# 1) القراءة — الحقول الجديدة مكشوفة
# ============================================================
@pytest.mark.django_db
def test_me_returns_full_name_and_email(client, customer):
    customer.full_name = "Grace Hopper"
    customer.email = "grace@example.com"
    customer.save()

    body = client.get("/api/auth/me", **auth(customer)).json()

    assert body["full_name"] == "Grace Hopper"
    assert body["email"] == "grace@example.com"


@pytest.mark.django_db
def test_me_returns_empty_name_and_null_email_when_unset(client, customer):
    """📌 الافتراضي: اسم فارغ وبريد None — لا انفجار ولا مفاتيح ناقصة."""
    body = client.get("/api/auth/me", **auth(customer)).json()

    assert body["full_name"] == ""
    assert body["email"] is None


@pytest.mark.django_db
def test_me_exposes_exactly_six_fields(client, customer):
    """🔒 لا تسريب حقول امتياز أو حساسة."""
    body = client.get("/api/auth/me", **auth(customer)).json()

    assert set(body) == {"id", "phone", "role", "status", "full_name", "email"}
    for forbidden in ("password", "is_staff", "is_superuser", "last_login",
                      "groups", "user_permissions", "date_joined"):
        assert forbidden not in body


@pytest.mark.django_db
def test_login_response_carries_the_same_user_shape(client, customer):
    """
    رد الدخول و/auth/me يستخدمان المُسلسِل نفسه — لا انحراف بينهما.
    """
    from apps.accounts.services import otp as otp_svc
    import apps.accounts.services.otp as otp_module

    customer.full_name = "Ada"
    customer.save()

    real = otp_module.generate_code
    captured = {}

    def spy(n):
        code = real(n)
        captured["code"] = code
        return code

    otp_module.generate_code = spy
    otp_svc.generate_and_send(customer.phone)
    otp_module.generate_code = real

    r = client.post(
        "/api/auth/otp/verify",
        data=json.dumps({"phone": customer.phone, "code": captured["code"]}),
        content_type="application/json",
    )

    user_block = r.json()["user"]
    me_block = client.get("/api/auth/me", **auth(customer)).json()

    assert set(user_block) == set(me_block)
    assert user_block["full_name"] == "Ada"


# ============================================================
# 2) التعديل الذاتي
# ============================================================
@pytest.mark.django_db
def test_customer_updates_own_name(client, customer):
    r = patch(client, "/api/auth/me", {"full_name": "Grace Hopper"}, **auth(customer))

    assert r.status_code == 200, r.content
    assert r.json()["full_name"] == "Grace Hopper"

    customer.refresh_from_db()
    assert customer.full_name == "Grace Hopper"


@pytest.mark.django_db
def test_customer_updates_own_email(client, customer):
    r = patch(client, "/api/auth/me", {"email": "grace@example.com"}, **auth(customer))

    assert r.status_code == 200, r.content
    customer.refresh_from_db()
    assert customer.email == "grace@example.com"


@pytest.mark.django_db
def test_partial_update_leaves_the_other_field_untouched(client, customer):
    customer.full_name = "Original"
    customer.email = "original@example.com"
    customer.save()

    patch(client, "/api/auth/me", {"full_name": "Changed"}, **auth(customer))

    customer.refresh_from_db()
    assert customer.full_name == "Changed"
    assert customer.email == "original@example.com"


@pytest.mark.django_db
def test_empty_email_clears_it_as_null_not_empty_string(client, customer):
    """
    🔒 الفراغ يُخزَّن NULL لا "": حقل البريد فريد، وحسابان بقيمة ""
       كانا سيتعارضان.
    """
    customer.email = "grace@example.com"
    customer.save()

    r = patch(client, "/api/auth/me", {"email": ""}, **auth(customer))

    assert r.status_code == 200, r.content
    assert r.json()["email"] is None

    customer.refresh_from_db()
    assert customer.email is None


@pytest.mark.django_db
def test_two_accounts_can_both_have_no_email(client, customer, contractor):
    """النتيجة العملية لقاعدة NULL أعلاه."""
    for user in (customer, contractor):
        assert patch(client, "/api/auth/me", {"email": ""}, **auth(user)).status_code == 200

    assert User.objects.filter(email__isnull=True).count() >= 2


@pytest.mark.django_db
def test_name_is_trimmed(client, customer):
    patch(client, "/api/auth/me", {"full_name": "  Grace  "}, **auth(customer))

    customer.refresh_from_db()
    assert customer.full_name == "Grace"


@pytest.mark.django_db
def test_empty_body_is_accepted_and_changes_nothing(client, customer):
    customer.full_name = "Original"
    customer.save()

    r = patch(client, "/api/auth/me", {}, **auth(customer))

    assert r.status_code == 200
    customer.refresh_from_db()
    assert customer.full_name == "Original"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role", [ConfirmedRole.CUSTOMER, ConfirmedRole.CONTRACTOR, ConfirmedRole.ADMIN]
)
def test_every_role_may_update_their_own_profile(client, db, role):
    """⚠️ لا حظر أدوار: الملف الشخصي يخص صاحبه أيًّا كان دوره."""
    user = User.objects.create_user(phone=f"+6140033{role[:4]}", role=role)

    r = patch(client, "/api/auth/me", {"full_name": "Someone"}, **auth(user))

    assert r.status_code == 200, r.content


# ============================================================
# 3) 🔒 الحدود الأمنية
# ============================================================
@pytest.mark.django_db
def test_role_cannot_be_escalated(client, customer):
    """🔒 الأهم: لا تصعيد صلاحيات عبر جسم الطلب."""
    r = patch(
        client, "/api/auth/me",
        {"full_name": "x", "role": "ADMIN"},
        **auth(customer),
    )

    customer.refresh_from_db()
    assert customer.role == ConfirmedRole.CUSTOMER
    # الحقل غير معرَّف في الـschema، فيُهمَل قبل أن يصل الخدمة أصلًا
    assert r.json()["role"] == ConfirmedRole.CUSTOMER


@pytest.mark.django_db
def test_status_cannot_be_changed(client, customer):
    patch(client, "/api/auth/me", {"status": "SUSPENDED"}, **auth(customer))

    customer.refresh_from_db()
    assert customer.status == UserStatus.ACTIVE


@pytest.mark.django_db
def test_phone_cannot_be_changed(client, customer):
    """🔒 الهاتف معرّف الدخول — تغييره يحتاج تحقق OTP للرقم الجديد."""
    original = customer.phone

    patch(client, "/api/auth/me", {"phone": "+61499999999"}, **auth(customer))

    customer.refresh_from_db()
    assert customer.phone == original


@pytest.mark.django_db
def test_staff_flags_cannot_be_set(client, customer):
    patch(
        client, "/api/auth/me",
        {"is_staff": True, "is_superuser": True, "is_active": False},
        **auth(customer),
    )

    customer.refresh_from_db()
    assert customer.is_staff is False
    assert customer.is_superuser is False
    assert customer.is_active is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field,value",
    [
        ("role", ConfirmedRole.ADMIN),
        ("status", UserStatus.SUSPENDED),
        ("phone", "+61499999999"),
        ("is_staff", True),
        ("is_superuser", True),
        ("is_active", False),
        ("password", "hunter2"),
    ],
)
def test_service_layer_rejects_every_forbidden_field(customer, field, value):
    """
    🔒 خط الدفاع الحقيقي.

    اختبارات الـAPI أعلاه لا تكفي وحدها: الـschema يُسقط الحقول المجهولة
    صامتًا، فتمرّ حتى لو كانت الخدمة نفسها مثقوبة. هذا الاختبار يضرب
    الخدمة مباشرةً، فيمسك توسيع القائمة البيضاء.
    """
    with pytest.raises(identity_svc.ProfileUpdateError):
        identity_svc.update_own_profile(customer, **{field: value})


@pytest.mark.django_db
def test_whitelist_contains_exactly_two_fields():
    """
    أي حقل يُضاف للقائمة البيضاء يكسر هذا الاختبار عمدًا — قرار واعٍ
    مطلوب، لأن كل إضافة هنا سطح هجوم جديد.
    """
    assert identity_svc.UPDATABLE_PROFILE_FIELDS == {"full_name", "email"}


@pytest.mark.django_db
def test_cannot_update_another_users_profile(client, customer, contractor):
    """🔒 لا معرّف يُقبل من العميل — المسار ذاتي بالكامل."""
    patch(client, "/api/auth/me", {"full_name": "Hacked"}, **auth(customer))

    contractor.refresh_from_db()
    assert contractor.full_name == ""


@pytest.mark.django_db
def test_unauthenticated_update_is_rejected(client, customer):
    assert patch(client, "/api/auth/me", {"full_name": "x"}).status_code == 401
    assert client.get("/api/auth/me").status_code == 401


# ============================================================
# 4) تفرّد البريد
# ============================================================
@pytest.mark.django_db
def test_duplicate_email_is_rejected_with_409(client, customer, contractor):
    contractor.email = "taken@example.com"
    contractor.save()

    r = patch(client, "/api/auth/me", {"email": "taken@example.com"}, **auth(customer))

    assert r.status_code == 409, r.content
    assert r.json()["code"] == "email_already_used"

    customer.refresh_from_db()
    assert customer.email is None


@pytest.mark.django_db
def test_duplicate_email_check_is_case_insensitive(client, customer, contractor):
    contractor.email = "taken@example.com"
    contractor.save()

    r = patch(client, "/api/auth/me", {"email": "TAKEN@example.com"}, **auth(customer))

    assert r.status_code == 409, r.content


@pytest.mark.django_db
def test_keeping_your_own_email_is_not_a_conflict(client, customer):
    """إعادة إرسال البريد نفسه لا تعتبره مستخدَمًا من غيرك."""
    customer.email = "mine@example.com"
    customer.save()

    r = patch(client, "/api/auth/me", {"email": "mine@example.com"}, **auth(customer))

    assert r.status_code == 200, r.content


@pytest.mark.django_db
def test_malformed_email_is_rejected(client, customer):
    r = patch(client, "/api/auth/me", {"email": "not-an-email"}, **auth(customer))

    assert r.status_code == 422, r.content
    customer.refresh_from_db()
    assert customer.email is None
