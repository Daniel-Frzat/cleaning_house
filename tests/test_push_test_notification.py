"""
طلب فريق الموبايل (2026-09-28): title/body داخل data، وإشعار تجريبي لمستخدم
واحد من لوحة الأدمن لفحص الإعداد على الإنتاج.
"""

import json

import pytest

from adapters.push_notification.fake import FakePushAdapter
from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.audit.models import AuditLog
from apps.notifications.services import notifications as nsvc
from tests.test_bookings_dispatch import auth


@pytest.fixture
def admin(db):
    return User.objects.create_user(phone="+61400099001", email="push@example.com", role=ConfirmedRole.ADMIN)


@pytest.fixture
def customer(db):
    return User.objects.create_user(phone="+61400099002")


def send(client, admin, user, **body):
    return client.post(
        f"/api/admin/users/{user.id}/test-notification", data=json.dumps(body),
        content_type="application/json", **auth(admin),
    )


@pytest.mark.django_db
def test_title_and_body_travel_inside_data_too(customer, django_capture_on_commit_callbacks):
    nsvc.register_device(customer, "android-token-123456", "ANDROID")
    with django_capture_on_commit_callbacks(execute=True):
        nsvc.notify(customer, "booking.confirmed", "CUSTOMER", "Cleaner confirmed", "Sam is on the way.",
                    data={"booking_id": "b-1"})
    message = FakePushAdapter.sent[-1]["message"]
    assert message.title == "Cleaner confirmed"  # كتلة notification كما هي
    assert message.data["title"] == "Cleaner confirmed"
    assert message.data["body"] == "Sam is on the way."
    assert message.data["type"] == "booking.confirmed" and message.data["booking_id"] == "b-1"


@pytest.mark.django_db
def test_test_notification_is_sent_now_and_reports_sent(client, admin, customer):
    nsvc.register_device(customer, "android-token-123456", "ANDROID")
    r = send(client, admin, customer, priority="HIGH")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["push_status"] == "SENT" and body["devices"] == 1 and body["type"] == "test"
    message = FakePushAdapter.sent[-1]["message"]
    assert message.android_channel_id == "offers" and message.priority == "high"
    assert AuditLog.objects.filter(action="notification.test").exists()


@pytest.mark.django_db
def test_normal_priority_uses_the_general_channel(client, admin, customer):
    nsvc.register_device(customer, "android-token-123456", "ANDROID")
    send(client, admin, customer)
    assert FakePushAdapter.sent[-1]["message"].android_channel_id == "general"


@pytest.mark.django_db
def test_no_device_is_reported(client, admin, customer):
    r = send(client, admin, customer)
    assert r.json()["push_status"] == "NO_DEVICE" and r.json()["devices"] == 0


@pytest.mark.django_db
def test_unknown_user_is_404(client, admin):
    r = client.post(
        "/api/admin/users/6f1c2f5e-0000-4000-8000-000000000000/test-notification",
        data=json.dumps({}), content_type="application/json", **auth(admin),
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_only_admins(client, customer):
    assert send(client, customer, customer).status_code in (401, 403)
