"""
Back-office — لوحة المؤشرات وسجل التدقيق (Superuser).
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services.audit import record
from apps.bookings.models import BookingStatus, DispatchStatus
from apps.contractors.models import BusinessRegistration, InsuranceDocument
from apps.jobs.models import Job, JobStatus
from apps.payments.models import Payment, PaymentStatus
from apps.payouts.models import Payout, PayoutStatus
from apps.support.models import SupportCategory, SupportRequest, SupportStatus
from tests.backoffice import (
    auth,
    make_admin,
    make_booking,
    make_contractor,
    make_superuser,
    make_unknown_outcome_payment,
    make_user,
)


@pytest.fixture
def client():
    return Client()


# ============================================================
# لوحة المؤشرات
# ============================================================
@pytest.mark.django_db
def test_dashboard_empty(client):
    r = client.get("/api/admin/dashboard/summary", **auth(make_admin()))
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["bookings"]["by_status"] == {"PENDING": 0, "CONFIRMED": 0, "CANCELLED": 0}
    assert body["bookings"]["by_dispatch_status"] == {
        "SEARCHING": 0,
        "NO_CONTRACTOR": 0,
        "ASSIGNED": 0,
    }
    assert body["gross_revenue"] == {"today": "0.00", "last_7_days": "0.00", "last_30_days": "0.00"}
    assert body["total_customers"] == 0
    assert body["total_contractors"] == 0


def _paid(price, paid_days_ago):
    _, profile = make_contractor(available=False)
    booking = make_booking(
        make_user(), status=BookingStatus.CONFIRMED, price=price, contractor=profile,
        dispatch_status=DispatchStatus.ASSIGNED,
    )
    return Payment.objects.create(
        booking=booking,
        amount=price,
        status=PaymentStatus.SUCCEEDED,
        paid_at=timezone.now() - timedelta(days=paid_days_ago),
    )


@pytest.mark.django_db
def test_dashboard_counts(client):
    admin = make_admin()
    # حجوزات
    make_booking(make_user())
    old = make_booking(make_user(), dispatch_status=DispatchStatus.NO_CONTRACTOR)
    old.created_at = timezone.now() - timedelta(days=10)
    old.save()
    # مقاولون: اثنان متاحان، وثائق معلّقة
    _, avail1 = make_contractor()
    _, avail2 = make_contractor()
    BusinessRegistration.objects.create(contractor=avail1, abn="51824753556", business_name="X")
    InsuranceDocument.objects.create(
        contractor=avail2, document_reference="P", expiry_date=timezone.localdate() + timedelta(days=90)
    )
    InsuranceDocument.objects.create(
        contractor=avail1, document_reference="P2", expiry_date=timezone.localdate() + timedelta(days=90)
    )
    # دفعات: اليوم، قبل 5 أيام، قبل 20 يومًا، قبل 60 يومًا
    _paid(Decimal("100.00"), 0)
    _paid(Decimal("50.25"), 5)
    _paid(Decimal("20.00"), 20)
    _paid(Decimal("999.00"), 60)
    # دفعة مجهولة النتيجة + دفعة فاشلة حديثة
    make_unknown_outcome_payment(make_user(), avail1)
    failed_booking = make_booking(make_user(), status=BookingStatus.CONFIRMED, price=Decimal("10.00"))
    Payment.objects.create(booking=failed_booking, amount=Decimal("10.00"), status=PaymentStatus.FAILED)
    # دفعات مقاولين
    for status, reason in (
        (PayoutStatus.PENDING, "provider_error: X"),
        (PayoutStatus.PENDING, None),
        (PayoutStatus.FAILED, "bank"),
    ):
        b = make_booking(make_user(), status=BookingStatus.CONFIRMED, price=Decimal("10.00"), contractor=avail2)
        Job.objects.create(booking=b, status=JobStatus.COMPLETED)
        Payout.objects.create(
            booking=b, contractor=avail2.user, amount=Decimal("10.00"), status=status, failure_reason=reason
        )
    # دعم: اثنان مفتوحان وواحد محلول
    u = make_user()
    for status in (SupportStatus.SUBMITTED, SupportStatus.UNDER_REVIEW, SupportStatus.RESOLVED):
        SupportRequest.objects.create(user=u, category=SupportCategory.OTHER, message="m", status=status)
    # حساب ثنائي الصفة يُعدّ في الاثنين
    make_user(is_contractor=True)

    body = client.get("/api/admin/dashboard/summary", **auth(admin)).json()

    bookings = body["bookings"]
    total_bookings = sum(bookings["by_status"].values())
    assert bookings["by_status"]["PENDING"] == 3  # اثنان + حجز الدفعة المجهولة
    assert bookings["by_dispatch_status"]["NO_CONTRACTOR"] == 1
    assert bookings["created_last_7_days"] == total_bookings - 1
    assert bookings["created_today"] == total_bookings - 1
    assert body["pending_verifications"] == {
        "business_registrations": 1,
        "insurance_documents": 2,
        "total": 3,
    }
    assert body["open_support_requests"] == 2
    assert body["payments"] == {"needs_reconciliation": 1, "failed_last_30_days": 1}
    assert body["payouts"] == {"pending": 2, "failed": 1, "needs_reconciliation": 1}
    assert body["gross_revenue"]["today"] == "100.00"
    assert body["gross_revenue"]["last_7_days"] == "150.25"
    assert body["gross_revenue"]["last_30_days"] == "170.25"
    assert body["contractors_available_now"] == 2
    # 6 مقاولين (2 متاحان + 4 من _paid) + الثنائي
    assert body["total_contractors"] == 7
    customers = body["total_customers"]
    assert customers >= 1 and isinstance(customers, int)


@pytest.mark.django_db
def test_dashboard_revenue_is_exact_decimal(client):
    for _ in range(3):
        _paid(Decimal("0.10"), 0)
    body = client.get("/api/admin/dashboard/summary", **auth(make_admin())).json()
    assert body["gross_revenue"]["today"] == "0.30"


# ============================================================
# سجل التدقيق
# ============================================================
@pytest.mark.django_db
def test_audit_log_lists_and_filters(client):
    su = make_superuser()
    other_admin = make_admin()
    target = make_user()
    e1 = record(other_admin, "user.suspend", target=target, details={"reason": "x"})
    e2 = record(su, "user.reactivate", target=target, details={})
    e3 = record(su, "pricing_config.update", details={"k": 1})
    AuditLog.objects.filter(pk=e3.pk).update(created_at=timezone.now() - timedelta(days=5))

    h = auth(su)
    body = client.get("/api/admin/audit-log", **h).json()
    assert body["count"] == 3
    assert [i["id"] for i in body["items"]] == [str(e2.id), str(e1.id), str(e3.id)]
    assert body["items"][1]["details"] == {"reason": "x"}
    assert body["items"][1]["actor_id"] == str(other_admin.id)

    def ids(q):
        r = client.get(f"/api/admin/audit-log?{q}", **h)
        assert r.status_code == 200, r.content
        return {i["id"] for i in r.json()["items"]}

    today = timezone.localdate()
    assert ids(f"actor_id={other_admin.id}") == {str(e1.id)}
    assert ids("action=user.reactivate") == {str(e2.id)}
    assert ids("target_type=User") == {str(e1.id), str(e2.id)}
    assert ids(f"target_id={target.id}") == {str(e1.id), str(e2.id)}
    assert ids(f"from={today}") == {str(e1.id), str(e2.id)}
    assert ids(f"to={today - timedelta(days=1)}") == {str(e3.id)}
    assert ids(f"from={today - timedelta(days=6)}&to={today - timedelta(days=4)}") == {str(e3.id)}

    page = client.get("/api/admin/audit-log?limit=1&offset=1", **h).json()
    assert page["count"] == 3 and [i["id"] for i in page["items"]] == [str(e1.id)]


@pytest.mark.django_db
def test_audit_log_reflects_real_mutation(client):
    su = make_superuser()
    user = make_user()
    client.post(
        f"/api/admin/users/{user.id}/suspend",
        data='{"reason": "chargeback"}',
        content_type="application/json",
        **auth(make_admin()),
    )
    body = client.get("/api/admin/audit-log?action=user.suspend", **auth(su)).json()
    assert body["count"] == 1
    assert body["items"][0]["target_id"] == str(user.id)


def test_audit_log_is_read_only():
    """🔒 السجل للإضافة فقط — لا مسار يعدّله أو يحذفه."""
    from config.urls import api

    paths = api.get_openapi_schema()["paths"]
    audit_paths = {p: set(m) for p, m in paths.items() if "audit" in p}
    assert audit_paths == {"/api/admin/audit-log": {"get"}}
