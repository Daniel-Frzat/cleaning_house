"""
Back-office — بوابات الوصول لكل مسار إداري جديد.

يغطي لكل مسار: بلا توكن 401، العميل والمقاول 403 admin_required، مسارات
الـSuperuser ترفض الأدمن العادي 403 superuser_required، معرّف مشوّه 422،
ومعرّف غير موجود 404.
"""

import json
import uuid

import pytest
from django.test import Client

from tests.backoffice import auth, make_admin, make_contractor, make_superuser, make_user

X = "00000000-0000-4000-8000-000000000000"

# (method, url, body)
ADMIN_LISTS = [
    ("get", "/api/admin/users", None),
    ("get", "/api/admin/properties", None),
    ("get", "/api/admin/bookings", None),
    ("get", "/api/admin/jobs", None),
    ("get", "/api/admin/payments", None),
    ("get", "/api/admin/payouts", None),
    ("get", "/api/admin/support-requests", None),
    ("get", "/api/admin/dashboard/summary", None),
]

RECONCILE_BODY = {"outcome": "FAILED", "note": "checked with provider"}

# مسارات بمعرّف: (method, url-template, body, expected 404 code)
ADMIN_BY_ID = [
    ("get", "/api/admin/users/{id}", None, "user_not_found"),
    ("post", "/api/admin/users/{id}/suspend", {"reason": "fraud"}, "user_not_found"),
    ("post", "/api/admin/users/{id}/reactivate", None, "user_not_found"),
    ("get", "/api/admin/properties/{id}", None, "property_not_found"),
    ("get", "/api/admin/bookings/{id}", None, "booking_not_found"),
    ("get", "/api/admin/jobs/{id}", None, "job_not_found"),
    ("get", "/api/admin/payments/{id}", None, "payment_not_found"),
    ("get", "/api/admin/payouts/{id}", None, "payout_not_found"),
    ("get", "/api/admin/support-requests/{id}", None, "support_not_found"),
    ("patch", "/api/admin/support-requests/{id}", {"status": "RESOLVED"}, "support_not_found"),
    (
        "get",
        "/api/admin/contractors/{id}/verifications",
        None,
        "contractor_profile_not_found",
    ),
]

SUPERUSER_BY_ID = [
    ("post", "/api/admin/payments/{id}/reconcile", RECONCILE_BODY, "payment_not_found"),
    ("post", "/api/admin/payouts/{id}/reconcile", RECONCILE_BODY, "payout_not_found"),
]

ALL_ADMIN = ADMIN_LISTS + [(m, u.format(id=X), b) for m, u, b, _ in ADMIN_BY_ID]
ALL_SUPERUSER = [("get", "/api/admin/audit-log", None)] + [
    (m, u.format(id=X), b) for m, u, b, _ in SUPERUSER_BY_ID
]


def call(client, method, url, body=None, **headers):
    if body is None:
        return getattr(client, method)(url, **headers)
    return getattr(client, method)(
        url, data=json.dumps(body), content_type="application/json", **headers
    )


@pytest.fixture
def client():
    return Client()


@pytest.mark.django_db
@pytest.mark.parametrize("method,url,body", ALL_ADMIN + ALL_SUPERUSER)
def test_anonymous_is_rejected(client, method, url, body):
    assert call(client, method, url, body).status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("method,url,body", ALL_ADMIN + ALL_SUPERUSER)
@pytest.mark.parametrize("who", ["customer", "contractor"])
def test_non_admin_gets_admin_required(client, method, url, body, who):
    user = make_user() if who == "customer" else make_contractor()[0]
    r = call(client, method, url, body, **auth(user))
    assert r.status_code == 403, r.content
    assert r.json() == {
        "code": "admin_required",
        "detail": "Only administrators can access this endpoint.",
    }


@pytest.mark.django_db
def test_dual_role_customer_contractor_is_not_admin(client):
    user = make_user(is_contractor=True)
    r = client.get("/api/admin/users", **auth(user))
    assert r.status_code == 403
    assert r.json()["code"] == "admin_required"


@pytest.mark.django_db
@pytest.mark.parametrize("method,url,body", ALL_SUPERUSER)
def test_plain_admin_gets_superuser_required(client, method, url, body):
    r = call(client, method, url, body, **auth(make_admin()))
    assert r.status_code == 403, r.content
    assert r.json()["code"] == "superuser_required"


@pytest.mark.django_db
@pytest.mark.parametrize("method,url,body", ALL_ADMIN)
def test_superuser_may_use_admin_routes(client, method, url, body):
    r = call(client, method, url, body, **auth(make_superuser()))
    assert r.status_code in (200, 404), r.content


@pytest.mark.django_db
@pytest.mark.parametrize("method,template,body,code", ADMIN_BY_ID)
def test_unknown_id_is_404(client, method, template, body, code):
    r = call(client, method, template.format(id=uuid.uuid4()), body, **auth(make_admin()))
    assert r.status_code == 404, r.content
    assert r.json()["code"] == code


@pytest.mark.django_db
@pytest.mark.parametrize("method,template,body,code", SUPERUSER_BY_ID)
def test_unknown_id_is_404_on_superuser_routes(client, method, template, body, code):
    r = call(client, method, template.format(id=uuid.uuid4()), body, **auth(make_superuser()))
    assert r.status_code == 404, r.content
    assert r.json()["code"] == code


@pytest.mark.django_db
@pytest.mark.parametrize(
    "method,template,body,superuser",
    [(m, u, b, False) for m, u, b, _ in ADMIN_BY_ID]
    + [(m, u, b, True) for m, u, b, _ in SUPERUSER_BY_ID],
)
def test_malformed_id_is_422(client, method, template, body, superuser):
    user = make_superuser() if superuser else make_admin()
    r = call(client, method, template.format(id="NOT-A-UUID"), body, **auth(user))
    assert r.status_code == 422, r.content


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url",
    [
        "/api/admin/bookings?customer_id=nope",
        "/api/admin/bookings?contractor_id=nope",
        "/api/admin/properties?owner_id=nope",
        "/api/admin/payments?booking_id=nope",
        "/api/admin/payouts?contractor_id=nope",
        "/api/admin/support-requests?user_id=nope",
        "/api/admin/jobs?contractor_id=nope",
        "/api/admin/bookings?status=NOPE",
        "/api/admin/users?limit=0",
        "/api/admin/users?limit=201",
        "/api/admin/users?offset=-1",
        "/api/admin/bookings?created_from=yesterday",
    ],
)
def test_malformed_filters_are_422(client, url):
    assert client.get(url, **auth(make_admin())).status_code == 422


@pytest.mark.django_db
def test_suspended_admin_is_rejected(client):
    from apps.accounts.models import UserStatus

    admin = make_admin()
    headers = auth(admin)
    admin.status = UserStatus.SUSPENDED
    admin.save()
    assert client.get("/api/admin/users", **headers).status_code == 401


def test_admin_api_layer_has_no_orm():
    """§43: طبقة الـAPI الإدارية لا تستدعي الـORM."""
    import inspect

    from apps.accounts.api import backoffice_users
    from apps.audit.api import admin as audit_admin
    from apps.audit.api import common
    from apps.bookings.api import admin as bookings_admin
    from apps.jobs.api import admin as jobs_admin
    from apps.payments.api import admin as payments_admin
    from apps.payouts.api import admin as payouts_admin
    from apps.properties.api import admin as properties_admin
    from apps.support.api import admin as support_admin

    for mod in (
        backoffice_users,
        audit_admin,
        common,
        bookings_admin,
        jobs_admin,
        payments_admin,
        payouts_admin,
        properties_admin,
        support_admin,
    ):
        src = inspect.getsource(mod)
        assert ".objects" not in src, mod.__name__
        assert "role ==" not in src, mod.__name__
