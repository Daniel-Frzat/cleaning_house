"""
طلبات الدعم — Support Domain (MVP)

يغطي:
  - الإنشاء بحجز وبدونه، وأن كل طلب يبدأ SUBMITTED.
  - 🔒 ربط حجز الغير يعيد 404 — لا يكشف وجوده.
  - القراءة: كلٌّ يرى طلباته، والإدارة ترى الجميع.
  - 🔒 طلب الغير يعيد 404 لا 403.
  - أن الدعم متاح لكل دور (عميل ومقاول وإدارة).
  - أن status لا يُقبل من العميل.
"""

import json
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.bookings.models import Booking
from apps.properties.models import Property, PropertyAddress, PropertyType
from apps.support.models import SupportCategory, SupportRequest, SupportStatus


# ------------------------------------------------------------
# أدوات
# ------------------------------------------------------------
def make_user(phone, role=ConfirmedRole.CUSTOMER):
    return User.objects.create_user(phone=phone, role=role)


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


def post(client, url, payload=None, **extra):
    return client.post(
        url, data=json.dumps(payload or {}), content_type="application/json", **extra
    )


def make_property(owner):
    prop = Property.objects.create(
        owner=owner, label="Home", property_type=PropertyType.HOUSE
    )
    PropertyAddress.objects.create(
        property=prop,
        street_address="12 Example St",
        suburb="Bondi",
        state="NSW",
        postcode="2026",
        latitude=Decimal("-33.868800"),
        longitude=Decimal("151.209300"),
    )
    return prop


def make_booking(customer):
    return Booking.objects.create(
        customer=customer,
        property=make_property(customer),
        scheduled_at=timezone.now() + timedelta(days=1),
        customer_timezone="Australia/Sydney",
    )


def body(category=SupportCategory.APP_ISSUE, message="The app crashes.", booking=None):
    payload = {"category": category, "message": message}
    if booking is not None:
        payload["booking_id"] = str(booking.id)
    return payload


# Fixtures — كتلة أرقام +614000140xx
@pytest.fixture
def client():
    return Client()


@pytest.fixture
def customer(db):
    return make_user("+61400014001")


@pytest.fixture
def other_customer(db):
    return make_user("+61400014002")


@pytest.fixture
def contractor_user(db):
    return make_user("+61400014003", role=ConfirmedRole.CONTRACTOR)


@pytest.fixture
def admin_user(db):
    return make_user("+61400014004", role=ConfirmedRole.ADMIN)


# ============================================================
# 1) الإنشاء
# ============================================================
@pytest.mark.django_db
def test_create_without_a_booking(client, customer):
    """📌 الطلب قد لا يخصّ حجزًا (عطل في التطبيق)."""
    r = post(client, "/api/support-requests", body(), **auth(customer))

    assert r.status_code == 201, r.content
    data = r.json()

    assert data["user_id"] == str(customer.id)
    assert data["booking_id"] is None
    assert data["category"] == SupportCategory.APP_ISSUE
    assert data["status"] == SupportStatus.SUBMITTED
    assert SupportRequest.objects.count() == 1


@pytest.mark.django_db
def test_create_with_own_booking(client, customer):
    booking = make_booking(customer)

    r = post(
        client,
        "/api/support-requests",
        body(category=SupportCategory.BOOKING_ISSUE, booking=booking),
        **auth(customer),
    )

    assert r.status_code == 201, r.content
    assert r.json()["booking_id"] == str(booking.id)


@pytest.mark.django_db
def test_every_request_starts_submitted(client, customer):
    """🔒 status لا يُقبل من العميل."""
    r = post(
        client,
        "/api/support-requests",
        {**body(), "status": SupportStatus.RESOLVED},
        **auth(customer),
    )

    assert r.status_code == 201, r.content
    assert r.json()["status"] == SupportStatus.SUBMITTED


@pytest.mark.django_db
def test_another_customers_booking_returns_404(client, customer, other_customer):
    """🔒 حجز الغير كغير الموجود — لا نكشف وجوده."""
    booking = make_booking(other_customer)

    r = post(
        client,
        "/api/support-requests",
        body(booking=booking),
        **auth(customer),
    )

    assert r.status_code == 404, r.content
    assert r.json()["code"] == "booking_not_found"
    assert SupportRequest.objects.count() == 0


@pytest.mark.django_db
def test_unknown_booking_returns_the_same_404(client, customer, other_customer):
    """🔒 الردّان متطابقان — لا فرق بين غير موجود وغير مملوك."""
    foreign = post(
        client,
        "/api/support-requests",
        body(booking=make_booking(other_customer)),
        **auth(customer),
    )
    unknown = client.post(
        "/api/support-requests",
        data=json.dumps({**body(), "booking_id": str(uuid.uuid4())}),
        content_type="application/json",
        **auth(customer),
    )

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()


@pytest.mark.django_db
def test_unknown_category_is_rejected(client, customer):
    r = post(
        client,
        "/api/support-requests",
        {"category": "NOT_A_CATEGORY", "message": "Hello."},
        **auth(customer),
    )

    assert r.status_code == 422, r.content
    assert SupportRequest.objects.count() == 0


@pytest.mark.django_db
def test_empty_message_is_rejected(client, customer):
    r = post(client, "/api/support-requests", body(message=""), **auth(customer))

    assert r.status_code == 422, r.content
    assert SupportRequest.objects.count() == 0


@pytest.mark.django_db
def test_overlong_message_is_rejected(client, customer):
    from apps.support.models import MESSAGE_MAX_LENGTH

    r = post(
        client,
        "/api/support-requests",
        body(message="x" * (MESSAGE_MAX_LENGTH + 1)),
        **auth(customer),
    )

    assert r.status_code == 422, r.content
    assert SupportRequest.objects.count() == 0


# ============================================================
# 2) الدعم متاح لكل دور
# ============================================================
@pytest.mark.django_db
def test_contractor_can_open_a_request(client, contractor_user):
    """📌 لا بوابة دور: المقاول يحتاج الدعم كما يحتاجه العميل."""
    r = post(
        client,
        "/api/support-requests",
        body(category=SupportCategory.PAYMENT_ISSUE),
        **auth(contractor_user),
    )

    assert r.status_code == 201, r.content


@pytest.mark.django_db
def test_unauthenticated_request_is_rejected(client):
    r = post(client, "/api/support-requests", body())

    assert r.status_code == 401, r.content
    assert SupportRequest.objects.count() == 0


# ============================================================
# 3) القراءة
# ============================================================
@pytest.mark.django_db
def test_list_shows_only_own_requests(client, customer, other_customer):
    post(client, "/api/support-requests", body(message="Mine."), **auth(customer))
    post(
        client,
        "/api/support-requests",
        body(message="Theirs."),
        **auth(other_customer),
    )

    r = client.get("/api/support-requests", **auth(customer))

    assert r.status_code == 200, r.content
    data = r.json()
    assert len(data) == 1
    assert data[0]["message"] == "Mine."


@pytest.mark.django_db
def test_admin_sees_every_request(client, customer, other_customer, admin_user):
    post(client, "/api/support-requests", body(message="A."), **auth(customer))
    post(client, "/api/support-requests", body(message="B."), **auth(other_customer))

    r = client.get("/api/support-requests", **auth(admin_user))

    assert r.status_code == 200, r.content
    assert len(r.json()) == 2


@pytest.mark.django_db
def test_retrieve_own_request(client, customer):
    created = post(client, "/api/support-requests", body(), **auth(customer)).json()

    r = client.get(f"/api/support-requests/{created['id']}", **auth(customer))

    assert r.status_code == 200, r.content
    assert r.json()["id"] == created["id"]


@pytest.mark.django_db
def test_retrieve_another_users_request_returns_404(
    client, customer, other_customer
):
    """🔒 404 لا 403 — لا نكشف وجود المورد لغير مالكه."""
    created = post(client, "/api/support-requests", body(), **auth(customer)).json()

    r = client.get(f"/api/support-requests/{created['id']}", **auth(other_customer))

    assert r.status_code == 404, r.content
    assert r.json()["code"] == "support_not_found"


@pytest.mark.django_db
def test_unknown_request_returns_the_same_404(client, customer, other_customer):
    created = post(client, "/api/support-requests", body(), **auth(customer)).json()

    foreign = client.get(
        f"/api/support-requests/{created['id']}", **auth(other_customer)
    )
    unknown = client.get(
        f"/api/support-requests/{uuid.uuid4()}", **auth(other_customer)
    )

    assert foreign.json() == unknown.json()


@pytest.mark.django_db
def test_admin_can_retrieve_any_request(client, customer, admin_user):
    created = post(client, "/api/support-requests", body(), **auth(customer)).json()

    r = client.get(f"/api/support-requests/{created['id']}", **auth(admin_user))

    assert r.status_code == 200, r.content


# ============================================================
# 4) قواعد بنيوية
# ============================================================
@pytest.mark.django_db
def test_model_rejects_a_foreign_booking(customer, other_customer):
    """🔒 الفحص في clean() كذلك — يحرس كل مسار كتابة لا الـAPI وحده."""
    from django.core.exceptions import ValidationError

    request = SupportRequest(
        user=customer,
        booking=make_booking(other_customer),
        category=SupportCategory.BOOKING_ISSUE,
        message="Not mine.",
    )

    with pytest.raises(ValidationError) as exc:
        request.full_clean()

    assert "booking" in exc.value.message_dict


def test_no_status_change_endpoint():
    """
    ⚠️ لا تغيير للحالة من مسارات المستخدم. الإدارة وحدها تغيّرها — من لوحة
       Django أو من /api/admin/support-requests (AdminJWTAuth).
    """
    from config.urls import api

    paths = api.get_openapi_schema()["paths"]

    for path, methods in paths.items():
        if "support-requests" in path and not path.startswith("/api/admin/"):
            assert set(methods) <= {"get", "post"}, f"{path} exposes {sorted(methods)}"


def test_api_layer_has_no_orm():
    """§43: طبقة الـAPI لا تستدعي الـORM."""
    import inspect

    from apps.support.api import support as api_mod

    src = inspect.getsource(api_mod)

    assert ".objects." not in src
    assert "SupportRequest(" not in src


def test_status_enum_has_no_conversation_states():
    """⚠️ لا CLOSED ولا REOPENED ولا WAITING_CUSTOMER — لا محادثة بعد."""
    values = set(SupportStatus.values)

    assert values == {"SUBMITTED", "UNDER_REVIEW", "RESOLVED"}
