"""
Properties API Integration Tests (Phase 1)

يغطي: الإنشاء المتداخل، العزل بين المستخدمين، بوابة الدور، عدم المصادقة،
الحذف الناعم، وأخطاء التحقق.
"""

import json
import uuid

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.properties.models import AustralianState, Property, PropertyType

VALID_ADDRESS = {
    "street_address": "12 Example St",
    "suburb": "Bondi",
    "state": "NSW",
    "postcode": "2026",
}

# رمز بريدي حقيقي لكل ولاية — الرمز والولاية يجب أن يتّسقا (models.clean)
CAPITAL_POSTCODES = {
    "NSW": "2000", "VIC": "3000", "QLD": "4000", "SA": "5000",
    "WA": "6000", "TAS": "7000", "NT": "0800", "ACT": "2600",
}

VALID_PROPERTY = {
    "label": "Home",
    "property_type": "HOUSE",
    "address": VALID_ADDRESS,
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
def customer_a(db):
    return make_user("+61400001001")


@pytest.fixture
def customer_b(db):
    return make_user("+61400001002")


@pytest.fixture
def contractor(db):
    return make_user("+61400001003", role=ConfirmedRole.CONTRACTOR)


@pytest.fixture
def admin_user(db):
    return make_user("+61400001004", role=ConfirmedRole.ADMIN)


def create_via_api(client, user, payload=None):
    r = post(client, "/api/properties", payload or VALID_PROPERTY, **auth(user))
    assert r.status_code == 201, r.content
    return r.json()


# ============================================================
# 1) إنشاء عقار بعنوان متداخل صحيح → 201
# ============================================================
@pytest.mark.django_db
def test_customer_creates_property_with_nested_address(client, customer_a):
    r = post(client, "/api/properties", VALID_PROPERTY, **auth(customer_a))

    assert r.status_code == 201, r.content
    body = r.json()

    assert body["label"] == "Home"
    assert body["property_type"] == "HOUSE"
    assert body["is_active"] is True
    assert body["owner_id"] == str(customer_a.id)

    addr = body["address"]
    assert addr["street_address"] == "12 Example St"
    assert addr["suburb"] == "Bondi"
    assert addr["state"] == "NSW"
    assert addr["postcode"] == "2026"
    assert addr["country"] == "AU"
    # الإحداثيات تُعبَّأ لاحقًا (Phase 2)
    assert addr["latitude"] is None and addr["longitude"] is None

    # حُفظ فعلًا
    prop = Property.objects.get(pk=body["id"])
    assert prop.owner_id == customer_a.id
    assert prop.address.suburb == "Bondi"


@pytest.mark.django_db
def test_response_does_not_leak_owner_account_fields(client, customer_a):
    body = create_via_api(client, customer_a)
    text = json.dumps(body)

    # المعرّف فقط — لا هاتف/بريد/دور/حالة
    assert body["owner_id"] == str(customer_a.id)
    assert customer_a.phone not in text
    for leaked in ("role", "status", "is_staff", "password", "phone", "email"):
        assert leaked not in body


@pytest.mark.django_db
def test_all_australian_states_accepted(client, customer_a):
    for i, state in enumerate(AustralianState.values):
        payload = {
            "label": f"P{i}",
            "property_type": "UNIT",
            "address": {**VALID_ADDRESS, "state": state,
                        "postcode": CAPITAL_POSTCODES[state]},
        }
        r = post(client, "/api/properties", payload, **auth(customer_a))
        assert r.status_code == 201, (state, r.content)


@pytest.mark.django_db
def test_create_with_invalid_postcode_is_rejected(client, customer_a):
    payload = {**VALID_PROPERTY, "address": {**VALID_ADDRESS, "postcode": "12"}}
    r = post(client, "/api/properties", payload, **auth(customer_a))
    assert r.status_code == 422
    assert Property.objects.count() == 0


# ============================================================
# 2) عزل المستخدمين — 404 (موثّق: لا نكشف وجود عقار الغير)
# ============================================================
@pytest.mark.django_db
def test_customer_b_cannot_get_customer_a_property(client, customer_a, customer_b):
    created = create_via_api(client, customer_a)

    r = client.get(f"/api/properties/{created['id']}", **auth(customer_b))
    assert r.status_code == 404
    assert r.json()["code"] == "property_not_found"


@pytest.mark.django_db
def test_customer_b_cannot_patch_customer_a_property(client, customer_a, customer_b):
    created = create_via_api(client, customer_a)

    r = patch(
        client, f"/api/properties/{created['id']}", {"label": "Hacked"}, **auth(customer_b)
    )
    assert r.status_code == 404

    prop = Property.objects.get(pk=created["id"])
    assert prop.label == "Home"


@pytest.mark.django_db
def test_customer_b_cannot_delete_customer_a_property(client, customer_a, customer_b):
    created = create_via_api(client, customer_a)

    r = client.delete(f"/api/properties/{created['id']}", **auth(customer_b))
    assert r.status_code == 404

    prop = Property.objects.get(pk=created["id"])
    assert prop.is_active is True


@pytest.mark.django_db
def test_nonexistent_and_forbidden_are_indistinguishable(client, customer_a, customer_b):
    """
    سياسة موثّقة: 404 للحالتين — عقار غير موجود، وعقار يخص غيرك.
    الردّان متطابقان حتى لا يكشف الرد وجود المورد.
    """
    created = create_via_api(client, customer_a)

    forbidden = client.get(f"/api/properties/{created['id']}", **auth(customer_b))
    missing = client.get(f"/api/properties/{uuid.uuid4()}", **auth(customer_b))

    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json() == missing.json()


@pytest.mark.django_db
def test_list_returns_only_own_properties(client, customer_a, customer_b):
    create_via_api(client, customer_a, {**VALID_PROPERTY, "label": "A1"})
    create_via_api(client, customer_a, {**VALID_PROPERTY, "label": "A2"})
    create_via_api(client, customer_b, {**VALID_PROPERTY, "label": "B1"})

    r = client.get("/api/properties", **auth(customer_a))
    assert r.status_code == 200
    labels = {p["label"] for p in r.json()}
    assert labels == {"A1", "A2"}


# ============================================================
# 3) بوابة الدور — CONTRACTOR / ADMIN → 403
# ============================================================
@pytest.mark.django_db
def test_contractor_forbidden_on_all_endpoints(client, customer_a, contractor):
    created = create_via_api(client, customer_a)
    pid = created["id"]

    calls = [
        ("POST", lambda: post(client, "/api/properties", VALID_PROPERTY, **auth(contractor))),
        ("GET list", lambda: client.get("/api/properties", **auth(contractor))),
        ("GET one", lambda: client.get(f"/api/properties/{pid}", **auth(contractor))),
        ("PATCH", lambda: patch(client, f"/api/properties/{pid}", {"label": "x"}, **auth(contractor))),
        ("DELETE", lambda: client.delete(f"/api/properties/{pid}", **auth(contractor))),
    ]
    for name, call in calls:
        r = call()
        assert r.status_code == 403, f"{name} returned {r.status_code}"
        assert r.json()["code"] == "invalid_owner_role"


@pytest.mark.django_db
def test_admin_role_also_forbidden_on_customer_endpoints(client, admin_user):
    r = client.get("/api/properties", **auth(admin_user))
    assert r.status_code == 403
    assert r.json()["code"] == "invalid_owner_role"


@pytest.mark.django_db
def test_contractor_cannot_create_property_even_for_self(client, contractor):
    r = post(client, "/api/properties", VALID_PROPERTY, **auth(contractor))
    assert r.status_code == 403
    assert Property.objects.count() == 0


# ============================================================
# 4) بدون مصادقة → 401
# ============================================================
@pytest.mark.django_db
def test_unauthenticated_requests_return_401(client, customer_a):
    created = create_via_api(client, customer_a)
    pid = created["id"]

    assert post(client, "/api/properties", VALID_PROPERTY).status_code == 401
    assert client.get("/api/properties").status_code == 401
    assert client.get(f"/api/properties/{pid}").status_code == 401
    assert patch(client, f"/api/properties/{pid}", {"label": "x"}).status_code == 401
    assert client.delete(f"/api/properties/{pid}").status_code == 401


@pytest.mark.django_db
def test_invalid_token_returns_401(client):
    bad = {"HTTP_AUTHORIZATION": "Bearer not-a-token"}
    assert client.get("/api/properties", **bad).status_code == 401


# ============================================================
# 5) الحذف الناعم
# ============================================================
@pytest.mark.django_db
def test_delete_soft_deletes_and_hides_from_list(client, customer_a):
    created = create_via_api(client, customer_a)
    pid = created["id"]

    r = client.delete(f"/api/properties/{pid}", **auth(customer_a))
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    # اختفى من القائمة
    listed = client.get("/api/properties", **auth(customer_a)).json()
    assert all(p["id"] != pid for p in listed)

    # لكن الصف ما زال موجودًا في قاعدة البيانات
    prop = Property.objects.get(pk=pid)
    assert prop.is_active is False
    assert Property.objects.filter(pk=pid).exists()


@pytest.mark.django_db
def test_delete_does_not_hard_delete_address_either(client, customer_a):
    created = create_via_api(client, customer_a)
    client.delete(f"/api/properties/{created['id']}", **auth(customer_a))

    prop = Property.objects.get(pk=created["id"])
    assert prop.address is not None
    assert prop.address.suburb == "Bondi"


@pytest.mark.django_db
def test_soft_deleted_property_still_retrievable_by_id(client, customer_a):
    """الصف موجود، فالقراءة المباشرة ما زالت ممكنة للمالك."""
    created = create_via_api(client, customer_a)
    client.delete(f"/api/properties/{created['id']}", **auth(customer_a))

    r = client.get(f"/api/properties/{created['id']}", **auth(customer_a))
    assert r.status_code == 200
    assert r.json()["is_active"] is False


# ============================================================
# 6) PATCH مع بيانات غير صالحة
# ============================================================
@pytest.mark.django_db
def test_patch_with_invalid_state_is_rejected(client, customer_a):
    created = create_via_api(client, customer_a)

    r = patch(
        client,
        f"/api/properties/{created['id']}",
        {"address": {"state": "XYZ"}},
        **auth(customer_a),
    )
    assert r.status_code == 422, r.content

    prop = Property.objects.get(pk=created["id"])
    assert prop.address.state == "NSW"


@pytest.mark.django_db
@pytest.mark.parametrize("bad_postcode", ["12", "123456", "abcd", "20a6"])
def test_patch_with_invalid_postcode_is_rejected(client, customer_a, bad_postcode):
    created = create_via_api(client, customer_a)

    r = patch(
        client,
        f"/api/properties/{created['id']}",
        {"address": {"postcode": bad_postcode}},
        **auth(customer_a),
    )
    assert r.status_code == 422, r.content

    prop = Property.objects.get(pk=created["id"])
    assert prop.address.postcode == "2026"


@pytest.mark.django_db
def test_patch_valid_updates_property_and_address(client, customer_a):
    created = create_via_api(client, customer_a)

    r = patch(
        client,
        f"/api/properties/{created['id']}",
        {
            "label": "Investment Unit 2",
            "property_type": "APARTMENT",
            "address": {"suburb": "Carlton", "state": "VIC", "postcode": "3053"},
        },
        **auth(customer_a),
    )
    assert r.status_code == 200, r.content
    body = r.json()

    assert body["label"] == "Investment Unit 2"
    assert body["property_type"] == "APARTMENT"
    assert body["address"]["suburb"] == "Carlton"
    assert body["address"]["state"] == "VIC"
    assert body["address"]["postcode"] == "3053"
    # الحقول غير المُرسلة لم تتغير
    assert body["address"]["street_address"] == "12 Example St"


@pytest.mark.django_db
def test_patch_cannot_transfer_ownership(client, customer_a, customer_b):
    """owner ليس ضمن حقول الـschema — إرساله يُتجاهل."""
    created = create_via_api(client, customer_a)

    patch(
        client,
        f"/api/properties/{created['id']}",
        {"owner_id": str(customer_b.id), "label": "X"},
        **auth(customer_a),
    )

    prop = Property.objects.get(pk=created["id"])
    assert prop.owner_id == customer_a.id


@pytest.mark.django_db
def test_patch_can_reactivate_property(client, customer_a):
    created = create_via_api(client, customer_a)
    client.delete(f"/api/properties/{created['id']}", **auth(customer_a))

    r = patch(
        client, f"/api/properties/{created['id']}", {"is_active": True}, **auth(customer_a)
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is True


# ============================================================
# عام
# ============================================================
@pytest.mark.django_db
def test_service_layer_ownership_is_reused_not_duplicated():
    """طبقة الـAPI تستدعي خدمات Prompt 1 ولا تكرر منطق الملكية."""
    import inspect

    import apps.properties.api.properties as api_mod

    src = inspect.getsource(api_mod)
    # لا مقارنة ملكية مباشرة في طبقة الـAPI
    assert "owner_id ==" not in src
    assert "owner ==" not in src
    # تستخدم خدمات الملكية
    for fn in ("svc.get_property", "svc.update_property", "svc.deactivate_property"):
        assert fn in src


@pytest.mark.django_db
def test_identity_endpoints_still_work(client, customer_a):
    """لا انحدار على Identity Domain."""
    r = client.get("/api/auth/me", **auth(customer_a))
    assert r.status_code == 200
    assert r.json()["role"] == ConfirmedRole.CUSTOMER
