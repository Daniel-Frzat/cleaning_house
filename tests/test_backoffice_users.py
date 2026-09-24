"""
Back-office — المستخدمون: القائمة والمرشِّحات والتفاصيل والإيقاف وإعادة التفعيل.
"""

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import SocialAccount, SocialProvider, UserStatus
from apps.accounts.roles import ConfirmedRole
from apps.audit.models import AuditLog
from apps.contractors.models import AvailabilityStatus
from tests.backoffice import (
    auth,
    make_admin,
    make_booking,
    make_contractor,
    make_property,
    make_superuser,
    make_user,
    tokens,
)


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin(db):
    return make_admin(email="ops@example.com")


def post(client, url, body=None, **headers):
    return client.post(
        url, data=json.dumps(body or {}), content_type="application/json", **headers
    )


# ------------------------------------------------------------
# القائمة
# ------------------------------------------------------------
@pytest.mark.django_db
def test_list_users_shape_and_order(client, admin):
    older = make_user(full_name="Old Timer")
    older.date_joined = timezone.now() - timedelta(days=3)
    older.save()
    newer = make_user(full_name="New Comer")

    r = client.get("/api/admin/users", **auth(admin))

    assert r.status_code == 200, r.content
    body = r.json()
    assert body["count"] == 3  # + الأدمن نفسه
    ids = [u["id"] for u in body["items"]]
    assert ids.index(str(newer.id)) < ids.index(str(older.id))
    item = next(u for u in body["items"] if u["id"] == str(newer.id))
    assert item["roles"] == ["CUSTOMER"]
    assert item["is_superuser"] is False
    for secret in ("password", "failed_login_attempts", "locked_until"):
        assert secret not in item


@pytest.mark.django_db
def test_list_users_filters(client, admin):
    customer = make_user(full_name="Alice Smith", email="alice@example.com")
    dual = make_user(is_contractor=True, full_name="Bob Dual")
    contractor, _ = make_contractor(full_name="Carl Contractor")
    suspended = make_user(status=UserStatus.SUSPENDED)
    h = auth(admin)

    def ids(query):
        r = client.get(f"/api/admin/users?{query}", **h)
        assert r.status_code == 200, r.content
        return {u["id"] for u in r.json()["items"]}

    assert ids("role=CONTRACTOR") == {str(contractor.id)}
    assert ids("role=ADMIN") == {str(admin.id)}
    assert ids("is_contractor=true") == {str(dual.id)}
    assert ids("status=SUSPENDED") == {str(suspended.id)}
    assert ids("q=alice@") == {str(customer.id)}
    assert ids("q=smith") == {str(customer.id)}
    assert ids(f"q={contractor.phone[-6:]}") == {str(contractor.id)}

    today = timezone.localdate()
    customer.date_joined = timezone.now() - timedelta(days=10)
    customer.save()
    assert str(customer.id) not in ids(f"joined_from={today - timedelta(days=1)}")
    assert ids(
        f"joined_from={today - timedelta(days=11)}&joined_to={today - timedelta(days=9)}"
    ) == {str(customer.id)}


@pytest.mark.django_db
def test_list_users_pagination(client, admin):
    for _ in range(5):
        make_user()
    r = client.get("/api/admin/users?limit=2&offset=1", **auth(admin))
    body = r.json()
    assert body["count"] == 6
    assert len(body["items"]) == 2

    r = client.get("/api/admin/users?limit=2&offset=5", **auth(admin))
    assert len(r.json()["items"]) == 1


# ------------------------------------------------------------
# التفاصيل
# ------------------------------------------------------------
@pytest.mark.django_db
def test_user_detail_counts_and_providers(client, admin):
    user = make_user(is_contractor=True)
    prop = make_property(user)
    make_property(user)
    make_booking(user, prop)
    make_booking(user, prop)
    make_booking(user, prop)
    SocialAccount.objects.create(user=user, provider=SocialProvider.GOOGLE, provider_user_id="g-1")
    SocialAccount.objects.create(user=user, provider=SocialProvider.APPLE, provider_user_id="a-1")

    r = client.get(f"/api/admin/users/{user.id}", **auth(admin))

    assert r.status_code == 200, r.content
    body = r.json()
    assert body["bookings_count"] == 3
    assert body["properties_count"] == 2
    assert body["social_providers"] == ["APPLE", "GOOGLE"]
    assert body["roles"] == ["CUSTOMER", "CONTRACTOR"]
    assert body["contractor_status"] == "NONE"
    assert body["contractor_profile_id"] is None


@pytest.mark.django_db
def test_user_detail_for_contractor(client, admin):
    user, profile = make_contractor()
    body = client.get(f"/api/admin/users/{user.id}", **auth(admin)).json()
    assert body["contractor_profile_id"] == str(profile.id)
    assert body["contractor_status"] == "PENDING"


@pytest.mark.django_db
def test_superuser_flag_is_a_boolean(client, admin):
    su = make_superuser()
    body = client.get(f"/api/admin/users/{su.id}", **auth(admin)).json()
    assert body["is_superuser"] is True
    assert body["role"] == ConfirmedRole.ADMIN


# ------------------------------------------------------------
# الإيقاف
# ------------------------------------------------------------
@pytest.mark.django_db
def test_suspend_user(client, admin):
    user, profile = make_contractor()

    r = post(client, f"/api/admin/users/{user.id}/suspend", {"reason": "fraud report"}, **auth(admin))

    assert r.status_code == 200, r.content
    assert r.json()["status"] == "SUSPENDED"
    assert r.json()["contractor_status"] == "SUSPENDED"
    user.refresh_from_db()
    profile.refresh_from_db()
    assert user.status == UserStatus.SUSPENDED
    assert profile.availability_status == AvailabilityStatus.UNAVAILABLE

    entry = AuditLog.objects.get(action="user.suspend")
    assert entry.actor == admin
    assert entry.target_type == "User"
    assert entry.target_id == str(user.id)
    assert entry.details["reason"] == "fraud report"
    assert entry.details["from"] == "ACTIVE"
    assert entry.details["contractor_profile_made_unavailable"] is True


@pytest.mark.django_db
def test_suspend_revokes_refresh_tokens_and_blocks_access(client, admin):
    user = make_user()
    pair_a = tokens(user)
    pair_b = tokens(user)
    headers = {"HTTP_AUTHORIZATION": f"Bearer {pair_a['access']}"}
    assert client.get("/api/auth/me", **headers).status_code == 200

    r = post(client, f"/api/admin/users/{user.id}/suspend", {"reason": "abuse"}, **auth(admin))
    assert r.status_code == 200
    assert AuditLog.objects.get(action="user.suspend").details["refresh_tokens_revoked"] >= 2

    # access token قائم يُرفض فورًا (ActiveUserJWTAuth يقرأ status من القاعدة)
    assert client.get("/api/auth/me", **headers).status_code == 401

    # refresh tokens كلها مُبطلة
    for pair in (pair_a, pair_b):
        r = post(client, "/api/auth/token/refresh", {"refresh": pair["refresh"]})
        assert r.status_code == 401, r.content

    # 📌 القائمة السوداء نفسها — لا الاعتماد على فحص status وحده
    from ninja_jwt.token_blacklist.models import BlacklistedToken

    assert BlacklistedToken.objects.filter(token__user=user).count() >= 2


@pytest.mark.django_db
def test_revoked_refresh_stays_revoked_after_reactivation(client, admin):
    user = make_user()
    pair = tokens(user)
    post(client, f"/api/admin/users/{user.id}/suspend", {"reason": "x"}, **auth(admin))
    post(client, f"/api/admin/users/{user.id}/reactivate", **auth(admin))

    r = post(client, "/api/auth/token/refresh", {"refresh": pair["refresh"]})
    assert r.status_code == 401


@pytest.mark.django_db
def test_suspend_requires_reason(client, admin):
    user = make_user()
    h = auth(admin)
    assert post(client, f"/api/admin/users/{user.id}/suspend", {}, **h).status_code == 422
    assert post(client, f"/api/admin/users/{user.id}/suspend", {"reason": "  "}, **h).status_code == 422
    user.refresh_from_db()
    assert user.status == UserStatus.ACTIVE


@pytest.mark.django_db
def test_suspend_already_suspended_is_409(client, admin):
    user = make_user(status=UserStatus.SUSPENDED)
    r = post(client, f"/api/admin/users/{user.id}/suspend", {"reason": "x"}, **auth(admin))
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_status_transition"
    assert not AuditLog.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("target_kind", ["admin", "superuser", "self"])
def test_admin_accounts_cannot_be_suspended(client, admin, target_kind):
    target = {"admin": make_admin, "superuser": make_superuser}.get(target_kind, lambda: admin)()

    for action, body in (("suspend", {"reason": "x"}), ("reactivate", None)):
        r = post(client, f"/api/admin/users/{target.id}/{action}", body, **auth(make_superuser()))
        assert r.status_code == 409, r.content
        assert r.json()["code"] == "admin_account"

    target.refresh_from_db()
    assert target.status == UserStatus.ACTIVE
    assert not AuditLog.objects.exists()


# ------------------------------------------------------------
# إعادة التفعيل
# ------------------------------------------------------------
@pytest.mark.django_db
def test_reactivate_user(client, admin):
    user, profile = make_contractor()
    post(client, f"/api/admin/users/{user.id}/suspend", {"reason": "x"}, **auth(admin))

    r = post(client, f"/api/admin/users/{user.id}/reactivate", **auth(admin))

    assert r.status_code == 200, r.content
    assert r.json()["status"] == "ACTIVE"
    user.refresh_from_db()
    profile.refresh_from_db()
    assert user.status == UserStatus.ACTIVE
    # الإتاحة فعل يخص المقاول وحده — لا تعود تلقائيًا
    assert profile.availability_status == AvailabilityStatus.UNAVAILABLE

    entry = AuditLog.objects.get(action="user.reactivate")
    assert entry.details == {"from": "SUSPENDED", "to": "ACTIVE"}

    # يستطيع الدخول مجددًا بتوكن جديد
    assert client.get("/api/auth/me", **auth(user)).status_code == 200


@pytest.mark.django_db
def test_reactivate_inactive_user(client, admin):
    user = make_user(status=UserStatus.INACTIVE)
    r = post(client, f"/api/admin/users/{user.id}/reactivate", **auth(admin))
    assert r.status_code == 200
    assert AuditLog.objects.get(action="user.reactivate").details["from"] == "INACTIVE"


@pytest.mark.django_db
def test_reactivate_active_user_is_409(client, admin):
    user = make_user()
    r = post(client, f"/api/admin/users/{user.id}/reactivate", **auth(admin))
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_status_transition"


@pytest.mark.django_db
def test_audit_records_ip(client, admin):
    user = make_user()
    post(
        client,
        f"/api/admin/users/{user.id}/suspend",
        {"reason": "x"},
        REMOTE_ADDR="10.1.2.3",
        **auth(admin),
    )
    assert AuditLog.objects.get(action="user.suspend").ip_address == "10.1.2.3"
