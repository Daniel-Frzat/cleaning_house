"""
Back-office — العقارات والحجوزات والمهام (قراءة فقط).
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.bookings.models import (
    BookingQuote,
    BookingStatus,
    DispatchOfferStatus,
    DispatchStatus,
)
from apps.jobs.models import Job, JobPhoto, JobStatus, PhotoType
from apps.payouts.models import Payout, PayoutStatus
from tests.backoffice import (
    auth,
    make_admin,
    make_booking,
    make_contractor,
    make_offer,
    make_property,
    make_service_type,
    make_user,
)
from tests.helpers import mark_paid


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin(db):
    return make_admin()


def items(client, admin, url):
    r = client.get(url, **auth(admin))
    assert r.status_code == 200, r.content
    return r.json()


# ============================================================
# العقارات
# ============================================================
@pytest.mark.django_db
def test_list_properties_with_filters(client, admin):
    alice = make_user(full_name="Alice")
    bob = make_user()
    p1 = make_property(alice, suburb="Parramatta", state="NSW", postcode="2150")
    p2 = make_property(alice, suburb="Fitzroy", state="VIC", postcode="3065", street="9 Brunswick St")
    p3 = make_property(bob, suburb="Surry Hills", state="NSW", postcode="2010")
    p3.is_active = False
    p3.save()

    body = items(client, admin, "/api/admin/properties")
    assert body["count"] == 3
    assert body["items"][0]["id"] == str(p3.id)  # الأحدث أولًا
    first = next(i for i in body["items"] if i["id"] == str(p1.id))
    assert first["owner_name"] == "Alice"
    assert first["address"]["suburb"] == "Parramatta"

    def ids(q):
        return {i["id"] for i in items(client, admin, f"/api/admin/properties?{q}")["items"]}

    assert ids(f"owner_id={alice.id}") == {str(p1.id), str(p2.id)}
    assert ids("state=VIC") == {str(p2.id)}
    assert ids("is_active=false") == {str(p3.id)}
    assert ids("q=fitz") == {str(p2.id)}
    assert ids("q=2010") == {str(p3.id)}
    assert ids("q=brunswick") == {str(p2.id)}
    assert items(client, admin, "/api/admin/properties?limit=1")["count"] == 3


@pytest.mark.django_db
def test_property_detail_with_bookings_count(client, admin):
    owner = make_user()
    prop = make_property(owner)
    make_booking(owner, prop)
    make_booking(owner, prop)

    body = items(client, admin, f"/api/admin/properties/{prop.id}")
    assert body["bookings_count"] == 2
    assert body["address"]["postcode"] == "2150"
    assert body["owner_id"] == str(owner.id)


# ============================================================
# الحجوزات
# ============================================================
@pytest.mark.django_db
def test_list_bookings_filters(client, admin):
    alice, bob = make_user(), make_user()
    _, profile = make_contractor()
    b1 = make_booking(alice)
    b2 = make_booking(
        bob,
        status=BookingStatus.CONFIRMED,
        price=Decimal("215.00"),
        contractor=profile,
        dispatch_status=DispatchStatus.ASSIGNED,
        scheduled_at=timezone.now() + timedelta(days=5),
    )
    b3 = make_booking(alice, dispatch_status=DispatchStatus.NO_CONTRACTOR)
    b3.created_at = timezone.now() - timedelta(days=20)
    b3.save()

    body = items(client, admin, "/api/admin/bookings")
    assert body["count"] == 3
    assert [i["id"] for i in body["items"]] == [str(b2.id), str(b1.id), str(b3.id)]
    row = body["items"][0]
    assert row["assigned_contractor"]["profile_id"] == str(profile.id)
    assert row["computed_price"] == "215.00"
    assert row["suburb"] == "Parramatta"

    def ids(q):
        return {i["id"] for i in items(client, admin, f"/api/admin/bookings?{q}")["items"]}

    today = timezone.localdate()
    assert ids("status=CONFIRMED") == {str(b2.id)}
    assert ids("dispatch_status=NO_CONTRACTOR") == {str(b3.id)}
    assert ids(f"customer_id={alice.id}") == {str(b1.id), str(b3.id)}
    assert ids(f"contractor_id={profile.id}") == {str(b2.id)}
    assert ids(f"created_from={today - timedelta(days=1)}") == {str(b1.id), str(b2.id)}
    assert ids(f"created_to={today - timedelta(days=10)}") == {str(b3.id)}
    assert ids(
        f"scheduled_from={today + timedelta(days=4)}&scheduled_to={today + timedelta(days=6)}"
    ) == {str(b2.id)}
    assert ids(f"q={b1.public_reference[-4:]}") == {str(b1.id)}
    assert ids(f"q={b1.public_reference.lower()}") == {str(b1.id)}

    page = items(client, admin, "/api/admin/bookings?limit=1&offset=1")
    assert page["count"] == 3
    assert [i["id"] for i in page["items"]] == [str(b1.id)]


@pytest.mark.django_db
def test_booking_detail_full_history(client, admin):
    customer = make_user(full_name="Cathy", email="cathy@example.com")
    service = make_service_type()
    _, first = make_contractor()
    _, second = make_contractor()
    quote = BookingQuote.objects.create(
        customer=customer,
        property=make_property(customer),
        service_snapshot=[{"service_type_id": str(service.id), "rooms": 3}],
        services_total=Decimal("215.00"),
        maximum_total=Decimal("260.00"),
        pricing_version=4,
        expires_at=timezone.now() + timedelta(minutes=30),
    )
    booking = make_booking(
        customer,
        prop=quote.property,
        service=service,
        status=BookingStatus.CONFIRMED,
        price=Decimal("215.00"),
        contractor=second,
        quote=quote,
        max_total=Decimal("260.00"),
        access_notes="Key under the mat",
        dispatch_round=2,
    )
    make_offer(booking, first, status=DispatchOfferStatus.EXPIRED, rnd=1)
    make_offer(booking, first, status=DispatchOfferStatus.DECLINED, rnd=2)
    make_offer(booking, second, status=DispatchOfferStatus.ACCEPTED, rnd=2)
    payment = mark_paid(booking)
    job = Job.objects.create(booking=booking, status=JobStatus.COMPLETED)
    payout = Payout.objects.create(
        booking=booking,
        contractor=second.user,
        amount=Decimal("215.00"),
        status=PayoutStatus.PENDING,
        failure_reason="provider_error: ConnectionError",
    )

    body = items(client, admin, f"/api/admin/bookings/{booking.id}")

    assert body["customer"]["email"] == "cathy@example.com"
    assert body["property"]["address"]["suburb"] == "Parramatta"
    assert body["service_lines"] == [
        {"service_type_id": str(service.id), "service_name": "General Cleaning", "room_count": 3}
    ]
    assert body["quote"]["maximum_total"] == "260.00"
    assert body["quote"]["pricing_version"] == 4
    assert body["max_total"] == "260.00"
    assert body["computed_price"] == "215.00"
    assert body["access_notes"] == "Key under the mat"
    offers = body["dispatch_offers"]
    assert [(o["dispatch_round"], o["status"]) for o in offers] == [
        (1, "EXPIRED"),
        (2, "DECLINED"),
        (2, "ACCEPTED"),
    ]
    assert offers[2]["contractor"]["profile_id"] == str(second.id)
    assert offers[2]["contractor"]["user_id"] == str(second.user_id)
    assert offers[0]["distance_km"] == "1.112"
    assert offers[0]["total_amount"] == "215.00"
    assert body["payment"]["id"] == str(payment.id)
    assert body["payment"]["provider_reference"] == "test-paid"
    assert body["job"]["id"] == str(job.id)
    assert body["job"]["status"] == "COMPLETED"
    assert body["payout"]["id"] == str(payout.id)
    assert body["payout"]["failure_reason"].startswith("provider_error")


@pytest.mark.django_db
def test_booking_detail_pending_booking_has_empty_sections(client, admin):
    booking = make_booking(make_user())
    body = items(client, admin, f"/api/admin/bookings/{booking.id}")
    assert body["quote"] is None
    assert body["payment"] is None
    assert body["job"] is None
    assert body["payout"] is None
    assert body["assigned_contractor"] is None
    assert body["dispatch_offers"] == []
    assert body["computed_price"] is None


# ============================================================
# المهام
# ============================================================
def _job(profile, status=JobStatus.ASSIGNED):
    booking = make_booking(
        make_user(), status=BookingStatus.CONFIRMED, price=Decimal("215.00"), contractor=profile
    )
    return Job.objects.create(booking=booking, status=status)


@pytest.mark.django_db
def test_list_jobs_filters(client, admin):
    _, p1 = make_contractor()
    _, p2 = make_contractor()
    j1 = _job(p1)
    j2 = _job(p2, status=JobStatus.IN_PROGRESS)
    j3 = _job(p1, status=JobStatus.COMPLETED)
    j3.created_at = timezone.now() - timedelta(days=9)
    j3.save()

    body = items(client, admin, "/api/admin/jobs")
    assert body["count"] == 3
    assert body["items"][0]["id"] == str(j2.id)
    assert body["items"][0]["contractor"]["profile_id"] == str(p2.id)

    def ids(q):
        return {i["id"] for i in items(client, admin, f"/api/admin/jobs?{q}")["items"]}

    today = timezone.localdate()
    assert ids("status=IN_PROGRESS") == {str(j2.id)}
    assert ids(f"contractor_id={p1.id}") == {str(j1.id), str(j3.id)}
    assert ids(f"created_to={today - timedelta(days=5)}") == {str(j3.id)}
    assert ids(f"created_from={today}") == {str(j1.id), str(j2.id)}
    assert items(client, admin, "/api/admin/jobs?limit=2")["count"] == 3
    assert len(items(client, admin, "/api/admin/jobs?limit=2")["items"]) == 2


@pytest.mark.django_db
def test_job_detail_exposes_signed_url_and_storage_key(client, admin):
    user, profile = make_contractor()
    job = _job(profile, status=JobStatus.IN_PROGRESS)
    job.started_at = timezone.now()
    job.save()
    photo = JobPhoto.objects.create(
        job=job, photo_type=PhotoType.BEFORE, storage_key="fake/key-123.jpg", uploaded_by=user
    )

    body = items(client, admin, f"/api/admin/jobs/{job.id}")

    assert body["status"] == "IN_PROGRESS"
    assert body["started_at"] is not None
    assert len(body["photos"]) == 1
    p = body["photos"][0]
    assert p["id"] == str(photo.id)
    assert p["storage_key"] == "fake/key-123.jpg"
    assert p["url"]
    assert p["uploaded_by_id"] == str(user.id)
    # 🔒 موقع المقاول الحي غير معروض للإدارة
    assert "location" not in body and "contractor_location" not in body


@pytest.mark.django_db
def test_storage_key_is_not_exposed_on_customer_route(client, admin):
    """🔒 المرجع الخام للإدارة وحدها — مسار العميل يبقى بلا storage_key."""
    user, profile = make_contractor()
    job = _job(profile, status=JobStatus.IN_PROGRESS)
    JobPhoto.objects.create(
        job=job, photo_type=PhotoType.BEFORE, storage_key="fake/secret.jpg", uploaded_by=user
    )
    r = client.get(f"/api/bookings/{job.booking_id}/job", **auth(job.booking.customer))
    assert r.status_code == 200, r.content
    assert all(p.get("storage_key") is None for p in r.json()["photos"])
