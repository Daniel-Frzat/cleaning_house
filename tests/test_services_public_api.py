"""
Public Service Catalog Tests — Services Domain (§36.2)

يغطي: القراءة لأي دور مصادَق عليه، إخفاء الخدمات المعطّلة، 404 الموحّد،
وغياب حقول التسعير من JSON الخام.

🔒 منهجية التحقق من الحجب (نفس بند 9 من تدقيق §47): الفحص على **مفاتيح**
   JSON الخام لا على قيمها. مفتاح موجود بقيمة null يعني "حقل محجوب"،
   وهو تسريب بنيوي وإن بدا فارغًا — المطلوب ألا يوجد المفتاح أصلًا.
"""

import json
from decimal import Decimal

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.services.models import ServiceType


# أي مفتاح يلمّح إلى التسعير — لا يجوز ظهوره في أي رد عام
PRICING_KEYS = {
    "room_price",
    "base_price",
    "price_per_km",
    "price",
    "total",
    "amount",
    "computed_price",
}


@pytest.fixture
def client():
    return Client()


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


@pytest.fixture
def customer(db):
    return User.objects.create_user(phone="+61400007001", role=ConfirmedRole.CUSTOMER)


@pytest.fixture
def contractor(db):
    return User.objects.create_user(
        phone="+61400007002", role=ConfirmedRole.CONTRACTOR
    )


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(phone="+61400007003", role=ConfirmedRole.ADMIN)


def make_service(name, is_active=True, description="Standard clean"):
    return ServiceType.objects.create(
        name=name,
        description=description,
        room_price=Decimal("45.00"),
        base_price=Decimal("80.00"),
        is_active=is_active,
    )


@pytest.fixture
def active_service(db):
    return make_service("General Cleaning")


@pytest.fixture
def inactive_service(db):
    return make_service("Retired Cleaning", is_active=False)


# ============================================================
# 1) الوصول والمصادقة
# ============================================================
@pytest.mark.django_db
def test_customer_can_list_services(client, customer, active_service):
    r = client.get("/api/services", **auth(customer))

    assert r.status_code == 200, r.content
    body = r.json()
    assert len(body) == 1
    assert body[0]["name"] == "General Cleaning"


@pytest.mark.django_db
@pytest.mark.parametrize("role", [ConfirmedRole.CONTRACTOR, ConfirmedRole.ADMIN])
def test_every_authenticated_role_may_read(client, db, active_service, role):
    """⚠️ لا حظر أدوار على هذين المسارين — أي مستخدم مصادَق عليه يقرأ."""
    user = User.objects.create_user(phone=f"+6140000{role[:4]}", role=role)

    assert client.get("/api/services", **auth(user)).status_code == 200
    assert (
        client.get(f"/api/services/{active_service.id}", **auth(user)).status_code
        == 200
    )


@pytest.mark.django_db
def test_unauthenticated_requests_are_rejected(client, active_service):
    """المصادقة مطلوبة رغم أن الدور غير مقيَّد."""
    assert client.get("/api/services").status_code == 401
    assert client.get(f"/api/services/{active_service.id}").status_code == 401


@pytest.mark.django_db
def test_malformed_token_is_rejected(client, active_service):
    bad = {"HTTP_AUTHORIZATION": "Bearer not-a-real-token"}
    assert client.get("/api/services", **bad).status_code == 401


# ============================================================
# 2) 🔒 حجب التسعير — فحص المفاتيح الخام
# ============================================================
@pytest.mark.django_db
def test_list_response_has_no_pricing_keys_in_raw_json(
    client, customer, active_service
):
    """
    🔒 الفحص على المفاتيح لا القيم (بند 9 من تدقيق §47).

    مفتاح room_price بقيمة null كان سيمرّ من فحص قيمي، وهو تسريب بنيوي.
    """
    r = client.get("/api/services", **auth(customer))
    raw = json.loads(r.content)

    for item in raw:
        keys = set(item.keys())
        assert keys == {"id", "name", "description"}, keys
        assert not (keys & PRICING_KEYS), keys & PRICING_KEYS


@pytest.mark.django_db
def test_detail_response_has_no_pricing_keys_in_raw_json(
    client, customer, active_service
):
    r = client.get(f"/api/services/{active_service.id}", **auth(customer))
    keys = set(json.loads(r.content).keys())

    assert keys == {"id", "name", "description"}, keys
    assert not (keys & PRICING_KEYS), keys & PRICING_KEYS


@pytest.mark.django_db
def test_pricing_strings_absent_from_raw_response_body(
    client, customer, active_service
):
    """
    حتى نصيًا: قيم الأسعار المخزَّنة لا تظهر في البايتات المُعادة.

    فحص أخشن من فحص المفاتيح ويكمّله — يمسك التسريب عبر حقل بديل
    أو تداخل غير متوقع.
    """
    body = client.get("/api/services", **auth(customer)).content.decode()

    assert "45.00" not in body
    assert "80.00" not in body
    assert "room_price" not in body
    assert "base_price" not in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role", [ConfirmedRole.CUSTOMER, ConfirmedRole.CONTRACTOR, ConfirmedRole.ADMIN]
)
def test_pricing_hidden_for_every_role_including_admin(
    client, db, active_service, role
):
    """
    🔒 الحجب بنيوي لا دوري: حتى ADMIN لا يرى السعر من هذا المسار.

    الإدارة تقرأ الأسعار من /api/admin/services — وهذا ما يجعل الحجب
    غير قابل للانكسار بتغيير دور أو خطأ في فحص صلاحية.
    """
    user = User.objects.create_user(phone=f"+6140001{role[:4]}", role=role)

    listed = json.loads(client.get("/api/services", **auth(user)).content)
    detail = json.loads(
        client.get(f"/api/services/{active_service.id}", **auth(user)).content
    )

    assert set(listed[0].keys()) == {"id", "name", "description"}
    assert set(detail.keys()) == {"id", "name", "description"}


@pytest.mark.django_db
def test_admin_endpoint_still_exposes_pricing(client, admin_user, active_service):
    """
    ⚠️ المسار الإداري لم يتغيّر: الأسعار تبقى مكشوفة للإدارة هناك.

    يثبت أن الحجب محصور في المسار العام ولم يكسر وظيفة قائمة.
    """
    r = client.get("/api/admin/services", **auth(admin_user))
    keys = set(json.loads(r.content)[0].keys())

    assert "room_price" in keys
    assert "base_price" in keys
    assert "is_active" in keys


# ============================================================
# 3) الخدمات المعطّلة
# ============================================================
@pytest.mark.django_db
def test_inactive_services_are_absent_from_the_list(
    client, customer, active_service, inactive_service
):
    body = client.get("/api/services", **auth(customer)).json()

    names = {s["name"] for s in body}
    assert names == {"General Cleaning"}
    assert "Retired Cleaning" not in names


@pytest.mark.django_db
def test_inactive_service_detail_returns_404(client, customer, inactive_service):
    r = client.get(f"/api/services/{inactive_service.id}", **auth(customer))

    assert r.status_code == 404
    assert r.json()["code"] == "service_not_found"


@pytest.mark.django_db
def test_unknown_and_inactive_are_indistinguishable(
    client, customer, inactive_service
):
    """🔒 نفس الرد حرفيًا — لا يكشف أن خدمة كانت موجودة ثم سُحبت."""
    import uuid

    inactive = client.get(f"/api/services/{inactive_service.id}", **auth(customer))
    unknown = client.get(f"/api/services/{uuid.uuid4()}", **auth(customer))

    assert inactive.status_code == unknown.status_code == 404
    assert inactive.json() == unknown.json()


@pytest.mark.django_db
def test_deactivating_a_service_removes_it_from_the_public_list(
    client, customer, admin_user, active_service
):
    """التعطيل الإداري ينعكس فورًا على العرض العام."""
    assert len(client.get("/api/services", **auth(customer)).json()) == 1

    client.delete(f"/api/admin/services/{active_service.id}", **auth(admin_user))

    assert client.get("/api/services", **auth(customer)).json() == []


@pytest.mark.django_db
def test_empty_catalog_returns_empty_list(client, customer):
    r = client.get("/api/services", **auth(customer))

    assert r.status_code == 200
    assert r.json() == []


# ============================================================
# 4) المحتوى
# ============================================================
@pytest.mark.django_db
def test_returned_id_is_usable_for_booking_selection(
    client, customer, active_service
):
    """📌 سبب وجود المسار: المعرّف المُعاد هو نفسه service_type_id للحجز."""
    body = client.get("/api/services", **auth(customer)).json()

    assert body[0]["id"] == str(active_service.id)


@pytest.mark.django_db
def test_description_is_returned_verbatim(client, customer):
    make_service("Deep Clean", description="Includes oven and windows")

    body = client.get("/api/services", **auth(customer)).json()

    assert body[0]["description"] == "Includes oven and windows"


@pytest.mark.django_db
def test_multiple_active_services_are_all_listed(client, customer):
    make_service("Alpha")
    make_service("Beta")
    make_service("Gamma", is_active=False)

    body = client.get("/api/services", **auth(customer)).json()

    assert {s["name"] for s in body} == {"Alpha", "Beta"}


# ============================================================
# 5) حدود النطاق
# ============================================================
def test_public_schema_has_no_inheritance_from_admin_schema():
    """
    🔒 الفصل بنيوي: ServicePublicOut لا يرث من أي schema إداري.

    هذا ما يمنع تكرار عيب Payout (بند 9 من §47) — لا وراثة ولا Union،
    فلا سبيل لأن يتسرّب حقل إداري إلى الشكل العام.
    """
    from ninja import Schema

    from apps.services.api import schemas as admin_schemas
    from apps.services.api.public_schemas import ServicePublicOut

    ancestors = set(ServicePublicOut.__mro__) - {ServicePublicOut}

    # الأصناف المُعرَّفة في الملف الإداري وحدها — Schema نفسها مستثناة،
    # فهي القاعدة المشتركة لكل schema في المشروع وتظهر في مساحة أسمائه
    # لمجرد أنه يستوردها.
    admin_types = {
        v for v in vars(admin_schemas).values()
        if isinstance(v, type)
        and issubclass(v, Schema)
        and v is not Schema
        and v.__module__ == admin_schemas.__name__
    }
    assert admin_types, "sanity: the admin module must define schemas"

    assert not (ancestors & admin_types), ancestors & admin_types
    # يرث من القاعدة مباشرة — لا طبقة وسيطة تحمل حقول تسعير
    assert ServicePublicOut.__mro__[1] is Schema


def test_public_schema_defines_exactly_three_fields():
    """أي حقل يُضاف مستقبلًا يكسر هذا الاختبار عمدًا — قرار واعٍ مطلوب."""
    from apps.services.api.public_schemas import ServicePublicOut

    assert set(ServicePublicOut.model_fields) == {"id", "name", "description"}


def test_public_schemas_module_does_not_import_admin_schemas():
    """لا اعتماد على الملف الإداري إطلاقًا — ولا حتى لشكل الخطأ."""
    import ast
    import pathlib

    src = pathlib.Path("apps/services/api/public_schemas.py").read_text(
        encoding="utf-8"
    )
    imported = {
        node.module
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not any("schemas" in (m or "") for m in imported), imported


def test_public_api_layer_makes_no_orm_calls():
    """§43: لا .objects. في طبقة الـAPI."""
    import ast
    import pathlib

    tree = ast.parse(
        pathlib.Path("apps/services/api/public_catalog.py").read_text(encoding="utf-8")
    )
    hits = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == "objects"
    ]

    assert hits == [], hits
