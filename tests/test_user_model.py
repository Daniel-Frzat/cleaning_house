"""
Identity Domain — User Model Tests (Phase 1 — Step 1)

يغطي:
  - إنشاء مستخدم لكل دور مؤكد (CUSTOMER / CONTRACTOR / ADMIN)
  - القيم الافتراضية (status / is_active / role)
  - __str__
  - phone كمعرّف أساسي (لا يوجد username)
  - createsuperuser عبر الـManager
  - قيود التفرد (phone / email)
"""

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from apps.accounts.models import UserStatus
from apps.accounts.roles import ConfirmedRole

User = get_user_model()


# ------------------------------------------------------------
# الأدوار الثلاثة المؤكدة
# ------------------------------------------------------------
@pytest.mark.django_db
def test_create_one_user_per_confirmed_role():
    """ينشئ مستخدمًا واحدًا لكل دور ويتحقق من role / status / str()."""
    specs = [
        ("+96550000001", ConfirmedRole.CUSTOMER, "Sara Customer"),
        ("+96550000002", ConfirmedRole.CONTRACTOR, "Omar Contractor"),
        ("+96550000003", ConfirmedRole.ADMIN, "Admin User"),
    ]

    created = []
    for phone, role, full_name in specs:
        user = User.objects.create_user(phone=phone, full_name=full_name, role=role)
        created.append(user)

        # الدور مطابق لما طُلب
        assert user.role == role
        # الحالة الافتراضية ACTIVE
        assert user.status == UserStatus.ACTIVE
        assert user.is_active is True
        # المفتاح الأساسي UUID
        assert isinstance(user.pk, uuid.UUID)
        # __str__ يعمل ويحوي الهاتف والدور
        assert str(user) == f"{phone} ({user.get_role_display()})"

    assert User.objects.count() == 3
    assert {u.role for u in created} == {
        ConfirmedRole.CUSTOMER,
        ConfirmedRole.CONTRACTOR,
        ConfirmedRole.ADMIN,
    }


@pytest.mark.django_db
def test_role_enum_has_only_three_confirmed_roles():
    """PropertyManager = CUSTOMER (Change Set قسم 12) — لا يوجد دور رابع."""
    assert set(ConfirmedRole.values) == {"CUSTOMER", "CONTRACTOR", "ADMIN"}
    assert not hasattr(ConfirmedRole, "PROPERTY_MANAGER")


# ------------------------------------------------------------
# القيم الافتراضية
# ------------------------------------------------------------
@pytest.mark.django_db
def test_default_role_and_status():
    user = User.objects.create_user(phone="+96550000010")
    assert user.role == ConfirmedRole.CUSTOMER
    assert user.status == UserStatus.ACTIVE
    assert user.is_active is True
    assert user.is_staff is False
    assert user.is_superuser is False
    assert user.date_joined is not None
    assert user.updated_at is not None


@pytest.mark.django_db
def test_status_choices():
    assert set(UserStatus.values) == {"ACTIVE", "INACTIVE", "SUSPENDED"}
    user = User.objects.create_user(phone="+96550000011", status=UserStatus.SUSPENDED)
    assert user.status == UserStatus.SUSPENDED


# ------------------------------------------------------------
# phone كمعرّف أساسي
# ------------------------------------------------------------
@pytest.mark.django_db
def test_phone_is_the_username_field():
    assert User.USERNAME_FIELD == "phone"
    assert "username" not in [f.name for f in User._meta.get_fields()]

    user = User.objects.create_user(phone="+96550000020")
    assert user.get_username() == "+96550000020"


@pytest.mark.django_db
def test_create_user_requires_phone():
    with pytest.raises(ValueError):
        User.objects.create_user(phone="")


@pytest.mark.django_db
def test_user_without_password_has_unusable_password():
    """حسابات OTP تُنشأ بلا كلمة مرور."""
    user = User.objects.create_user(phone="+96550000021")
    assert user.has_usable_password() is False


# ------------------------------------------------------------
# Superuser
# ------------------------------------------------------------
@pytest.mark.django_db
def test_create_superuser():
    admin = User.objects.create_superuser(phone="+96550000030", password="StrongPass123!")
    assert admin.is_staff is True
    assert admin.is_superuser is True
    assert admin.is_active is True
    assert admin.role == ConfirmedRole.ADMIN
    assert admin.status == UserStatus.ACTIVE
    assert admin.check_password("StrongPass123!") is True


@pytest.mark.django_db
def test_create_superuser_rejects_wrong_flags():
    with pytest.raises(ValueError):
        User.objects.create_superuser(phone="+96550000031", password="x", is_staff=False)
    with pytest.raises(ValueError):
        User.objects.create_superuser(phone="+96550000032", password="x", is_superuser=False)


# ------------------------------------------------------------
# التفرد
# ------------------------------------------------------------
@pytest.mark.django_db
def test_phone_is_unique():
    User.objects.create_user(phone="+96550000040")
    with pytest.raises(Exception):
        with transaction.atomic():
            User.objects.create_user(phone="+96550000040")


@pytest.mark.django_db
def test_email_is_optional_and_stored_as_null_when_blank():
    """عدة مستخدمين بلا بريد يجب ألا يتعارضوا مع قيد unique."""
    u1 = User.objects.create_user(phone="+96550000050")
    u2 = User.objects.create_user(phone="+96550000051")
    assert u1.email is None
    assert u2.email is None


@pytest.mark.django_db
def test_email_is_unique_when_present():
    User.objects.create_user(phone="+96550000060", email="dup@example.com")
    with pytest.raises(Exception):
        with transaction.atomic():
            User.objects.create_user(phone="+96550000061", email="dup@example.com")


@pytest.mark.django_db
def test_full_name_helpers():
    user = User.objects.create_user(phone="+96550000070", full_name="Sara Al Ali")
    assert user.get_full_name() == "Sara Al Ali"
    assert user.get_short_name() == "Sara"
