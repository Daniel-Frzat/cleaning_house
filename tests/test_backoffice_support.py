"""
Back-office — طلبات الدعم: القائمة والمرشِّحات والتفاصيل وانتقالات الحالة.
"""

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.support.models import SupportCategory, SupportRequest, SupportStatus
from tests.backoffice import auth, make_admin, make_booking, make_user


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin(db):
    return make_admin()


def make_request(user, message="Help me", category=SupportCategory.OTHER, booking=None, status=SupportStatus.SUBMITTED):
    return SupportRequest.objects.create(
        user=user, message=message, category=category, booking=booking, status=status
    )


def patch(client, url, body, **headers):
    return client.patch(url, data=json.dumps(body), content_type="application/json", **headers)


@pytest.mark.django_db
def test_list_support_requests_with_filters(client, admin):
    alice, bob = make_user(full_name="Alice"), make_user()
    booking = make_booking(alice)
    r1 = make_request(alice, "Cleaner never arrived", SupportCategory.BOOKING_ISSUE, booking)
    r2 = make_request(bob, "Charged twice", SupportCategory.PAYMENT_ISSUE, status=SupportStatus.UNDER_REVIEW)
    r3 = make_request(alice, "App crashes", SupportCategory.APP_ISSUE, status=SupportStatus.RESOLVED)
    r3.created_at = timezone.now() - timedelta(days=4)
    r3.save()

    h = auth(admin)
    body = client.get("/api/admin/support-requests", **h).json()
    assert body["count"] == 3
    assert [i["id"] for i in body["items"]] == [str(r2.id), str(r1.id), str(r3.id)]
    row = next(i for i in body["items"] if i["id"] == str(r1.id))
    assert row["booking_reference"] == booking.public_reference
    assert row["user_name"] == "Alice"

    def ids(q):
        r = client.get(f"/api/admin/support-requests?{q}", **h)
        assert r.status_code == 200, r.content
        return {i["id"] for i in r.json()["items"]}

    assert ids("status=UNDER_REVIEW") == {str(r2.id)}
    assert ids("category=BOOKING_ISSUE") == {str(r1.id)}
    assert ids(f"user_id={alice.id}") == {str(r1.id), str(r3.id)}
    assert ids(f"booking_id={booking.id}") == {str(r1.id)}
    assert ids("q=twice") == {str(r2.id)}
    assert ids(f"created_to={timezone.localdate() - timedelta(days=2)}") == {str(r3.id)}

    page = client.get("/api/admin/support-requests?limit=1&offset=2", **h).json()
    assert page["count"] == 3
    assert [i["id"] for i in page["items"]] == [str(r3.id)]


@pytest.mark.django_db
def test_admin_sees_any_users_request(client, admin):
    req = make_request(make_user(), "Private note")
    r = client.get(f"/api/admin/support-requests/{req.id}", **auth(admin))
    assert r.status_code == 200
    assert r.json()["message"] == "Private note"
    assert r.json()["status"] == "SUBMITTED"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    [
        ["UNDER_REVIEW", "RESOLVED"],
        ["RESOLVED"],
    ],
)
def test_allowed_transitions_are_audited(client, admin, path):
    req = make_request(make_user())
    url = f"/api/admin/support-requests/{req.id}"
    previous = "SUBMITTED"

    for target in path:
        r = patch(client, url, {"status": target}, **auth(admin))
        assert r.status_code == 200, r.content
        assert r.json()["status"] == target
        entry = AuditLog.objects.filter(action="support_request.status").order_by("-created_at").first()
        assert entry.details == {"from": previous, "to": target}
        assert entry.target_id == str(req.id)
        assert entry.target_type == "SupportRequest"
        assert entry.actor == admin
        previous = target

    assert AuditLog.objects.filter(action="support_request.status").count() == len(path)


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["SUBMITTED", "UNDER_REVIEW", "RESOLVED"])
def test_resolved_is_final(client, admin, target):
    req = make_request(make_user(), status=SupportStatus.RESOLVED)
    r = patch(client, f"/api/admin/support-requests/{req.id}", {"status": target}, **auth(admin))
    assert r.status_code == 409
    assert r.json()["code"] == "request_resolved"
    req.refresh_from_db()
    assert req.status == SupportStatus.RESOLVED
    assert not AuditLog.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "start,target",
    [
        (SupportStatus.UNDER_REVIEW, "SUBMITTED"),
        (SupportStatus.UNDER_REVIEW, "UNDER_REVIEW"),
        (SupportStatus.SUBMITTED, "SUBMITTED"),
    ],
)
def test_invalid_transitions_are_409(client, admin, start, target):
    req = make_request(make_user(), status=start)
    r = patch(client, f"/api/admin/support-requests/{req.id}", {"status": target}, **auth(admin))
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_status_transition"
    req.refresh_from_db()
    assert req.status == start
    assert not AuditLog.objects.exists()


@pytest.mark.django_db
def test_unknown_status_value_is_422(client, admin):
    req = make_request(make_user())
    r = patch(client, f"/api/admin/support-requests/{req.id}", {"status": "CLOSED"}, **auth(admin))
    assert r.status_code == 422


@pytest.mark.django_db
def test_owner_still_cannot_change_status(client):
    """🔒 مسار المستخدم يبقى بلا تغيير حالة — والمسار الإداري يرفضه."""
    user = make_user()
    req = make_request(user)
    r = patch(client, f"/api/admin/support-requests/{req.id}", {"status": "RESOLVED"}, **auth(user))
    assert r.status_code == 403
    req.refresh_from_db()
    assert req.status == SupportStatus.SUBMITTED
