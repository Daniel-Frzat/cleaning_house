"""
الاسترداد بقرار الأدمن — كامل أو جزئي (قرار PO — 2026-09-27).
"""

import json
from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.payments.models import PaymentStatus
from tests.test_jobs_completion import auth, client, contractor, customer, job, service_type  # noqa: F401


@pytest.fixture
def admin(db):
    return User.objects.create_user(phone="+61400097001", email="ops@example.com", role=ConfirmedRole.ADMIN)


def refund(client, admin, payment, **body):
    return client.post(
        f"/api/admin/payments/{payment.id}/refund", data=json.dumps(body),
        content_type="application/json", **auth(admin),
    )


@pytest.mark.django_db
def test_full_refund_marks_refunded_and_notifies(client, admin, job, django_capture_on_commit_callbacks):
    payment = job.booking.payment
    with django_capture_on_commit_callbacks(execute=True):
        r = refund(client, admin, payment, reason="Kitchen was not cleaned")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == "REFUNDED"
    assert Decimal(body["refunded_amount"]) == payment.amount

    note = Notification.objects.get(user=job.booking.customer, type="payment.refunded")
    assert "$215.00" in note.body
    entry = AuditLog.objects.get(action="payment.refund")
    assert entry.details["amount"] == "215.00" and entry.details["reason"] == "Kitchen was not cleaned"


@pytest.mark.django_db
def test_partial_refunds_add_up_and_cannot_exceed_the_payment(client, admin, job):
    payment = job.booking.payment
    first = refund(client, admin, payment, amount="50.00", reason="Oven skipped")
    assert first.status_code == 200 and first.json()["status"] == "SUCCEEDED"
    second = refund(client, admin, payment, amount="100.00", reason="Windows skipped")
    assert Decimal(second.json()["refunded_amount"]) == Decimal("150.00")

    too_much = refund(client, admin, payment, amount="65.01", reason="x")
    assert too_much.status_code == 422 and too_much.json()["code"] == "invalid_refund_amount"

    rest = refund(client, admin, payment, reason="Close it")  # بلا مبلغ = المتبقي
    assert rest.json()["status"] == "REFUNDED" and Decimal(rest.json()["refunded_amount"]) == Decimal("215.00")
    again = refund(client, admin, payment, reason="x")
    assert again.status_code == 409 and again.json()["code"] == "not_refundable"


@pytest.mark.django_db
def test_customer_sees_the_refund(client, admin, job):
    refund(client, admin, job.booking.payment, amount="40.00", reason="Partial")
    customer = job.booking.customer
    payment = client.get(f"/api/bookings/{job.booking_id}/payment", **auth(customer)).json()
    assert Decimal(payment["refunded_amount"]) == Decimal("40.00") and payment["refunded_at"]
    summary = client.get(f"/api/bookings/{job.booking_id}", **auth(customer)).json()["payment"]
    assert Decimal(summary["refunded_amount"]) == Decimal("40.00")


@pytest.mark.django_db
def test_unpaid_payment_cannot_be_refunded(client, admin, job):
    payment = job.booking.payment
    payment.status = PaymentStatus.FAILED
    payment.save(update_fields=["status"])
    r = refund(client, admin, payment, reason="x")
    assert r.status_code == 409 and r.json()["code"] == "not_refundable"


@pytest.mark.django_db
def test_provider_refusal_changes_nothing(client, admin, job, monkeypatch):
    from apps.payments.adapters.base import ChargeOutcome, PaymentChargeResult
    from apps.payments.adapters.fake_adapter import FakePaymentAdapter

    monkeypatch.setattr(
        FakePaymentAdapter, "refund",
        lambda self, *a, **k: PaymentChargeResult(outcome=ChargeOutcome.FAILED, failure_reason="Card closed"),
    )
    payment = job.booking.payment
    r = refund(client, admin, payment, reason="x")
    assert r.status_code == 502 and r.json()["code"] == "refund_failed"
    payment.refresh_from_db()
    assert payment.refunded_amount == 0 and payment.status == PaymentStatus.SUCCEEDED


@pytest.mark.django_db
def test_only_admins_refund(client, job):
    customer = job.booking.customer
    r = refund(client, customer, job.booking.payment, reason="Give me my money")
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_reason_is_required(client, admin, job):
    r = refund(client, admin, job.booking.payment, reason="  ")
    assert r.status_code == 422
