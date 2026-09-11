"""
Service Catalog & Pricing API Integration Tests (Change Set §36.2، §5)

يغطي: CRUD الكامل، إنفاذ ADMIN-only (403 لـCUSTOMER/CONTRACTOR والمجهول)،
الحذف الناعم، وقراءة/تحديث إعداد التسعير العام.
"""

import json
import uuid
from decimal import Decimal

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.services.models import PricingConfig, ServiceType

VALID_SERVICE = {
    "name": "General Cleaning",
    "description": "Standard whole-home clean.",
    "room_price": "45.00",
    "base_price": "80.00",
}


# ------------------------------------------------------------
# أدوات
# ------------------------------------------------------------
@pytest.fixture
def client():
    return Client()


def make_user(phone, role=ConfirmedRole.CUSTOMER):
    return User.objects.create_user(phone=phone, role=role)


def auth(user):
    token = issue_tokens_for_user(user)["access"]
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def post(client, url, payload, **extra):
    return client.post(
        url, data=json.dumps(payload), content_type="application/json", **extra
    )


def patch(client, url, payload, **extra):
    return client.patch(
        url, data=json.dumps(payload), content_type="application/json", **extra
    )


@pytest.fixture
def admin_user(db):
    return make_user("+61400002001", role=ConfirmedRole.ADMIN)


@pytest.fixture
def other_admin(db):
    return make_user("+61400002002", role=ConfirmedRole.ADMIN)


@pytest.fixture
def customer(db):
    return make_user("+61400002003", role=ConfirmedRole.CUSTOMER)


@pytest.fixture
def contractor(db):
    return make_user("+61400002004", role=ConfirmedRole.CONTRACTOR)


def create_via_api(client, user, payload=None):
    r = post(client, "/api/admin/services", payload or VALID_SERVICE, **auth(user))
    assert r.status_code == 201, r.content
    return r.json()


# ============================================================
# 1) إنشاء — POST /api/admin/services
# ============================================================
@pytest.mark.django_db
def test_admin_creates_service_type(client, admin_user):
    r = post(client, "/api/admin/services", VALID_SERVICE, **auth(admin_user))

    assert r.status_code == 201, r.content
    body = r.json()

    assert body["name"] == "General Cleaning"
    assert body["description"] == "Standard whole-home clean."
    assert Decimal(body["room_price"]) == Decimal("45.00")
    assert Decimal(body["base_price"]) == Decimal("80.00")
    assert body["is_active"] is True
    assert body["created_at"] and body["updated_at"]

    # حُفظ فعلًا
    service = ServiceType.objects.get(pk=body["id"])
    assert service.room_price == Decimal("45.00")
    assert service.base_price == Decimal("80.00")


@pytest.mark.django_db
def test_created_id_is_uuid(client, admin_user):
    body = create_via_api(client, admin_user)
    # يرفع ValueError إن لم يكن UUID صالحًا
    assert uuid.UUID(body["id"])


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name", ["General Cleaning", "Carpet Cleaning", "Garden Cleaning"]
)
def test_catalog_accepts_the_named_service_types(client, admin_user, name):
    r = post(
        client, "/api/admin/services", {**VALID_SERVICE, "name": name}, **auth(admin_user)
    )
    assert r.status_code == 201, r.content
    assert r.json()["name"] == name


@pytest.mark.django_db
def test_description_is_optional(client, admin_user):
    payload = {k: v for k, v in VALID_SERVICE.items() if k != "description"}
    r = post(client, "/api/admin/services", payload, **auth(admin_user))

    assert r.status_code == 201, r.content
    assert r.json()["description"] == ""


@pytest.mark.django_db
def test_prices_are_scoped_per_service_not_global(client, admin_user):
    """
    §36.2: room_price و base_price لكل خدمة على حدة.
    تغيير سعر خدمة لا يمس خدمة أخرى.
    """
    a = create_via_api(
        client,
        admin_user,
        {**VALID_SERVICE, "name": "Carpet Cleaning", "room_price": "30.00"},
    )
    b = create_via_api(
        client,
        admin_user,
        {**VALID_SERVICE, "name": "Garden Cleaning", "room_price": "70.00"},
    )

    assert Decimal(a["room_price"]) == Decimal("30.00")
    assert Decimal(b["room_price"]) == Decimal("70.00")

    # تعديل سعر الأولى لا يغيّر الثانية
    patch(
        client, f"/api/admin/services/{a['id']}", {"room_price": "35.00"}, **auth(admin_user)
    )

    assert ServiceType.objects.get(pk=a["id"]).room_price == Decimal("35.00")
    assert ServiceType.objects.get(pk=b["id"]).room_price == Decimal("70.00")


@pytest.mark.django_db
def test_duplicate_name_is_rejected(client, admin_user):
    create_via_api(client, admin_user)

    r = post(client, "/api/admin/services", VALID_SERVICE, **auth(admin_user))
    assert r.status_code == 422, r.content
    assert ServiceType.objects.filter(name="General Cleaning").count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["room_price", "base_price"])
def test_negative_prices_are_rejected(client, admin_user, field):
    r = post(
        client, "/api/admin/services", {**VALID_SERVICE, field: "-1.00"}, **auth(admin_user)
    )
    assert r.status_code == 422, r.content
    assert ServiceType.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["room_price", "base_price"])
def test_zero_prices_are_accepted(client, admin_user, field):
    """صفر قيمة مشروعة (خدمة ترويجية أو بلا رسم أساسي)."""
    r = post(
        client, "/api/admin/services", {**VALID_SERVICE, field: "0.00"}, **auth(admin_user)
    )
    assert r.status_code == 201, r.content
    assert Decimal(r.json()[field]) == Decimal("0.00")


@pytest.mark.django_db
def test_missing_required_price_is_rejected(client, admin_user):
    payload = {k: v for k, v in VALID_SERVICE.items() if k != "room_price"}
    r = post(client, "/api/admin/services", payload, **auth(admin_user))
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_blank_name_is_rejected(client, admin_user):
    r = post(client, "/api/admin/services", {**VALID_SERVICE, "name": ""}, **auth(admin_user))
    assert r.status_code == 422, r.content
    assert ServiceType.objects.count() == 0


# ============================================================
# 2) قراءة — GET list / GET one
# ============================================================
@pytest.mark.django_db
def test_list_returns_active_and_inactive(client, admin_user):
    """
    قائمة الإدارة لا تُرشِّح المعطّل — بخلاف قائمة العقارات الموجّهة للعميل.
    """
    active = create_via_api(client, admin_user, {**VALID_SERVICE, "name": "Active One"})
    inactive = create_via_api(
        client, admin_user, {**VALID_SERVICE, "name": "Inactive One"}
    )
    client.delete(f"/api/admin/services/{inactive['id']}", **auth(admin_user))

    r = client.get("/api/admin/services", **auth(admin_user))
    assert r.status_code == 200

    by_id = {s["id"]: s for s in r.json()}
    assert active["id"] in by_id
    assert inactive["id"] in by_id
    assert by_id[active["id"]]["is_active"] is True
    assert by_id[inactive["id"]]["is_active"] is False


@pytest.mark.django_db
def test_list_is_empty_when_no_services(client, admin_user):
    r = client.get("/api/admin/services", **auth(admin_user))
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.django_db
def test_retrieve_single_service(client, admin_user):
    created = create_via_api(client, admin_user)

    r = client.get(f"/api/admin/services/{created['id']}", **auth(admin_user))
    assert r.status_code == 200
    assert r.json() == created


@pytest.mark.django_db
def test_retrieve_unknown_id_returns_404(client, admin_user):
    r = client.get(f"/api/admin/services/{uuid.uuid4()}", **auth(admin_user))
    assert r.status_code == 404
    assert r.json()["code"] == "service_not_found"


@pytest.mark.django_db
def test_catalog_is_shared_across_admins(client, admin_user, other_admin):
    """الكتالوج مورد عام للنظام — ليس مملوكًا لمُنشئه."""
    created = create_via_api(client, admin_user)

    r = client.get(f"/api/admin/services/{created['id']}", **auth(other_admin))
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


# ============================================================
# 3) تعديل — PATCH
# ============================================================
@pytest.mark.django_db
def test_patch_updates_non_price_fields(client, admin_user):
    created = create_via_api(client, admin_user)

    r = patch(
        client,
        f"/api/admin/services/{created['id']}",
        {"name": "Deep Cleaning", "description": "Updated."},
        **auth(admin_user),
    )
    assert r.status_code == 200, r.content
    body = r.json()

    assert body["name"] == "Deep Cleaning"
    assert body["description"] == "Updated."
    # الحقول غير المُرسلة لم تتغير
    assert Decimal(body["room_price"]) == Decimal("45.00")


@pytest.mark.django_db
def test_patch_updates_prices_at_any_time(client, admin_user):
    """
    §36.2: حقول السعر قابلة للتعديل في أي وقت بلا قيود.
    """
    created = create_via_api(client, admin_user)

    r = patch(
        client,
        f"/api/admin/services/{created['id']}",
        {"room_price": "55.50", "base_price": "99.99"},
        **auth(admin_user),
    )
    assert r.status_code == 200, r.content
    body = r.json()

    assert Decimal(body["room_price"]) == Decimal("55.50")
    assert Decimal(body["base_price"]) == Decimal("99.99")

    service = ServiceType.objects.get(pk=created["id"])
    assert service.room_price == Decimal("55.50")
    assert service.base_price == Decimal("99.99")


@pytest.mark.django_db
def test_price_edit_stores_current_value_only(client, admin_user):
    """
    ⚠️ لا إعادة حساب رجعي ولا تاريخ أسعار في هذه المرحلة — الـModel يخزّن
       القيمة الحيّة الحالية فقط. تجميد السعر على الحجز شأن Booking Domain.
    """
    created = create_via_api(client, admin_user)

    for price in ("50.00", "60.00", "70.00"):
        patch(
            client,
            f"/api/admin/services/{created['id']}",
            {"room_price": price},
            **auth(admin_user),
        )

    service = ServiceType.objects.get(pk=created["id"])
    # القيمة الأخيرة فقط، ولا صف تاريخي إضافي
    assert service.room_price == Decimal("70.00")
    assert ServiceType.objects.count() == 1


@pytest.mark.django_db
def test_patch_negative_price_is_rejected(client, admin_user):
    created = create_via_api(client, admin_user)

    r = patch(
        client,
        f"/api/admin/services/{created['id']}",
        {"room_price": "-5.00"},
        **auth(admin_user),
    )
    assert r.status_code == 422, r.content

    service = ServiceType.objects.get(pk=created["id"])
    assert service.room_price == Decimal("45.00")


@pytest.mark.django_db
def test_patch_can_reactivate_service(client, admin_user):
    created = create_via_api(client, admin_user)
    client.delete(f"/api/admin/services/{created['id']}", **auth(admin_user))

    r = patch(
        client, f"/api/admin/services/{created['id']}", {"is_active": True}, **auth(admin_user)
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is True
    assert ServiceType.objects.get(pk=created["id"]).is_active is True


@pytest.mark.django_db
def test_patch_unknown_id_returns_404(client, admin_user):
    r = patch(
        client, f"/api/admin/services/{uuid.uuid4()}", {"name": "X"}, **auth(admin_user)
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_patch_to_duplicate_name_is_rejected(client, admin_user):
    create_via_api(client, admin_user, {**VALID_SERVICE, "name": "Carpet Cleaning"})
    second = create_via_api(client, admin_user, {**VALID_SERVICE, "name": "Garden Cleaning"})

    r = patch(
        client,
        f"/api/admin/services/{second['id']}",
        {"name": "Carpet Cleaning"},
        **auth(admin_user),
    )
    assert r.status_code == 422, r.content
    assert ServiceType.objects.get(pk=second["id"]).name == "Garden Cleaning"


@pytest.mark.django_db
def test_empty_patch_is_a_noop(client, admin_user):
    created = create_via_api(client, admin_user)

    r = patch(client, f"/api/admin/services/{created['id']}", {}, **auth(admin_user))
    assert r.status_code == 200
    assert r.json()["name"] == created["name"]


# ============================================================
# 4) الحذف الناعم — DELETE
# ============================================================
@pytest.mark.django_db
def test_delete_is_soft_and_keeps_the_row(client, admin_user):
    created = create_via_api(client, admin_user)

    r = client.delete(f"/api/admin/services/{created['id']}", **auth(admin_user))
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    # الصف ما زال موجودًا، بأسعاره
    service = ServiceType.objects.get(pk=created["id"])
    assert service.is_active is False
    assert service.room_price == Decimal("45.00")
    assert ServiceType.objects.filter(pk=created["id"]).exists()


@pytest.mark.django_db
def test_soft_deleted_service_still_retrievable(client, admin_user):
    created = create_via_api(client, admin_user)
    client.delete(f"/api/admin/services/{created['id']}", **auth(admin_user))

    r = client.get(f"/api/admin/services/{created['id']}", **auth(admin_user))
    assert r.status_code == 200
    assert r.json()["is_active"] is False


@pytest.mark.django_db
def test_delete_is_idempotent(client, admin_user):
    created = create_via_api(client, admin_user)

    first = client.delete(f"/api/admin/services/{created['id']}", **auth(admin_user))
    second = client.delete(f"/api/admin/services/{created['id']}", **auth(admin_user))

    assert first.status_code == second.status_code == 200
    assert second.json()["is_active"] is False


@pytest.mark.django_db
def test_delete_unknown_id_returns_404(client, admin_user):
    r = client.delete(f"/api/admin/services/{uuid.uuid4()}", **auth(admin_user))
    assert r.status_code == 404


@pytest.mark.django_db
def test_delete_does_not_affect_other_services(client, admin_user):
    keep = create_via_api(client, admin_user, {**VALID_SERVICE, "name": "Keep Me"})
    drop = create_via_api(client, admin_user, {**VALID_SERVICE, "name": "Drop Me"})

    client.delete(f"/api/admin/services/{drop['id']}", **auth(admin_user))

    assert ServiceType.objects.get(pk=keep["id"]).is_active is True


# ============================================================
# 5) بوابة الدور — ADMIN فقط (403 لغيره)
# ============================================================
def _all_calls(client, actor, service_id):
    """كل نقاط النهاية في هذا النطاق، بفاعل واحد."""
    return [
        ("POST services", lambda: post(client, "/api/admin/services", VALID_SERVICE, **auth(actor))),
        ("GET services", lambda: client.get("/api/admin/services", **auth(actor))),
        ("GET service", lambda: client.get(f"/api/admin/services/{service_id}", **auth(actor))),
        ("PATCH service", lambda: patch(client, f"/api/admin/services/{service_id}", {"name": "X"}, **auth(actor))),
        ("DELETE service", lambda: client.delete(f"/api/admin/services/{service_id}", **auth(actor))),
        ("GET pricing", lambda: client.get("/api/admin/pricing-config", **auth(actor))),
        ("PATCH pricing", lambda: patch(client, "/api/admin/pricing-config", {"price_per_km": "9.99"}, **auth(actor))),
    ]


@pytest.mark.django_db
def test_customer_forbidden_on_all_endpoints(client, admin_user, customer):
    created = create_via_api(client, admin_user)

    for name, call in _all_calls(client, customer, created["id"]):
        r = call()
        assert r.status_code == 403, f"{name} returned {r.status_code}"
        assert r.json()["code"] == "admin_role_required"


@pytest.mark.django_db
def test_contractor_forbidden_on_all_endpoints(client, admin_user, contractor):
    created = create_via_api(client, admin_user)

    for name, call in _all_calls(client, contractor, created["id"]):
        r = call()
        assert r.status_code == 403, f"{name} returned {r.status_code}"
        assert r.json()["code"] == "admin_role_required"


@pytest.mark.django_db
def test_unauthenticated_requests_return_401(client, admin_user):
    created = create_via_api(client, admin_user)
    sid = created["id"]

    assert post(client, "/api/admin/services", VALID_SERVICE).status_code == 401
    assert client.get("/api/admin/services").status_code == 401
    assert client.get(f"/api/admin/services/{sid}").status_code == 401
    assert patch(client, f"/api/admin/services/{sid}", {"name": "X"}).status_code == 401
    assert client.delete(f"/api/admin/services/{sid}").status_code == 401
    assert client.get("/api/admin/pricing-config").status_code == 401
    assert patch(client, "/api/admin/pricing-config", {"price_per_km": "1.00"}).status_code == 401


@pytest.mark.django_db
def test_invalid_token_returns_401(client):
    bad = {"HTTP_AUTHORIZATION": "Bearer not-a-token"}
    assert client.get("/api/admin/services", **bad).status_code == 401
    assert client.get("/api/admin/pricing-config", **bad).status_code == 401


@pytest.mark.django_db
def test_non_admin_cannot_mutate_anything(client, admin_user, customer, contractor):
    """الرفض ليس شكليًا: لا صف يُنشأ ولا قيمة تتغير."""
    created = create_via_api(client, admin_user)

    for actor in (customer, contractor):
        post(client, "/api/admin/services", {**VALID_SERVICE, "name": "Sneaky"}, **auth(actor))
        patch(client, f"/api/admin/services/{created['id']}", {"room_price": "1.00"}, **auth(actor))
        client.delete(f"/api/admin/services/{created['id']}", **auth(actor))
        patch(client, "/api/admin/pricing-config", {"price_per_km": "999.00"}, **auth(actor))

    assert not ServiceType.objects.filter(name="Sneaky").exists()
    service = ServiceType.objects.get(pk=created["id"])
    assert service.room_price == Decimal("45.00")
    assert service.is_active is True
    assert not PricingConfig.objects.filter(price_per_km=Decimal("999.00")).exists()


@pytest.mark.django_db
def test_no_customer_facing_catalog_endpoint_exists(client, customer):
    """
    في هذه المرحلة لا توجد نقطة نهاية تكشف الكتالوج للعميل.
    المسارات المتوقّعة مستقبلًا (Booking Domain) غير موجودة الآن.
    """
    for url in ("/api/services", "/api/service-types", "/api/pricing-config"):
        r = client.get(url, **auth(customer))
        assert r.status_code == 404, f"{url} unexpectedly exists ({r.status_code})"


# ============================================================
# 6) إعداد التسعير العام — PricingConfig
# ============================================================
@pytest.mark.django_db
def test_get_pricing_config_creates_default_on_first_read(client, admin_user):
    assert PricingConfig.objects.count() == 0

    r = client.get("/api/admin/pricing-config", **auth(admin_user))
    assert r.status_code == 200, r.content
    assert Decimal(r.json()["price_per_km"]) == Decimal("0.00")

    assert PricingConfig.objects.count() == 1


@pytest.mark.django_db
def test_update_pricing_config(client, admin_user):
    r = patch(
        client, "/api/admin/pricing-config", {"price_per_km": "2.75"}, **auth(admin_user)
    )
    assert r.status_code == 200, r.content
    assert Decimal(r.json()["price_per_km"]) == Decimal("2.75")

    # وقراءته لاحقًا تعيد نفس القيمة
    r2 = client.get("/api/admin/pricing-config", **auth(admin_user))
    assert Decimal(r2.json()["price_per_km"]) == Decimal("2.75")


@pytest.mark.django_db
def test_pricing_config_stays_a_singleton(client, admin_user):
    """§5: قيمة واحدة عامة — لا صفوف متعددة مهما تكرّر التحديث."""
    for price in ("1.00", "2.00", "3.00"):
        patch(client, "/api/admin/pricing-config", {"price_per_km": price}, **auth(admin_user))

    assert PricingConfig.objects.count() == 1
    assert PricingConfig.objects.get().price_per_km == Decimal("3.00")


@pytest.mark.django_db
def test_pricing_config_is_global_not_per_service(client, admin_user):
    """
    تغيير price_per_km لا يمس أي خدمة، ولا يوجد الحقل على ServiceType أصلًا.
    """
    created = create_via_api(client, admin_user)
    patch(client, "/api/admin/pricing-config", {"price_per_km": "3.50"}, **auth(admin_user))

    body = client.get(f"/api/admin/services/{created['id']}", **auth(admin_user)).json()
    assert "price_per_km" not in body

    field_names = {f.name for f in ServiceType._meta.get_fields()}
    assert "price_per_km" not in field_names


@pytest.mark.django_db
def test_negative_price_per_km_is_rejected(client, admin_user):
    patch(client, "/api/admin/pricing-config", {"price_per_km": "2.00"}, **auth(admin_user))

    r = patch(
        client, "/api/admin/pricing-config", {"price_per_km": "-1.00"}, **auth(admin_user)
    )
    assert r.status_code == 422, r.content
    assert PricingConfig.objects.get().price_per_km == Decimal("2.00")


@pytest.mark.django_db
def test_zero_price_per_km_is_accepted(client, admin_user):
    r = patch(
        client, "/api/admin/pricing-config", {"price_per_km": "0.00"}, **auth(admin_user)
    )
    assert r.status_code == 200, r.content
    assert Decimal(r.json()["price_per_km"]) == Decimal("0.00")


@pytest.mark.django_db
def test_pricing_config_update_is_visible_to_other_admins(client, admin_user, other_admin):
    patch(client, "/api/admin/pricing-config", {"price_per_km": "4.25"}, **auth(admin_user))

    r = client.get("/api/admin/pricing-config", **auth(other_admin))
    assert Decimal(r.json()["price_per_km"]) == Decimal("4.25")
