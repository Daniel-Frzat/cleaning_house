"""
ما بعد التنظيف (قرارات PO — 2026-09-27): تقييم إلزامي، ضمان إعادة تنظيف
72 ساعة بقرار الأدمن، فاتورة INV-YYYY-NNNNNN بلا GST.
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.aftercare.models import Invoice, RecleanStatus
from apps.jobs.models import Job
from apps.jobs.services import jobs as jobs_svc
from apps.notifications.models import Notification
from apps.properties.models import PropertyAddress
from tests.test_jobs_completion import (  # noqa: F401
    add_both_photos, auth, client, contractor, customer, job, service_type,
)


def post(client, url, body, user):
    return client.post(url, data=json.dumps(body), content_type="application/json", **auth(user))


@pytest.fixture
def completed(job, contractor):
    user, _ = contractor
    add_both_photos(user, job)
    jobs_svc.mark_job_done(job, user)
    jobs_svc.confirm_job_completion(job, job.booking.customer)
    job.refresh_from_db()
    return job


@pytest.fixture
def admin(db):
    return User.objects.create_user(phone="+61400098001", email="ops2@example.com", role=ConfirmedRole.ADMIN)


# ------------------------------------------------------------ التقييم
@pytest.mark.django_db
def test_rating_needs_a_completed_clean(client, job):
    r = post(client, f"/api/bookings/{job.booking_id}/review", {"stars": 5}, job.booking.customer)
    assert r.status_code == 409 and r.json()["code"] == "review_not_allowed"


@pytest.mark.django_db
def test_customer_rates_once_and_the_cleaner_is_told(client, completed, contractor, django_capture_on_commit_callbacks):
    customer = completed.booking.customer
    booking_url = f"/api/bookings/{completed.booking_id}"
    assert client.get(booking_url, **auth(customer)).json()["review_required"] is True

    with django_capture_on_commit_callbacks(execute=True):
        r = post(client, f"{booking_url}/review", {"stars": 4, "comment": "Great job"}, customer)
    assert r.status_code == 201, r.content
    body = client.get(booking_url, **auth(customer)).json()
    assert body["review_required"] is False and body["review"]["stars"] == 4

    again = post(client, f"{booking_url}/review", {"stars": 1}, customer)
    assert again.status_code == 409 and again.json()["code"] == "already_reviewed"

    cleaner, _ = contractor
    assert Notification.objects.filter(user=cleaner, type="review.received").exists()
    profile = client.get("/api/contractor/profile", **auth(cleaner)).json()
    assert Decimal(str(profile["rating_average"])) == Decimal("4.00") and profile["rating_count"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("stars", [0, 6])
def test_stars_must_be_1_to_5(client, completed, stars):
    r = post(client, f"/api/bookings/{completed.booking_id}/review", {"stars": stars}, completed.booking.customer)
    assert r.status_code == 422


@pytest.mark.django_db
def test_rating_is_mandatory_before_a_new_request(client, completed, service_type):
    customer = completed.booking.customer
    prop = completed.booking.property
    PropertyAddress.objects.create(
        property=prop, street_address="1 Test St", suburb="Bondi", state="NSW", postcode="2026",
        latitude=Decimal("-33.868800"), longitude=Decimal("151.209300"),
    )
    new_request = {
        "property_id": str(prop.id),
        "service_selections": [{"service_type_id": str(service_type.id), "room_count": 1}],
    }
    blocked = post(client, "/api/bookings", new_request, customer)
    assert blocked.status_code == 409 and blocked.json()["code"] == "review_required"
    assert completed.booking.public_reference in blocked.json()["detail"]

    post(client, f"/api/bookings/{completed.booking_id}/review", {"stars": 5}, customer)
    assert post(client, "/api/bookings", new_request, customer).status_code == 201


# ------------------------------------------------------------ إعادة التنظيف
@pytest.mark.django_db
def test_reclean_only_for_covered_services(client, completed):
    customer = completed.booking.customer
    r = post(client, f"/api/bookings/{completed.booking_id}/reclean-requests", {"areas": ["KITCHEN"]}, customer)
    assert r.status_code == 409 and r.json()["code"] == "reclean_not_eligible"
    booking = client.get(f"/api/bookings/{completed.booking_id}", **auth(customer)).json()
    assert booking["reclean_eligible_until"] is None


@pytest.mark.django_db
def test_reclean_within_72_hours_then_admin_decides(
    client, completed, service_type, admin, django_capture_on_commit_callbacks
):
    service_type.reclean_guarantee = True
    service_type.save()
    customer = completed.booking.customer
    url = f"/api/bookings/{completed.booking_id}/reclean-requests"

    booking = client.get(f"/api/bookings/{completed.booking_id}", **auth(customer)).json()
    assert booking["reclean_eligible_until"] is not None

    r = post(client, url, {"areas": ["KITCHEN", "WINDOWS"], "details": "Oven still greasy"}, customer)
    assert r.status_code == 201, r.content
    request_id = r.json()["id"]
    second = post(client, url, {"areas": ["OTHER"]}, customer)
    assert second.status_code == 409 and second.json()["code"] == "reclean_already_open"

    listed = client.get("/api/admin/reclean-requests?status=SUBMITTED", **auth(admin)).json()
    assert listed["count"] == 1
    with django_capture_on_commit_callbacks(execute=True):
        decided = client.patch(
            f"/api/admin/reclean-requests/{request_id}",
            data=json.dumps({"status": "APPROVED", "note": "Booked for tomorrow 10am"}),
            content_type="application/json", **auth(admin),
        )
    assert decided.status_code == 200 and decided.json()["status"] == RecleanStatus.APPROVED
    note = Notification.objects.get(user=customer, type="reclean.updated")
    assert "Booked for tomorrow 10am" in note.body

    # لا حد للعدد: بعد القرار يمكن طلب جديد
    assert post(client, url, {"areas": ["BATHROOMS"]}, customer).status_code == 201
    again = client.patch(
        f"/api/admin/reclean-requests/{request_id}", data=json.dumps({"status": "REJECTED"}),
        content_type="application/json", **auth(admin),
    )
    assert again.status_code == 409 and again.json()["code"] == "reclean_already_decided"


@pytest.mark.django_db
def test_reclean_window_closes_after_72_hours(client, completed, service_type):
    service_type.reclean_guarantee = True
    service_type.save()
    Job.objects.filter(pk=completed.pk).update(confirmed_at=timezone.now() - timedelta(hours=73))
    r = post(
        client, f"/api/bookings/{completed.booking_id}/reclean-requests", {"areas": ["KITCHEN"]},
        completed.booking.customer,
    )
    assert r.status_code == 409 and r.json()["code"] == "reclean_window_closed"


@pytest.mark.django_db
def test_reclean_requires_a_valid_area(client, completed, service_type):
    service_type.reclean_guarantee = True
    service_type.save()
    r = post(
        client, f"/api/bookings/{completed.booking_id}/reclean-requests", {"areas": ["GARAGE"]},
        completed.booking.customer,
    )
    assert r.status_code == 422


# ------------------------------------------------------------ الفاتورة
@pytest.mark.django_db
def test_invoice_number_is_sequential_and_stable(client, completed, admin):
    customer = completed.booking.customer
    url = f"/api/bookings/{completed.booking_id}/invoice"
    first = client.get(url, **auth(customer))
    assert first.status_code == 200, first.content
    body = first.json()
    assert body["number"].startswith("INV-") and body["number"].endswith("-000001")
    assert client.get(url, **auth(customer)).json()["number"] == body["number"]
    admin_view = client.get(f"/api/admin/bookings/{completed.booking_id}/invoice", **auth(admin)).json()
    assert admin_view["number"] == body["number"]
    assert Invoice.objects.count() == 1

    total = sum(Decimal(line["amount"]) for line in body["lines"])
    assert total == Decimal(body["total"]) == Decimal("215.00")
    assert body["gst_included"] is False
    assert body["payment"]["status"] == "SUCCEEDED"


@pytest.mark.django_db
def test_invoice_needs_a_paid_booking(client, job):
    payment = job.booking.payment
    payment.status = "FAILED"
    payment.save(update_fields=["status"])
    r = client.get(f"/api/bookings/{job.booking_id}/invoice", **auth(job.booking.customer))
    assert r.status_code == 409 and r.json()["code"] == "invoice_not_available"


@pytest.mark.django_db
def test_invoice_is_private_to_the_customer(client, completed):
    stranger = User.objects.create_user(phone="+61400098009")
    assert client.get(f"/api/bookings/{completed.booking_id}/invoice", **auth(stranger)).status_code == 404


@pytest.mark.django_db
def test_invoice_numbers_are_never_reused(completed):
    from apps.aftercare.services import aftercare as svc

    first = svc.get_or_issue_invoice(completed.booking)
    assert first.number.endswith("-000001")
    Invoice.objects.all().delete()
    second = svc.get_or_issue_invoice(completed.booking)
    assert second.number.endswith("-000002")
