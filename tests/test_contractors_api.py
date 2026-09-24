"""
Contractor Profile API Integration Tests — Contractors Domain

يغطي: تقييد الإنشاء بدور CONTRACTOR، إنفاذ الملكية (مقاول لا يرى/يعدّل ملف
مقاول آخر)، تبديل الجاهزية، وصول الإدارة (قائمة/قراءة)، ومنع غير الإدارة
من مسارات /api/admin/contractors/*.
"""

import json
import uuid
from decimal import Decimal

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.contractors.models import AvailabilityStatus, ContractorProfile

VALID_PROFILE = {
    "business_name": "Sparkle Co",
    "street_address": "44 Trade St",
    "suburb": "Newtown",
    "state": "NSW",
    "postcode": "2042",
}


# ------------------------------------------------------------
# أدوات
# ------------------------------------------------------------
@pytest.fixture
def client():
    return Client()


def make_user(phone, role=ConfirmedRole.CONTRACTOR):
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
def contractor_a(db):
    return make_user("+61400004001")


@pytest.fixture
def contractor_b(db):
    return make_user("+61400004002")


@pytest.fixture
def customer(db):
    return make_user("+61400004003", role=ConfirmedRole.CUSTOMER)


@pytest.fixture
def admin_user(db):
    return make_user("+61400004004", role=ConfirmedRole.ADMIN)


def create_via_api(client, user, payload=None):
    r = post(client, "/api/contractor/profile", payload or VALID_PROFILE, **auth(user))
    assert r.status_code == 201, r.content
    return r.json()


# ============================================================
# 1) الإنشاء مقصور على دور CONTRACTOR
# ============================================================
@pytest.mark.django_db
def test_contractor_creates_own_profile(client, contractor_a):
    r = post(client, "/api/contractor/profile", VALID_PROFILE, **auth(contractor_a))

    assert r.status_code == 201, r.content
    body = r.json()

    assert body["business_name"] == "Sparkle Co"
    assert body["suburb"] == "Newtown"
    assert body["state"] == "NSW"
    assert body["postcode"] == "2042"
    assert body["country"] == "AU"
    assert body["user_id"] == str(contractor_a.id)

    # حُفظ فعلًا وارتبط بالمستخدم الصحيح
    profile = ContractorProfile.objects.get(pk=body["id"])
    assert profile.user_id == contractor_a.id


@pytest.mark.django_db
def test_new_profile_defaults_to_unavailable(client, contractor_a):
    """
    ⚠️ قاعدة صريحة: الملف الجديد لا يكون AVAILABLE تلقائيًا.
    """
    body = create_via_api(client, contractor_a)

    assert body["availability_status"] == AvailabilityStatus.UNAVAILABLE
    assert (
        ContractorProfile.objects.get(pk=body["id"]).availability_status
        == AvailabilityStatus.UNAVAILABLE
    )


@pytest.mark.django_db
def test_availability_cannot_be_set_at_creation(client, contractor_a):
    """محاولة بدء الملف متاحًا تُتجاهل — الإتاحة فعل صريح لاحق."""
    r = post(
        client,
        "/api/contractor/profile",
        {**VALID_PROFILE, "availability_status": "AVAILABLE"},
        **auth(contractor_a),
    )

    assert r.status_code == 201, r.content
    assert r.json()["availability_status"] == AvailabilityStatus.UNAVAILABLE


@pytest.mark.django_db
def test_admin_cannot_create_a_contractor_profile(client, db):
    """
    ⚠️ ADMIN حصري: الإدارة لا تعمل كمقاول.

    عُدِّل عمدًا: كان هذا الاختبار يشمل CUSTOMER أيضًا (حين كان الملف
    مقصورًا على دور CONTRACTOR). صار الزبون يستطيع الانضمام كعامل من
    حسابه — راجع tests/test_accounts_dual_role.py — فبقي المنع على
    الإدارة وحدها.
    """
    user = make_user("+61400004100", role=ConfirmedRole.ADMIN)

    r = post(client, "/api/contractor/profile", VALID_PROFILE, **auth(user))

    assert r.status_code == 403, r.content
    assert r.json()["code"] == "invalid_contractor_role"
    assert ContractorProfile.objects.count() == 0


@pytest.mark.django_db
def test_role_enforced_in_service_layer_not_only_api(db):
    """
    الإنفاذ يعيش في طبقة الخدمة: استدعاؤها مباشرة بدور ممنوع يُرفض أيضًا.
    """
    from apps.contractors.services import profile as svc

    admin = make_user("+61400004101", role=ConfirmedRole.ADMIN)

    with pytest.raises(svc.InvalidContractorRoleError):
        svc.create_profile(admin, business_name="X")

    assert ContractorProfile.objects.count() == 0


@pytest.mark.django_db
def test_second_profile_for_same_user_is_rejected(client, contractor_a):
    create_via_api(client, contractor_a)

    r = post(client, "/api/contractor/profile", VALID_PROFILE, **auth(contractor_a))

    assert r.status_code == 409, r.content
    assert ContractorProfile.objects.filter(user=contractor_a).count() == 1


@pytest.mark.django_db
def test_profile_can_be_created_with_minimal_fields(client, contractor_a):
    """كل الحقول اختيارية — الملف يبدأ ناقصًا ويُستكمل لاحقًا."""
    r = post(client, "/api/contractor/profile", {}, **auth(contractor_a))

    assert r.status_code == 201, r.content
    assert r.json()["business_name"] == ""
    assert r.json()["latitude"] is None


@pytest.mark.django_db
def test_invalid_postcode_is_rejected(client, contractor_a):
    r = post(
        client,
        "/api/contractor/profile",
        {**VALID_PROFILE, "postcode": "12"},
        **auth(contractor_a),
    )

    assert r.status_code == 422, r.content
    assert ContractorProfile.objects.count() == 0


@pytest.mark.django_db
def test_invalid_state_is_rejected(client, contractor_a):
    r = post(
        client,
        "/api/contractor/profile",
        {**VALID_PROFILE, "state": "XYZ"},
        **auth(contractor_a),
    )

    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_contractor_postcode_must_match_state(client, db):
    user = make_user("+61400004299")
    r = post(
        client,
        "/api/contractor/profile",
        {**VALID_PROFILE, "state": "VIC", "postcode": "2042"},
        **auth(user),
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_all_australian_states_accepted(client, db):
    from apps.contractors.models import AustralianState

    # رمز بريدي من الولاية نفسها — الرمز المتناقض مرفوض
    postcodes = {
        "NSW": "2000", "VIC": "3000", "QLD": "4000", "SA": "5000",
        "WA": "6000", "TAS": "7000", "NT": "0800", "ACT": "2600",
    }
    for i, state in enumerate(AustralianState.values):
        user = make_user(f"+6140000420{i}")
        r = post(
            client,
            "/api/contractor/profile",
            {**VALID_PROFILE, "state": state, "postcode": postcodes[state]},
            **auth(user),
        )
        assert r.status_code == 201, (state, r.content)


@pytest.mark.django_db
def test_coordinates_are_stored_verbatim_without_geocoding(client, contractor_a):
    """
    ⚠️ الإحداثيات حقول مخزَّنة تُملأ يدويًا — لا geocoding ولا adapter (§34/§4).
    """
    r = post(
        client,
        "/api/contractor/profile",
        {**VALID_PROFILE, "latitude": "-33.895000", "longitude": "151.179000"},
        **auth(contractor_a),
    )

    assert r.status_code == 201, r.content
    profile = ContractorProfile.objects.get(pk=r.json()["id"])
    assert profile.latitude == Decimal("-33.895000")
    assert profile.longitude == Decimal("151.179000")


# ============================================================
# 2) القراءة والتعديل الذاتيان
# ============================================================
@pytest.mark.django_db
def test_contractor_retrieves_own_profile(client, contractor_a):
    created = create_via_api(client, contractor_a)

    r = client.get("/api/contractor/profile", **auth(contractor_a))

    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


@pytest.mark.django_db
def test_retrieve_before_creation_returns_404(client, contractor_a):
    r = client.get("/api/contractor/profile", **auth(contractor_a))

    assert r.status_code == 404
    assert r.json()["code"] == "contractor_profile_not_found"


@pytest.mark.django_db
def test_contractor_updates_own_profile(client, contractor_a):
    create_via_api(client, contractor_a)

    r = patch(
        client,
        "/api/contractor/profile",
        {"business_name": "Shine Ltd", "suburb": "Carlton", "state": "VIC", "postcode": "3053"},
        **auth(contractor_a),
    )

    assert r.status_code == 200, r.content
    body = r.json()
    assert body["business_name"] == "Shine Ltd"
    assert body["suburb"] == "Carlton"
    assert body["state"] == "VIC"
    # الحقول غير المُرسلة لم تتغير
    assert body["street_address"] == "44 Trade St"


@pytest.mark.django_db
def test_patch_cannot_change_availability(client, contractor_a):
    """
    الجاهزية لها مسارها المخصّص — تعديلها عبر المسار العام مرفوض.
    """
    create_via_api(client, contractor_a)

    r = patch(
        client,
        "/api/contractor/profile",
        {"availability_status": "AVAILABLE"},
        **auth(contractor_a),
    )

    # الحقل ليس ضمن schema التعديل العام → يُتجاهل، والجاهزية تبقى كما هي
    assert r.status_code == 200, r.content
    assert r.json()["availability_status"] == AvailabilityStatus.UNAVAILABLE


@pytest.mark.django_db
def test_patch_cannot_change_country(client, contractor_a):
    create_via_api(client, contractor_a)

    patch(client, "/api/contractor/profile", {"country": "NZ"}, **auth(contractor_a))

    assert ContractorProfile.objects.get(user=contractor_a).country == "AU"


@pytest.mark.django_db
def test_patch_cannot_transfer_ownership(client, contractor_a, contractor_b):
    create_via_api(client, contractor_a)

    patch(
        client,
        "/api/contractor/profile",
        {"user_id": str(contractor_b.id), "business_name": "X"},
        **auth(contractor_a),
    )

    assert ContractorProfile.objects.get(user=contractor_a).user_id == contractor_a.id


# ============================================================
# 3) إنفاذ الملكية — مقاول لا يصل إلى ملف مقاول آخر
# ============================================================
@pytest.mark.django_db
def test_contractor_b_sees_only_own_profile(client, contractor_a, contractor_b):
    """
    المسارات الذاتية تشتقّ الملف من التوكن — لا معرّف يمكن استبداله.
    """
    a_profile = create_via_api(client, contractor_a, {**VALID_PROFILE, "business_name": "A Co"})
    create_via_api(client, contractor_b, {**VALID_PROFILE, "business_name": "B Co"})

    r = client.get("/api/contractor/profile", **auth(contractor_b))

    assert r.status_code == 200
    assert r.json()["business_name"] == "B Co"
    assert r.json()["id"] != a_profile["id"]


@pytest.mark.django_db
def test_contractor_b_edit_cannot_touch_contractor_a_profile(
    client, contractor_a, contractor_b
):
    create_via_api(client, contractor_a, {**VALID_PROFILE, "business_name": "A Co"})
    create_via_api(client, contractor_b, {**VALID_PROFILE, "business_name": "B Co"})

    patch(
        client,
        "/api/contractor/profile",
        {"business_name": "Hacked"},
        **auth(contractor_b),
    )

    # ملف A لم يُمس
    assert ContractorProfile.objects.get(user=contractor_a).business_name == "A Co"
    assert ContractorProfile.objects.get(user=contractor_b).business_name == "Hacked"


@pytest.mark.django_db
def test_contractor_cannot_reach_another_profile_via_admin_route(
    client, contractor_a, contractor_b
):
    """
    المسار الوحيد الذي يقبل معرّفًا هو مسار الإدارة — وهو محجوب عن المقاول.
    """
    a_profile = create_via_api(client, contractor_a)

    r = client.get(f"/api/admin/contractors/{a_profile['id']}", **auth(contractor_b))

    assert r.status_code == 403
    assert r.json()["code"] == "admin_role_required"


@pytest.mark.django_db
def test_ownership_enforced_in_service_layer(db, contractor_a, contractor_b):
    """
    فحص الملكية على مستوى الكائن — استدعاء مباشر بملف الغير يُرفض.
    """
    from apps.contractors.services import profile as svc

    profile_a = svc.create_profile(contractor_a, business_name="A Co")

    with pytest.raises(svc.ContractorProfilePermissionError):
        svc.assert_owns(contractor_b, profile_a)


# ============================================================
# 4) تبديل الجاهزية
# ============================================================
@pytest.mark.django_db
def test_contractor_toggles_availability_to_available(client, contractor_a):
    create_via_api(client, contractor_a)

    r = patch(
        client,
        "/api/contractor/profile/availability",
        {"availability_status": "AVAILABLE"},
        **auth(contractor_a),
    )

    assert r.status_code == 200, r.content
    assert r.json()["availability_status"] == AvailabilityStatus.AVAILABLE
    assert (
        ContractorProfile.objects.get(user=contractor_a).availability_status
        == AvailabilityStatus.AVAILABLE
    )


@pytest.mark.django_db
def test_availability_can_toggle_back_and_forth(client, contractor_a):
    """يُستدعى بكثرة — التبديل المتكرر سلوك عادي."""
    create_via_api(client, contractor_a)

    for expected in ("AVAILABLE", "UNAVAILABLE", "AVAILABLE", "UNAVAILABLE"):
        r = patch(
            client,
            "/api/contractor/profile/availability",
            {"availability_status": expected},
            **auth(contractor_a),
        )
        assert r.status_code == 200, r.content
        assert r.json()["availability_status"] == expected


@pytest.mark.django_db
def test_availability_endpoint_does_not_touch_other_fields(client, contractor_a):
    """المسار خفيف: يكتب حقل الجاهزية وحده."""
    create_via_api(client, contractor_a)

    patch(
        client,
        "/api/contractor/profile/availability",
        {"availability_status": "AVAILABLE"},
        **auth(contractor_a),
    )

    profile = ContractorProfile.objects.get(user=contractor_a)
    assert profile.business_name == "Sparkle Co"
    assert profile.suburb == "Newtown"
    assert profile.postcode == "2042"


@pytest.mark.django_db
def test_invalid_availability_value_is_rejected(client, contractor_a):
    create_via_api(client, contractor_a)

    r = patch(
        client,
        "/api/contractor/profile/availability",
        {"availability_status": "MAYBE"},
        **auth(contractor_a),
    )

    assert r.status_code == 422, r.content
    assert (
        ContractorProfile.objects.get(user=contractor_a).availability_status
        == AvailabilityStatus.UNAVAILABLE
    )


@pytest.mark.django_db
def test_availability_before_profile_exists_returns_404(client, contractor_a):
    r = patch(
        client,
        "/api/contractor/profile/availability",
        {"availability_status": "AVAILABLE"},
        **auth(contractor_a),
    )

    assert r.status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("role", [ConfirmedRole.CUSTOMER, ConfirmedRole.ADMIN])
def test_non_contractor_cannot_toggle_availability(client, db, role):
    user = make_user("+61400004300", role=role)

    r = patch(
        client,
        "/api/contractor/profile/availability",
        {"availability_status": "AVAILABLE"},
        **auth(user),
    )

    assert r.status_code == 403


@pytest.mark.django_db
def test_availability_is_a_separate_endpoint_from_profile_update(client, contractor_a):
    """
    قاعدة صريحة: مسار الجاهزية مستقل عن التعديل العام.
    """
    from config.urls import api

    paths = set(api.get_openapi_schema()["paths"])

    assert "/api/contractor/profile" in paths
    assert "/api/contractor/profile/availability" in paths


# ============================================================
# 5) وصول الإدارة
# ============================================================
@pytest.mark.django_db
def test_admin_lists_all_contractor_profiles(client, contractor_a, contractor_b, admin_user):
    create_via_api(client, contractor_a, {**VALID_PROFILE, "business_name": "A Co"})
    create_via_api(client, contractor_b, {**VALID_PROFILE, "business_name": "B Co"})

    r = client.get("/api/admin/contractors", **auth(admin_user))

    assert r.status_code == 200, r.content
    names = {p["business_name"] for p in r.json()}
    assert names == {"A Co", "B Co"}


@pytest.mark.django_db
def test_admin_list_includes_available_and_unavailable(
    client, contractor_a, contractor_b, admin_user
):
    """الترشيح بالجاهزية شأن Dispatch لاحقًا — قائمة الإدارة تعرض الكل."""
    create_via_api(client, contractor_a)
    create_via_api(client, contractor_b)
    patch(
        client,
        "/api/contractor/profile/availability",
        {"availability_status": "AVAILABLE"},
        **auth(contractor_a),
    )

    r = client.get("/api/admin/contractors", **auth(admin_user))
    statuses = {p["availability_status"] for p in r.json()}

    assert statuses == {"AVAILABLE", "UNAVAILABLE"}


@pytest.mark.django_db
def test_admin_list_is_empty_when_no_profiles(client, admin_user):
    r = client.get("/api/admin/contractors", **auth(admin_user))

    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.django_db
def test_admin_retrieves_any_profile_by_id(client, contractor_a, admin_user):
    created = create_via_api(client, contractor_a)

    r = client.get(f"/api/admin/contractors/{created['id']}", **auth(admin_user))

    assert r.status_code == 200, r.content
    assert r.json()["id"] == created["id"]
    assert r.json()["business_name"] == "Sparkle Co"


@pytest.mark.django_db
def test_admin_retrieve_unknown_id_returns_404(client, admin_user):
    r = client.get(f"/api/admin/contractors/{uuid.uuid4()}", **auth(admin_user))

    assert r.status_code == 404
    assert r.json()["code"] == "contractor_profile_not_found"


# ============================================================
# 6) غير الإدارة ممنوع من /api/admin/contractors/*
# ============================================================
@pytest.mark.django_db
def test_non_admin_forbidden_on_admin_contractor_endpoints(
    client, contractor_a, customer
):
    created = create_via_api(client, contractor_a)

    for label, actor in (("contractor", contractor_a), ("customer", customer)):
        listed = client.get("/api/admin/contractors", **auth(actor))
        one = client.get(f"/api/admin/contractors/{created['id']}", **auth(actor))

        assert listed.status_code == 403, f"{label} list -> {listed.status_code}"
        assert one.status_code == 403, f"{label} retrieve -> {one.status_code}"
        assert listed.json()["code"] == "admin_role_required"


@pytest.mark.django_db
def test_unauthenticated_requests_return_401(client, contractor_a):
    created = create_via_api(client, contractor_a)

    assert post(client, "/api/contractor/profile", VALID_PROFILE).status_code == 401
    assert client.get("/api/contractor/profile").status_code == 401
    assert patch(client, "/api/contractor/profile", {"business_name": "x"}).status_code == 401
    assert patch(
        client, "/api/contractor/profile/availability", {"availability_status": "AVAILABLE"}
    ).status_code == 401
    assert client.get("/api/admin/contractors").status_code == 401
    assert client.get(f"/api/admin/contractors/{created['id']}").status_code == 401


@pytest.mark.django_db
def test_invalid_token_returns_401(client):
    bad = {"HTTP_AUTHORIZATION": "Bearer not-a-token"}

    assert client.get("/api/contractor/profile", **bad).status_code == 401
    assert client.get("/api/admin/contractors", **bad).status_code == 401


# ============================================================
# 7) حدود النطاق والطبقات
# ============================================================
@pytest.mark.django_db
def test_response_does_not_leak_account_fields(client, contractor_a):
    body = create_via_api(client, contractor_a)
    text = json.dumps(body)

    assert body["user_id"] == str(contractor_a.id)
    assert contractor_a.phone not in text
    for leaked in ("role", "status", "is_staff", "password", "phone", "email"):
        assert leaked not in body


def test_api_layer_does_not_touch_orm_directly():
    """كل الوصول يمر عبر طبقة الخدمة — لا .objects في طبقة الـAPI."""
    import inspect

    from apps.contractors.api import admin_contractors, profile as profile_api

    for mod in (profile_api, admin_contractors):
        src = inspect.getsource(mod)
        assert ".objects." not in src, f"{mod.__name__} touches the ORM directly"
        assert "ContractorProfile(" not in src


def test_api_layer_does_not_reimplement_ownership_or_role_checks():
    """الفحوص تعيش في طبقة الخدمة ولا تُكرَّر في الـAPI."""
    import inspect

    from apps.contractors.api import admin_contractors, profile as profile_api

    for mod in (profile_api, admin_contractors):
        src = inspect.getsource(mod)
        assert "ConfirmedRole" not in src
        assert "user_id ==" not in src
        assert "role ==" not in src
        assert "role !=" not in src


def test_no_dispatch_or_adapter_coupling_in_this_phase():
    """
    ⚠️ هذه المرحلة تخزين فقط: لا منطق Dispatch/مسافة، ولا أي adapter
       (gps_distance / address_validation) — كلاهما تجريدي حسب §34/§4.
    """
    import ast
    import inspect

    from apps.contractors import models
    from apps.contractors.api import admin_contractors, profile as profile_api
    from apps.contractors.services import profile as profile_svc

    for mod in (models, profile_svc, profile_api, admin_contractors):
        tree = ast.parse(inspect.getsource(mod))

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)

        for name in imported:
            low = name.lower()
            assert "adapter" not in low, f"{mod.__name__} imports adapter: {name}"
            assert "gps" not in low, f"{mod.__name__} imports gps: {name}"
            assert "dispatch" not in low, f"{mod.__name__} imports dispatch: {name}"
            assert "booking" not in low, f"{mod.__name__} imports booking: {name}"

        identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for foreign in ("Dispatch", "Booking", "haversine", "geocode"):
            assert foreign not in identifiers, f"{mod.__name__} references {foreign}"


def test_contractors_app_defines_its_own_address_enum():
    """
    نسخة محلية مقصودة: choices تُضمَّن في الـmigrations، فاستيرادها من
    نطاق شقيق يخلق اعتماد migration عابرًا للنطاقات.
    """
    import inspect

    from apps.contractors import models

    src = inspect.getsource(models)

    assert "from apps.properties" not in src
    assert "class AustralianState" in src
