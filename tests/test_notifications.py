"""
الإشعارات — تسجيل الأجهزة، القائمة، الإرسال عبر Firebase، أحداث العمل،
الرسائل العامة، والتنظيف.

⚠️ الأحداث تُطلق بعد نجاح المعاملة (on_commit)، فكل اختبار أحداث يلفّ
   الفعل بـdjango_capture_on_commit_callbacks(execute=True).
"""

import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest

from tests.helpers import start_job_for_tests
from django.test import Client
from django.utils import timezone

from adapters.push_notification import PushMessage, PushProviderError
from adapters.push_notification.fake import FakePushAdapter
from apps.accounts.models import User, UserStatus
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.audit.models import AuditLog
from apps.bookings.models import Booking, BookingStatus, DispatchStatus
from apps.bookings.services import offers as offers_svc
from apps.bookings.services.dispatch import assign_next_contractor
from apps.contractors.models import BusinessRegistration, VerificationStatus
from apps.contractors.services import verification as vsvc
from apps.jobs.services import jobs as jobs_svc
from apps.jobs.services import photos as photos_svc
from apps.notifications.models import (
    Broadcast,
    DeviceToken,
    Notification,
    PushStatus,
)
from apps.notifications.services import notifications as nsvc
from tests.helpers import JPEG_BYTES
from tests.test_audit_fixes import make_pending_booking
from tests.test_bookings_dispatch import (  # noqa: F401 — fixtures
    FAR,
    NEAR,
    customer,
    general,
    make_contractor,
    pricing,
    prop,
)


def post(client, url, payload=None, **extra):
    return client.post(url, data=json.dumps(payload or {}), content_type="application/json", **extra)


def bearer(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


def register(user, token="fcm-token-aaaaaaaaaa", platform="ANDROID"):
    return DeviceToken.objects.create(user=user, token=token, platform=platform)


@pytest.fixture
def client():
    return Client()


# ============================================================
# 1) تسجيل الأجهزة
# ============================================================
@pytest.mark.django_db
def test_device_register_is_idempotent_and_moves_between_accounts(client, customer):
    other = User.objects.create_user(phone="+61400900002")
    payload = {"token": "fcm-token-shared-123", "platform": "ios", "app_version": "1.2.0"}
    assert post(client, "/api/devices", payload, **bearer(customer)).status_code == 201
    assert post(client, "/api/devices", payload, **bearer(customer)).status_code == 200
    assert DeviceToken.objects.count() == 1

    # الجهاز نفسه دخل بحساب آخر → التوكن ينتقل ولا يستلم الأول إشعارات الثاني
    post(client, "/api/devices", payload, **bearer(other))
    assert DeviceToken.objects.get().user == other


@pytest.mark.django_db
def test_device_register_validates_platform(client, customer):
    r = post(client, "/api/devices", {"token": "fcm-token-xxxxxxxx", "platform": "nokia"}, **bearer(customer))
    assert r.status_code == 422


@pytest.mark.django_db
def test_unregister_and_logout_stop_pushes(client, customer):
    register(customer, "fcm-token-one-1234")
    assert post(client, "/api/devices/unregister", {"token": "fcm-token-one-1234"}, **bearer(customer)).status_code == 204
    assert not DeviceToken.objects.exists()

    register(customer, "fcm-token-two-1234")
    refresh = issue_tokens_for_user(customer)["refresh"]
    assert post(client, "/api/auth/logout", {"refresh": refresh, "device_token": "fcm-token-two-1234"}).status_code == 204
    assert not DeviceToken.objects.exists()


# ============================================================
# 2) القائمة داخل التطبيق
# ============================================================
@pytest.mark.django_db
def test_inbox_list_unread_count_and_marking(client, customer):
    for i in range(3):
        Notification.objects.create(user=customer, audience="CUSTOMER", type="t", title=f"n{i}", body="b")
    Notification.objects.create(user=customer, audience="CONTRACTOR", type="t", title="c", body="b")
    Notification.objects.create(user=customer, audience="ALL", type="broadcast", title="all", body="b")

    body = client.get("/api/notifications?audience=CUSTOMER", **bearer(customer)).json()
    assert body["count"] == 4  # 3 + العامة
    assert body["unread"] == 4
    first = body["items"][0]["id"]

    assert post(client, f"/api/notifications/{first}/read", **bearer(customer)).json()["read"] is True
    assert client.get("/api/notifications/unread-count", **bearer(customer)).json()["unread"] == 4  # كل الصفات
    assert post(client, "/api/notifications/read-all", **bearer(customer)).json()["marked"] == 4
    assert client.get("/api/notifications?unread_only=true", **bearer(customer)).json()["count"] == 0


@pytest.mark.django_db
def test_cannot_read_someone_elses_notification(client, customer):
    other = User.objects.create_user(phone="+61400900003")
    n = Notification.objects.create(user=other, audience="CUSTOMER", type="t", title="x", body="b")
    assert post(client, f"/api/notifications/{n.id}/read", **bearer(customer)).status_code == 404


# ============================================================
# 3) الإرسال
# ============================================================
@pytest.mark.django_db
def test_push_statuses(customer, settings, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        n = nsvc.notify(customer, "t", "CUSTOMER", "Hi", "Body")
    assert Notification.objects.get(pk=n.pk).push_status == PushStatus.NO_DEVICE

    register(customer, "fcm-token-good-123")
    register(customer, "invalid-dead-token")
    with django_capture_on_commit_callbacks(execute=True):
        n = nsvc.notify(customer, "t", "CUSTOMER", "Hi", "Body", data={"booking_id": "b1"})
    n.refresh_from_db()
    assert n.push_status == PushStatus.SENT
    assert not DeviceToken.objects.filter(token="invalid-dead-token").exists()
    sent = FakePushAdapter.sent[-1]["message"]
    assert sent.data["type"] == "t" and sent.data["notification_id"] == str(n.id)
    assert sent.data["booking_id"] == "b1"


@pytest.mark.django_db
def test_transient_failure_is_retried_then_marked_failed(customer, settings, django_capture_on_commit_callbacks):
    settings.NOTIFICATIONS_MAX_PUSH_ATTEMPTS = 2
    register(customer, "flaky-token-12345")
    with django_capture_on_commit_callbacks(execute=True):
        n = nsvc.notify(customer, "t", "CUSTOMER", "Hi", "Body")
    n.refresh_from_db()
    assert n.push_status == PushStatus.PENDING and n.push_attempts == 1

    Notification.objects.filter(pk=n.pk).update(created_at=timezone.now() - timedelta(minutes=10))
    assert nsvc.retry_pending() == 1
    n.refresh_from_db()
    assert n.push_status == PushStatus.FAILED and n.push_attempts == 2


@pytest.mark.django_db
def test_unconfigured_provider_never_breaks_the_caller(customer, settings, django_capture_on_commit_callbacks):
    settings.PUSH_ADAPTER = "adapters.push_notification.fcm.FCMPushAdapter"
    settings.FIREBASE_CREDENTIALS_JSON = ""
    settings.FIREBASE_CREDENTIALS_FILE = ""
    import adapters.push_notification.fcm as fcm

    fcm._credentials = None
    register(customer)
    with django_capture_on_commit_callbacks(execute=True):
        n = nsvc.notify(customer, "t", "CUSTOMER", "Hi", "Body")
    n.refresh_from_db()
    assert n.push_status == PushStatus.PENDING
    assert "provider" in n.push_error


# ============================================================
# 4) أحداث العمل
# ============================================================
def types_for(user):
    return list(Notification.objects.filter(user=user).order_by("created_at").values_list("type", flat=True))


@pytest.mark.django_db
def test_new_offer_notifies_contractor_with_high_priority(customer, prop, general, pricing, django_capture_on_commit_callbacks):
    user, _ = make_contractor("+61400900101", coords=NEAR)
    register(user)
    booking = make_pending_booking(customer, prop, general)
    with django_capture_on_commit_callbacks(execute=True):
        offer = assign_next_contractor(booking)
    n = Notification.objects.get(user=user)
    assert n.type == "offer.new" and n.priority == "HIGH" and n.audience == "CONTRACTOR"
    assert n.data["offer_id"] == str(offer.id)
    assert "Bondi" in n.body and "Respond within" in n.body
    assert FakePushAdapter.sent[-1]["message"].android_channel_id == "offers"


@pytest.mark.django_db
def test_no_notification_when_no_contractor_is_found(customer, prop, general, django_capture_on_commit_callbacks):
    """قرار PO: لا إشعار عند NO_CONTRACTOR."""
    booking = make_pending_booking(customer, prop, general)
    with django_capture_on_commit_callbacks(execute=True):
        assert assign_next_contractor(booking) is None
    booking.refresh_from_db()
    assert booking.dispatch_status == DispatchStatus.NO_CONTRACTOR
    assert not Notification.objects.exists()


@pytest.mark.django_db
def test_full_job_lifecycle_notifications(customer, prop, general, pricing, django_capture_on_commit_callbacks):
    user, _ = make_contractor("+61400900102", coords=NEAR)
    booking = make_pending_booking(customer, prop, general)
    with django_capture_on_commit_callbacks(execute=True):
        offer = assign_next_contractor(booking)
    with django_capture_on_commit_callbacks(execute=True):
        offers_svc.accept_offer(user, offer.id)
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CONFIRMED
    assert types_for(customer) == ["booking.confirmed"]
    assert types_for(user) == ["offer.new", "job.confirmed"]

    job = jobs_svc.get_job_by_booking_id(customer, booking.id)
    with django_capture_on_commit_callbacks(execute=True):
        start_job_for_tests(job, user)
    photos_svc.upload_job_photo(user, job.id, "BEFORE", JPEG_BYTES, "image/jpeg")
    photos_svc.upload_job_photo(user, job.id, "AFTER", JPEG_BYTES, "image/jpeg")
    with django_capture_on_commit_callbacks(execute=True):
        jobs_svc.mark_job_done(job, user)
    with django_capture_on_commit_callbacks(execute=True):
        jobs_svc.confirm_job_completion(job, customer)

    assert types_for(customer) == [
        "booking.confirmed", "job.arrived", "job.started", "job.awaiting_confirmation",
    ]
    assert types_for(user) == ["offer.new", "job.confirmed", "job.completed", "payout.sent"]


@pytest.mark.django_db
def test_failed_payment_notifies_customer(customer, prop, general, pricing, django_capture_on_commit_callbacks):
    from apps.payments.adapters import fake_adapter

    user, _ = make_contractor("+61400900103", coords=NEAR)
    booking = make_pending_booking(customer, prop, general)
    with django_capture_on_commit_callbacks(execute=True):
        offer = assign_next_contractor(booking)
    with mock.patch.object(fake_adapter, "FAILURE_SENTINEL_AMOUNT", offer.total_amount):
        with django_capture_on_commit_callbacks(execute=True):
            offers_svc.accept_offer(user, offer.id)
    assert types_for(customer) == ["payment.failed"]
    assert Notification.objects.get(user=customer).priority == "HIGH"


@pytest.mark.django_db
def test_verification_decisions_notify_contractor(django_capture_on_commit_callbacks):
    admin = User.objects.create_user(phone="+61400900110", email="a@example.com", role=ConfirmedRole.ADMIN)
    user, profile = make_contractor("+61400900111", eligible=False)
    reg = BusinessRegistration.objects.create(
        contractor=profile, abn="51824753556", business_name="Co", status=VerificationStatus.PENDING
    )
    with django_capture_on_commit_callbacks(execute=True):
        vsvc.review_business_registration(admin, reg.id, VerificationStatus.REJECTED, "Name mismatch")
    n = Notification.objects.get(user=user)
    assert n.type == "verification.rejected" and "Name mismatch" in n.body and "ABN" in n.body


@pytest.mark.django_db
def test_support_status_change_notifies_requester(client, customer, django_capture_on_commit_callbacks):
    from apps.support.models import SupportRequest

    admin = User.objects.create_user(phone="+61400900120", email="s@example.com", role=ConfirmedRole.ADMIN)
    req = SupportRequest.objects.create(user=customer, category="OTHER", message="help")
    with django_capture_on_commit_callbacks(execute=True):
        r = client.patch(f"/api/admin/support-requests/{req.id}", data={"status": "RESOLVED"},
                         content_type="application/json", **bearer(admin))
    assert r.status_code == 200, r.content
    n = Notification.objects.get(user=customer)
    assert n.type == "support.updated" and n.body == "Your request is now resolved."


@pytest.mark.django_db
def test_rolled_back_action_sends_nothing(customer, prop, general, django_capture_on_commit_callbacks):
    from django.db import transaction

    make_contractor("+61400900130", coords=NEAR)
    booking = make_pending_booking(customer, prop, general)
    with django_capture_on_commit_callbacks(execute=True):
        try:
            with transaction.atomic():
                assign_next_contractor(booking)
                raise RuntimeError("abort")
        except RuntimeError:
            pass
    assert not Notification.objects.exists()


# ============================================================
# 5) الرسائل العامة
# ============================================================
@pytest.mark.django_db
def test_admin_broadcast_to_customers(client, django_capture_on_commit_callbacks):
    admin = User.objects.create_user(phone="+61400900200", email="b@example.com", role=ConfirmedRole.ADMIN)
    c1 = User.objects.create_user(phone="+61400900201")
    c2 = User.objects.create_user(phone="+61400900202")
    suspended = User.objects.create_user(phone="+61400900203", status=UserStatus.SUSPENDED)
    worker, _ = make_contractor("+61400900204")
    register(c1)

    with django_capture_on_commit_callbacks(execute=True):
        r = post(client, "/api/admin/broadcasts",
                 {"target": "customers", "title": "Spring deal", "body": "20% off this week"}, **bearer(admin))
    assert r.status_code == 201, r.content
    assert r.json()["recipients_count"] == 2
    assert set(Notification.objects.values_list("user_id", flat=True)) == {c1.id, c2.id}
    assert not Notification.objects.filter(user__in=[suspended, worker, admin]).exists()
    assert AuditLog.objects.filter(action="broadcast.create").exists()

    detail = client.get(f"/api/admin/broadcasts/{r.json()['id']}", **bearer(admin)).json()
    assert detail["delivery"] == {"SENT": 1, "NO_DEVICE": 1, "read": 0}


@pytest.mark.django_db
def test_broadcast_requires_admin_and_valid_target(client, customer):
    assert post(client, "/api/admin/broadcasts", {"target": "CUSTOMERS", "title": "x", "body": "y"},
                **bearer(customer)).status_code == 403
    admin = User.objects.create_user(phone="+61400900210", email="v@example.com", role=ConfirmedRole.ADMIN)
    assert post(client, "/api/admin/broadcasts", {"target": "EVERYONE", "title": "x", "body": "y"},
                **bearer(admin)).status_code == 422


@pytest.mark.django_db
def test_admin_sees_a_users_delivery_log(client, customer):
    admin = User.objects.create_user(phone="+61400900220", email="l@example.com", role=ConfirmedRole.ADMIN)
    Notification.objects.create(user=customer, audience="CUSTOMER", type="t", title="x", body="b",
                                push_status=PushStatus.FAILED, push_error="503 UNAVAILABLE")
    body = client.get(f"/api/admin/users/{customer.id}/notifications", **bearer(admin)).json()
    assert body["count"] == 1 and body["items"][0]["push_error"] == "503 UNAVAILABLE"


# ============================================================
# 6) التنظيف
# ============================================================
@pytest.mark.django_db
def test_cleanup_removes_old_notifications_and_stale_devices(customer):
    old = Notification.objects.create(user=customer, audience="CUSTOMER", type="t", title="x", body="b")
    Notification.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=91))
    Notification.objects.create(user=customer, audience="CUSTOMER", type="t", title="y", body="b")
    stale = register(customer, "fcm-token-stale-12")
    DeviceToken.objects.filter(pk=stale.pk).update(last_seen_at=timezone.now() - timedelta(days=271))
    assert nsvc.cleanup() == {"notifications": 1, "devices": 1}
    assert Notification.objects.count() == 1


# ============================================================
# 7) محوّل FCM
# ============================================================
def _response(status, body=None):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = body or {}
    return r


def test_fcm_adapter_classifies_each_token(settings):
    import adapters.push_notification.fcm as fcm

    creds = mock.Mock(project_id="cleano-test", token="oauth", valid=True)
    responses = {
        "good": _response(200, {"name": "x"}),
        "dead": _response(404, {"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}}),
        "busy": _response(503, {"error": {"status": "UNAVAILABLE"}}),
    }
    with mock.patch.object(fcm, "_get_credentials", return_value=creds), \
         mock.patch("requests.Session.post", side_effect=lambda url, headers, json, timeout: responses[json["message"]["token"]]) as post_mock:
        result = fcm.FCMPushAdapter().send(
            ["good", "dead", "busy"], PushMessage(title="T", body="B", data={"k": 1}, priority="high", android_channel_id="offers")
        )
    assert result.delivered == ["good"]
    assert result.invalid == ["dead"]
    assert "busy" in result.failed
    sent = post_mock.call_args_list[0].kwargs["json"]["message"]
    assert sent["android"] == {"priority": "HIGH", "notification": {"channel_id": "offers"}}
    assert sent["data"] == {"k": "1"}
    assert "cleano-test" in post_mock.call_args_list[0].args[0]


def test_fcm_adapter_requires_credentials(settings):
    import adapters.push_notification.fcm as fcm

    fcm._credentials = None
    settings.FIREBASE_CREDENTIALS_JSON = ""
    settings.FIREBASE_CREDENTIALS_FILE = ""
    with pytest.raises(PushProviderError):
        fcm.FCMPushAdapter()
    settings.FIREBASE_CREDENTIALS_JSON = "{not json"
    with pytest.raises(PushProviderError):
        fcm.FCMPushAdapter()
