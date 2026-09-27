"""
حذف الحساب بطلب صاحبه (قرار PO — 2026-09-27؛ شرط App Store و Google Play).

تجهيل لا حذف صفوف، ولا حذف وعمل جارٍ، وإلغاء تلقائي لما لم يُدفع.
"""

import json
from decimal import Decimal

import pytest

from apps.accounts.models import User, UserStatus
from apps.accounts.roles import ConfirmedRole
from apps.audit.models import AuditLog
from apps.bookings.models import BookingStatus, DispatchOffer, OfferCloseReason
from apps.contractors.models import AvailabilityStatus
from apps.notifications.services import notifications as notifications_svc
from apps.payments.models import Payment
from apps.properties.models import Property
from tests.test_audit_fixes import make_pending_booking
from tests.test_bookings_dispatch import auth, customer, general, make_contractor, pricing, prop  # noqa: F401
from tests.test_jobs import contractor, job, service_type  # noqa: F401
from tests.test_offer_close_reason import offer_for


def status(client, user):
    return client.get("/api/auth/me/deletion", **auth(user))


def delete(client, user, confirmation="DELETE"):
    return client.post(
        "/api/auth/me/delete", data=json.dumps({"confirmation": confirmation}),
        content_type="application/json", **auth(user),
    )


@pytest.mark.django_db
def test_customer_deletes_account_after_confirming(client, customer, prop):
    headers = auth(customer)
    phone, user_id = customer.phone, customer.id
    customer.full_name, customer.email = "Sam Smith", "sam@example.com"
    customer.save()
    notifications_svc.register_device(customer, "device-token-123456", "ANDROID")

    assert status(client, customer).json() == {"can_delete": True, "blockers": []}
    r = delete(client, customer)
    assert r.status_code == 204, r.content

    user = User.objects.get(pk=user_id)
    assert user.status == UserStatus.DELETED and user.deleted_at and not user.is_active
    assert user.phone.startswith("deleted:") and user.phone != phone
    assert user.email is None and user.full_name == ""
    assert not user.has_usable_password()
    assert not user.device_tokens.exists()
    assert not Property.objects.filter(owner=user, is_active=True).exists()

    # الجلسة الحالية تتوقف فورًا
    assert client.get("/api/auth/me", **headers).status_code == 401
    # الرقم نفسه يسجّل حسابًا جديدًا تمامًا
    fresh = User.objects.create_user(phone=phone)
    assert fresh.pk != user_id


@pytest.mark.django_db
@pytest.mark.parametrize("confirmation", ["", "delete", "yes"])
def test_wrong_or_missing_confirmation_changes_nothing(client, customer, confirmation):
    r = delete(client, customer, confirmation)
    assert r.status_code == 400 and r.json()["code"] == "deletion_confirmation_required"
    customer.refresh_from_db()
    assert customer.status == UserStatus.ACTIVE


@pytest.mark.django_db
def test_unpaid_request_is_cancelled_by_deletion(client, customer, prop, general, pricing):
    _cleaner, booking, offer = offer_for(customer, prop, general)
    assert delete(client, customer).status_code == 204

    booking.refresh_from_db()
    offer.refresh_from_db()
    assert booking.status == BookingStatus.CANCELLED
    assert offer.close_reason == OfferCloseReason.BOOKING_CANCELLED


@pytest.mark.django_db
def test_payment_in_progress_blocks_deletion(client, customer, prop, general):
    booking = make_pending_booking(customer, prop, general)
    Payment.objects.create(booking=booking, amount=Decimal("125.00"), method="CARD", status="PROCESSING")

    body = status(client, customer).json()
    assert body["can_delete"] is False
    assert body["blockers"] == [{"reason": "payment_in_progress", "booking_id": str(booking.id)}]

    r = delete(client, customer)
    assert r.status_code == 409 and r.json()["code"] == "account_deletion_blocked"
    customer.refresh_from_db()
    assert customer.status == UserStatus.ACTIVE


@pytest.mark.django_db
def test_clean_in_progress_blocks_both_sides(client, job, contractor):
    customer = job.booking.customer
    cleaner, _profile = contractor

    assert [b["reason"] for b in status(client, customer).json()["blockers"]] == ["active_booking"]
    assert delete(client, customer).status_code == 409
    assert [b["reason"] for b in status(client, cleaner).json()["blockers"]] == ["active_job"]
    assert delete(client, cleaner).status_code == 409


@pytest.mark.django_db
def test_cleaner_goes_offline_and_open_offers_move_on(client, customer, prop, general, pricing):
    cleaner, booking, offer = offer_for(customer, prop, general)
    backup, _ = make_contractor("+61400096002")

    assert delete(client, cleaner).status_code == 204

    offer.refresh_from_db()
    assert offer.close_reason == OfferCloseReason.DECLINED
    cleaner.refresh_from_db()
    assert cleaner.contractor_profile.availability_status == AvailabilityStatus.UNAVAILABLE
    # الحجز انتقل للمقاول التالي
    assert DispatchOffer.objects.filter(booking=booking, contractor__user=backup, status="PENDING").exists()


@pytest.mark.django_db
def test_admin_cannot_delete_from_the_app(client, db):
    admin = User.objects.create_user(phone="+61400096009", email="a@example.com", role=ConfirmedRole.ADMIN)
    assert status(client, admin).status_code == 403
    r = delete(client, admin)
    assert r.status_code == 403 and r.json()["code"] == "admin_account_not_deletable"


@pytest.mark.django_db
def test_deleted_account_cannot_be_reactivated_or_suspended(client, customer):
    admin = User.objects.create_user(phone="+61400096010", email="b@example.com", role=ConfirmedRole.ADMIN)
    assert delete(client, customer).status_code == 204

    r = client.post(f"/api/admin/users/{customer.id}/reactivate", **auth(admin))
    assert r.status_code == 409, r.content
    r = client.post(
        f"/api/admin/users/{customer.id}/suspend", data=json.dumps({"reason": "x"}),
        content_type="application/json", **auth(admin),
    )
    assert r.status_code == 409, r.content


@pytest.mark.django_db
def test_audit_log_keeps_no_phone_or_email(client, customer):
    phone = customer.phone
    AuditLog.objects.create(actor=customer, actor_label=phone, action="something.before")
    assert delete(client, customer).status_code == 204

    assert not AuditLog.objects.filter(actor_label=phone).exists()
    assert AuditLog.objects.filter(action="account.deleted", target_id=str(customer.id)).exists()
