"""
Back-office — دفعات العملاء والمقاولين، والمطابقة اليدوية (Superuser).

قواعد المطابقة: دفعة بنتيجة مجهولة فقط (PROCESSING/PENDING مع failure_reason
يبدأ بـprovider_error)، وإلا 409. SUCCEEDED يتطلب provider_reference ويُطلق
confirm_payment. الملاحظة إلزامية. كل مطابقة تُسجَّل في سجل التدقيق.
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.bookings.models import BookingStatus, DispatchOfferStatus, DispatchStatus
from apps.jobs.models import Job, JobStatus
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.payouts.models import Payout, PayoutStatus
from tests.backoffice import (
    auth,
    make_admin,
    make_booking,
    make_contractor,
    make_superuser,
    make_unknown_outcome_payment,
    make_user,
)
from tests.helpers import mark_paid


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin(db):
    return make_admin()


@pytest.fixture
def superuser(db):
    return make_superuser(email="root@example.com")


def post(client, url, body, **headers):
    return client.post(url, data=json.dumps(body), content_type="application/json", **headers)


def get(client, user, url):
    r = client.get(url, **auth(user))
    assert r.status_code == 200, r.content
    return r.json()


def confirmed_booking(price=Decimal("215.00")):
    _, profile = make_contractor()
    return make_booking(
        make_user(), status=BookingStatus.CONFIRMED, price=price, contractor=profile
    ), profile


# ============================================================
# Payments — قراءة
# ============================================================
@pytest.mark.django_db
def test_list_payments_and_filters(client, admin):
    _, profile = make_contractor()
    b_unknown, _, unknown = make_unknown_outcome_payment(make_user(), profile)
    paid_booking, _ = confirmed_booking()
    paid = mark_paid(paid_booking)
    failed_booking, _ = confirmed_booking()
    failed = Payment.objects.create(
        booking=failed_booking,
        amount=Decimal("215.00"),
        method=PaymentMethod.CARD,
        status=PaymentStatus.FAILED,
        failure_reason="card_declined",
        provider_error_code="card_declined",
    )
    failed.created_at = timezone.now() - timedelta(days=40)
    failed.save()
    # PROCESSING عادي (بلا استثناء من المزوّد) — لا يحتاج مطابقة
    normal_booking, _ = confirmed_booking()
    processing = Payment.objects.create(
        booking=normal_booking, amount=Decimal("215.00"), status=PaymentStatus.PROCESSING
    )

    body = get(client, admin, "/api/admin/payments")
    assert body["count"] == 4
    assert body["items"][-1]["id"] == str(failed.id)

    def ids(q):
        return {i["id"] for i in get(client, admin, f"/api/admin/payments?{q}")["items"]}

    assert ids("needs_reconciliation=true") == {str(unknown.id)}
    assert str(unknown.id) not in ids("needs_reconciliation=false")
    assert ids("status=PROCESSING") == {str(unknown.id), str(processing.id)}
    assert ids("status=SUCCEEDED") == {str(paid.id)}
    assert ids(f"booking_id={b_unknown.id}") == {str(unknown.id)}
    assert ids(f"created_to={timezone.localdate() - timedelta(days=30)}") == {str(failed.id)}

    row = next(
        i for i in get(client, admin, "/api/admin/payments")["items"] if i["id"] == str(unknown.id)
    )
    assert row["needs_reconciliation"] is True
    assert row["public_reference"] == b_unknown.public_reference

    page = get(client, admin, "/api/admin/payments?limit=3&offset=3")
    assert page["count"] == 4 and len(page["items"]) == 1


@pytest.mark.django_db
def test_payment_detail_has_all_diagnostic_fields(client, admin):
    booking, _ = confirmed_booking()
    payment = Payment.objects.create(
        booking=booking,
        amount=Decimal("215.00"),
        status=PaymentStatus.FAILED,
        provider_reference="pi_123",
        provider_error_code="insufficient_funds",
        failure_reason="Insufficient funds",
        attempt_number=3,
        method_summary={"type": "card", "card_brand": "visa"},
        action_payload={"client_secret": "shh"},
    )

    body = get(client, admin, f"/api/admin/payments/{payment.id}")

    assert body["provider_reference"] == "pi_123"
    assert body["provider_error_code"] == "insufficient_funds"
    assert body["failure_reason"] == "Insufficient funds"
    assert body["attempt_number"] == 3
    assert body["amount"] == "215.00"
    assert body["method_summary"] == {"type": "card", "card_brand": "visa"}
    assert body["has_pending_action"] is True
    # 🔒 بيانات 3-D Secure الخاصة بالعميل لا تُعرض
    assert "shh" not in json.dumps(body)


# ============================================================
# Payments — المطابقة
# ============================================================
@pytest.mark.django_db
def test_reconcile_payment_succeeded_confirms_booking(
    client, superuser, django_capture_on_commit_callbacks
):
    _, profile = make_contractor()
    booking, offer, payment = make_unknown_outcome_payment(make_user(), profile)

    with django_capture_on_commit_callbacks(execute=True):
        r = post(
            client,
            f"/api/admin/payments/{payment.id}/reconcile",
            {"outcome": "SUCCEEDED", "provider_reference": "pi_live_1", "note": "seen in dashboard"},
            **auth(superuser),
        )

    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == "SUCCEEDED"
    assert body["provider_reference"] == "pi_live_1"
    assert body["booking_confirmed"] is True
    assert body["needs_reconciliation"] is False
    assert body["paid_at"] is not None

    payment.refresh_from_db()
    booking.refresh_from_db()
    offer.refresh_from_db()
    assert payment.failure_reason is None
    assert booking.status == BookingStatus.CONFIRMED
    assert booking.assigned_contractor_id == profile.id
    assert booking.computed_price == Decimal("215.00")
    assert booking.dispatch_status == DispatchStatus.ASSIGNED
    assert offer.status == DispatchOfferStatus.ACCEPTED
    assert Job.objects.get(booking=booking).status == JobStatus.ASSIGNED

    entry = AuditLog.objects.get(action="payment.reconcile")
    assert entry.actor == superuser
    assert entry.target_type == "Payment"
    assert entry.target_id == str(payment.id)
    assert entry.details["outcome"] == "SUCCEEDED"
    assert entry.details["note"] == "seen in dashboard"
    assert entry.details["provider_reference"] == "pi_live_1"
    assert entry.details["previous_failure_reason"] == "provider_error: TimeoutError"
    assert entry.details["booking_confirmed"] is True


@pytest.mark.django_db
def test_reconcile_payment_succeeded_without_reserved_offer_keeps_money_fact(
    client, superuser
):
    """
    ⚠️ العرض لم يعد محجوزًا: الدفعة تبقى SUCCEEDED (المال تحرّك) ولا يُسنَد
       أحد — ما بعدها قرار مفتوح، والرد يكشفه.
    """
    _, profile = make_contractor()
    booking, offer, payment = make_unknown_outcome_payment(make_user(), profile)
    offer.status = DispatchOfferStatus.EXPIRED
    offer.save()

    r = post(
        client,
        f"/api/admin/payments/{payment.id}/reconcile",
        {"outcome": "SUCCEEDED", "provider_reference": "pi_2", "note": "late capture"},
        **auth(superuser),
    )

    assert r.status_code == 200, r.content
    assert r.json()["status"] == "SUCCEEDED"
    assert r.json()["booking_confirmed"] is False
    booking.refresh_from_db()
    assert booking.status == BookingStatus.PENDING
    assert booking.assigned_contractor_id is None
    entry = AuditLog.objects.get(action="payment.reconcile")
    assert entry.details["assignment_error"] == "offer_no_longer_reserved"


@pytest.mark.django_db
def test_reconcile_payment_failed(client, superuser):
    _, profile = make_contractor()
    booking, _, payment = make_unknown_outcome_payment(make_user(), profile)

    r = post(
        client,
        f"/api/admin/payments/{payment.id}/reconcile",
        {"outcome": "FAILED", "note": "provider says declined"},
        **auth(superuser),
    )

    assert r.status_code == 200, r.content
    payment.refresh_from_db()
    booking.refresh_from_db()
    assert payment.status == PaymentStatus.FAILED
    assert payment.failure_reason == "provider says declined"
    assert booking.status == BookingStatus.PENDING  # لا إلغاء تلقائي
    assert AuditLog.objects.get(action="payment.reconcile").details["outcome"] == "FAILED"


@pytest.mark.django_db
def test_reconcile_payment_succeeded_requires_provider_reference(client, superuser):
    _, profile = make_contractor()
    _, _, payment = make_unknown_outcome_payment(make_user(), profile)

    for ref in (None, "   "):
        body = {"outcome": "SUCCEEDED", "note": "n"}
        if ref is not None:
            body["provider_reference"] = ref
        r = post(client, f"/api/admin/payments/{payment.id}/reconcile", body, **auth(superuser))
        assert r.status_code == 422, r.content
        assert r.json()["code"] == "provider_reference_required"

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PROCESSING
    assert not AuditLog.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        {"outcome": "FAILED"},
        {"outcome": "FAILED", "note": ""},
        {"outcome": "FAILED", "note": "   "},
        {"outcome": "REFUNDED", "note": "x"},
        {"note": "x"},
    ],
)
def test_reconcile_payment_body_validation(client, superuser, body):
    _, profile = make_contractor()
    _, _, payment = make_unknown_outcome_payment(make_user(), profile)
    r = post(client, f"/api/admin/payments/{payment.id}/reconcile", body, **auth(superuser))
    assert r.status_code == 422


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status,reason",
    [
        (PaymentStatus.PROCESSING, None),  # جارية بلا استثناء — ليست مجهولة
        (PaymentStatus.PROCESSING, "card_declined"),
        (PaymentStatus.SUCCEEDED, "provider_error: TimeoutError"),
        (PaymentStatus.FAILED, "provider_error: TimeoutError"),
        (PaymentStatus.REQUIRES_ACTION, None),
    ],
)
def test_reconcile_payment_refused_when_outcome_not_unknown(client, superuser, status, reason):
    booking, _ = confirmed_booking()
    payment = Payment.objects.create(
        booking=booking, amount=Decimal("215.00"), status=status, failure_reason=reason
    )

    r = post(
        client,
        f"/api/admin/payments/{payment.id}/reconcile",
        {"outcome": "FAILED", "note": "x"},
        **auth(superuser),
    )

    assert r.status_code == 409, r.content
    assert r.json()["code"] == "not_reconcilable"
    payment.refresh_from_db()
    assert payment.status == status
    assert not AuditLog.objects.exists()


@pytest.mark.django_db
def test_reconcile_payment_twice_is_409(client, superuser):
    _, profile = make_contractor()
    _, _, payment = make_unknown_outcome_payment(make_user(), profile)
    url = f"/api/admin/payments/{payment.id}/reconcile"
    assert post(client, url, {"outcome": "FAILED", "note": "x"}, **auth(superuser)).status_code == 200
    r = post(client, url, {"outcome": "FAILED", "note": "x"}, **auth(superuser))
    assert r.status_code == 409
    assert AuditLog.objects.filter(action="payment.reconcile").count() == 1


@pytest.mark.django_db
def test_plain_admin_cannot_reconcile_payment(client, admin):
    _, profile = make_contractor()
    _, _, payment = make_unknown_outcome_payment(make_user(), profile)
    r = post(
        client,
        f"/api/admin/payments/{payment.id}/reconcile",
        {"outcome": "FAILED", "note": "x"},
        **auth(admin),
    )
    assert r.status_code == 403
    assert r.json()["code"] == "superuser_required"
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PROCESSING


# ============================================================
# Payouts
# ============================================================
def make_payout(status=PayoutStatus.PENDING, reason=None):
    booking, profile = confirmed_booking()
    mark_paid(booking)
    Job.objects.create(booking=booking, status=JobStatus.COMPLETED)
    payout = Payout.objects.create(
        booking=booking,
        contractor=profile.user,
        amount=booking.computed_price,
        status=status,
        failure_reason=reason,
    )
    return payout, profile


@pytest.mark.django_db
def test_list_payouts_and_filters(client, admin):
    unknown, p_unknown = make_payout(reason="provider_error: TimeoutError")
    ok, _ = make_payout(status=PayoutStatus.SUCCEEDED)
    failed, _ = make_payout(status=PayoutStatus.FAILED, reason="bank rejected")
    failed.created_at = timezone.now() - timedelta(days=15)
    failed.save()

    body = get(client, admin, "/api/admin/payouts")
    assert body["count"] == 3
    assert body["items"][-1]["id"] == str(failed.id)

    def ids(q):
        return {i["id"] for i in get(client, admin, f"/api/admin/payouts?{q}")["items"]}

    assert ids("needs_reconciliation=true") == {str(unknown.id)}
    assert ids("status=SUCCEEDED") == {str(ok.id)}
    assert ids(f"contractor_id={p_unknown.id}") == {str(unknown.id)}
    assert ids(f"created_to={timezone.localdate() - timedelta(days=10)}") == {str(failed.id)}
    assert ids(f"created_from={timezone.localdate()}") == {str(unknown.id), str(ok.id)}
    assert get(client, admin, "/api/admin/payouts?limit=1")["count"] == 3

    detail = get(client, admin, f"/api/admin/payouts/{unknown.id}")
    assert detail["needs_reconciliation"] is True
    assert detail["contractor_user_id"] == str(p_unknown.user_id)
    assert detail["amount"] == "215.00"


@pytest.mark.django_db
def test_reconcile_payout_succeeded(client, superuser):
    payout, _ = make_payout(reason="provider_error: TimeoutError")

    r = post(
        client,
        f"/api/admin/payouts/{payout.id}/reconcile",
        {"outcome": "SUCCEEDED", "provider_reference": "po_77", "note": "bank confirmed"},
        **auth(superuser),
    )

    assert r.status_code == 200, r.content
    payout.refresh_from_db()
    assert payout.status == PayoutStatus.SUCCEEDED
    assert payout.provider_reference == "po_77"
    assert payout.failure_reason is None
    entry = AuditLog.objects.get(action="payout.reconcile")
    assert entry.target_id == str(payout.id)
    assert entry.details["provider_reference"] == "po_77"
    assert entry.details["note"] == "bank confirmed"


@pytest.mark.django_db
def test_reconcile_payout_failed(client, superuser):
    payout, _ = make_payout(reason="provider_error: TimeoutError")

    r = post(
        client,
        f"/api/admin/payouts/{payout.id}/reconcile",
        {"outcome": "FAILED", "note": "returned by bank"},
        **auth(superuser),
    )

    assert r.status_code == 200, r.content
    payout.refresh_from_db()
    assert payout.status == PayoutStatus.FAILED
    assert payout.failure_reason == "returned by bank"
    assert AuditLog.objects.get(action="payout.reconcile").details["outcome"] == "FAILED"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status,reason",
    [
        (PayoutStatus.PENDING, None),
        (PayoutStatus.SUCCEEDED, "provider_error: X"),
        (PayoutStatus.FAILED, "provider_error: X"),
    ],
)
def test_reconcile_payout_refused(client, superuser, status, reason):
    payout, _ = make_payout(status=status, reason=reason)
    r = post(
        client,
        f"/api/admin/payouts/{payout.id}/reconcile",
        {"outcome": "FAILED", "note": "x"},
        **auth(superuser),
    )
    assert r.status_code == 409
    assert r.json()["code"] == "not_reconcilable"
    assert not AuditLog.objects.exists()


@pytest.mark.django_db
def test_reconcile_payout_requires_reference_and_note(client, superuser):
    payout, _ = make_payout(reason="provider_error: X")
    url = f"/api/admin/payouts/{payout.id}/reconcile"
    r = post(client, url, {"outcome": "SUCCEEDED", "note": "x"}, **auth(superuser))
    assert r.status_code == 422
    assert r.json()["code"] == "provider_reference_required"
    assert post(client, url, {"outcome": "FAILED"}, **auth(superuser)).status_code == 422


@pytest.mark.django_db
def test_plain_admin_cannot_reconcile_payout(client, admin):
    payout, _ = make_payout(reason="provider_error: X")
    r = post(
        client,
        f"/api/admin/payouts/{payout.id}/reconcile",
        {"outcome": "FAILED", "note": "x"},
        **auth(admin),
    )
    assert r.status_code == 403
    assert r.json()["code"] == "superuser_required"
