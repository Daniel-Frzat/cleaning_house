"""
Job start tests — Jobs Domain

📌 ASSIGNED → IN_PROGRESS عبر فعل صريح من المقاول.

⚠️ قبل هذه الإضافة كان القبول يُنشئ المهمة جاهزة للتنفيذ في اللحظة نفسها،
   فلم يكن للعميل أي وسيلة ليعرف أن مقاولًا قَبِل لكنه لم يصل بعد —
   الحالتان كانتا لحظة واحدة.
"""

import datetime
from decimal import Decimal

import pytest

from tests.helpers import arrive

from tests.helpers import JPEG_BYTES, mark_paid
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.bookings.models import (
    Booking,
    BookingServiceSelection,
    BookingStatus,
)
from apps.contractors.models import (
    AvailabilityStatus,
    ContractorProfile,
)
from apps.jobs.models import Job, JobStatus, PhotoType
from apps.jobs.services import jobs as jobs_svc
from apps.jobs.services import photos as photos_svc
from apps.properties.models import Property, PropertyAddress, PropertyType
from apps.services.models import ServiceType

from tests.conftest import set_contractor_location


@pytest.fixture
def client():
    return Client()


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


@pytest.fixture
def customer(db):
    return User.objects.create_user(phone="+61400770001", role=ConfirmedRole.CUSTOMER)


@pytest.fixture
def contractor(db):
    user = User.objects.create_user(
        phone="+61400770002", role=ConfirmedRole.CONTRACTOR
    )
    profile = ContractorProfile.objects.create(
        user=user, business_name="Sparkle Co",
        latitude=Decimal("-33.868800"), longitude=Decimal("151.209300"),
        availability_status=AvailabilityStatus.AVAILABLE,
    )
    # §8: الإسناد يقرأ موقع الهاتف الحالي لا عنوان العمل.
    set_contractor_location(
        profile, (Decimal("-33.868800"), Decimal("151.209300"))
    )
    return user, profile


@pytest.fixture
def other_contractor(db):
    user = User.objects.create_user(
        phone="+61400770003", role=ConfirmedRole.CONTRACTOR
    )
    ContractorProfile.objects.create(user=user, business_name="Other")
    return user


@pytest.fixture
def job(customer, contractor):
    """مهمة كما ينشئها قبول العرض تمامًا — أي ASSIGNED."""
    _, profile = contractor
    prop = Property.objects.create(
        owner=customer, label="Home", property_type=PropertyType.HOUSE
    )
    PropertyAddress.objects.create(
        property=prop, street_address="1 St", suburb="Sydney",
        state="NSW", postcode="2000",
    )
    service = ServiceType.objects.create(
        name="General Cleaning",
        room_price=Decimal("45.00"), base_price=Decimal("80.00"),
    )
    booking = Booking.objects.create(
        customer=customer, property=prop, status=BookingStatus.CONFIRMED,
        computed_price=Decimal("215.00"), assigned_contractor=profile,
    )
    BookingServiceSelection.objects.create(
        booking=booking, service_type=service, room_count=3
    )
    # بدء المهمة مشروط بنجاح دفع العميل
    mark_paid(booking)
    return jobs_svc.create_job_for_booking(booking)


# ============================================================
# 1) الحالة الابتدائية
# ============================================================
@pytest.mark.django_db
def test_a_new_job_is_assigned_not_in_progress(job):
    """📌 القبول يُسنِد، ولا يبدأ."""
    assert job.status == JobStatus.ASSIGNED
    assert job.started_at is None


@pytest.mark.django_db
def test_job_status_enum_has_four_states():
    """⚠️ لا CANCELLED — الإلغاء بند مفتوح."""
    assert set(JobStatus.values) == {
        "ASSIGNED", "ARRIVED", "IN_PROGRESS", "AWAITING_CUSTOMER_CONFIRMATION", "COMPLETED",
    }


# ============================================================
# 2) الانتقال
# ============================================================
@pytest.mark.django_db
def test_assigned_contractor_starts_the_job(contractor, job):
    user, _ = contractor

    arrive(job, user)

    started = jobs_svc.start_job(job, user)

    assert started.status == JobStatus.IN_PROGRESS
    assert started.started_at is not None


@pytest.mark.django_db
def test_start_is_persisted(contractor, job):
    user, _ = contractor

    arrive(job, user)

    jobs_svc.start_job(job, user)

    stored = Job.objects.get(pk=job.pk)
    assert stored.status == JobStatus.IN_PROGRESS
    assert stored.started_at is not None


@pytest.mark.django_db
def test_started_at_is_not_the_creation_time(contractor, job):
    """الطابعان منفصلان: الإنشاء عند القبول، البدء عند الوصول."""
    user, _ = contractor
    created_at = job.created_at

    arrive(job, user)

    started = jobs_svc.start_job(job, user)

    assert started.started_at >= created_at


@pytest.mark.django_db
def test_api_start_endpoint(client, contractor, job):
    user, _ = contractor

    arrive(job, user)
    r = client.post(f"/api/contractor/jobs/{job.id}/start", **auth(user))

    assert r.status_code == 200, r.content
    assert r.json()["status"] == JobStatus.IN_PROGRESS
    assert r.json()["started_at"] is not None


# ============================================================
# 3) 🔒 الحدود
# ============================================================
@pytest.mark.django_db
def test_another_contractor_cannot_start_the_job(client, other_contractor, job):
    r = client.post(
        f"/api/contractor/jobs/{job.id}/start", **auth(other_contractor)
    )

    assert r.status_code == 403, r.content
    job.refresh_from_db()
    assert job.status == JobStatus.ASSIGNED


@pytest.mark.django_db
def test_the_customer_cannot_start_the_job(client, customer, job):
    """🔒 البدء فعل المقاول — لا العميل ولا الإدارة."""
    r = client.post(f"/api/contractor/jobs/{job.id}/start", **auth(customer))

    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_starting_twice_is_refused_with_409(client, contractor, job):
    user, _ = contractor
    arrive(job, user)
    client.post(f"/api/contractor/jobs/{job.id}/start", **auth(user))

    r = client.post(f"/api/contractor/jobs/{job.id}/start", **auth(user))

    assert r.status_code == 409, r.content
    assert r.json()["code"] == "invalid_job_status"


@pytest.mark.django_db
def test_unknown_job_returns_404(client, contractor):
    import uuid

    user, _ = contractor

    r = client.post(f"/api/contractor/jobs/{uuid.uuid4()}/start", **auth(user))

    assert r.status_code == 404, r.content


# ============================================================
# 4) العلاقة بالصور وبالإنجاز
# ============================================================
@pytest.mark.django_db
def test_photos_are_refused_before_the_job_starts(contractor, job):
    """
    🔒 صورة "قبل" تُلتقط عند الموقع: المقاول لم يصل بعد وهو ASSIGNED.
    """
    user, _ = contractor

    with pytest.raises(photos_svc.JobNotAcceptingPhotosError):
        photos_svc.upload_job_photo(
            user, job.id, photo_type=PhotoType.BEFORE,
            file_bytes=JPEG_BYTES * 16, content_type="image/jpeg",
        )


@pytest.mark.django_db
def test_photos_are_accepted_once_started(contractor, job):
    user, _ = contractor
    arrive(job, user)
    jobs_svc.start_job(job, user)

    photo = photos_svc.upload_job_photo(
        user, job.id, photo_type=PhotoType.BEFORE,
        file_bytes=JPEG_BYTES * 16, content_type="image/jpeg",
    )

    assert photo.photo_type == PhotoType.BEFORE


@pytest.mark.django_db
def test_mark_done_is_refused_before_starting(contractor, job):
    """⚠️ لا قفز فوق البدء: ASSIGNED لا تنتقل مباشرةً إلى الإنجاز."""
    user, _ = contractor

    with pytest.raises(jobs_svc.InvalidJobStatusError):
        jobs_svc.mark_job_done(job, user)


@pytest.mark.django_db
def test_the_full_sequence(client, customer, contractor, job):
    """📌 الدورة كاملة: ASSIGNED → IN_PROGRESS → AWAITING → COMPLETED."""
    user, _ = contractor

    assert job.status == JobStatus.ASSIGNED

    arrive(job, user)

    jobs_svc.start_job(job, user)
    job.refresh_from_db()
    assert job.status == JobStatus.IN_PROGRESS

    for kind in (PhotoType.BEFORE, PhotoType.AFTER):
        photos_svc.upload_job_photo(
            user, job.id, photo_type=kind,
            file_bytes=JPEG_BYTES * 16, content_type="image/jpeg",
        )

    jobs_svc.mark_job_done(job, user)
    job.refresh_from_db()
    assert job.status == JobStatus.AWAITING_CUSTOMER_CONFIRMATION

    r = client.post(
        f"/api/bookings/{job.booking_id}/job/confirm", **auth(customer)
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == JobStatus.COMPLETED


# ============================================================
# 5) ما يراه العميل
# ============================================================
@pytest.mark.django_db
def test_customer_can_tell_assigned_from_in_progress(client, customer, contractor, job):
    """
    📌 الغرض كله: الحالتان صارتا متمايزتين في رد الـAPI.
    """
    user, _ = contractor

    before = client.get(
        f"/api/bookings/{job.booking_id}/job", **auth(customer)
    ).json()
    assert before["status"] == JobStatus.ASSIGNED
    assert before["started_at"] is None

    arrive(job, user)

    jobs_svc.start_job(job, user)

    after = client.get(
        f"/api/bookings/{job.booking_id}/job", **auth(customer)
    ).json()
    assert after["status"] == JobStatus.IN_PROGRESS
    assert after["started_at"] is not None


# ============================================================
# مرحلة الوصول (ARRIVED — قرار PO 2026-09-26)
# ============================================================
@pytest.mark.django_db
def test_start_requires_arrival_first(client, contractor, job):
    user, _ = contractor
    r = client.post(f"/api/contractor/jobs/{job.id}/start", **auth(user))
    assert r.status_code == 409
    assert "arrival" in r.json()["detail"]


@pytest.mark.django_db
def test_arrive_near_property_then_start(client, contractor, job):
    from decimal import Decimal

    from apps.properties.models import PropertyAddress

    PropertyAddress.objects.filter(property=job.booking.property).update(
        latitude=Decimal("-33.868800"), longitude=Decimal("151.209300")
    )
    user, _ = contractor
    r = client.post(
        f"/api/contractor/jobs/{job.id}/arrive",
        data={"latitude": "-33.869500", "longitude": "151.209300", "accuracy": "15"},
        content_type="application/json",
        **auth(user),
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "ARRIVED" and r.json()["arrived_at"]
    assert client.post(f"/api/contractor/jobs/{job.id}/start", **auth(user)).status_code == 200


@pytest.mark.django_db
def test_arrive_far_from_property_is_refused(client, contractor, job):
    from decimal import Decimal

    from apps.properties.models import PropertyAddress

    PropertyAddress.objects.filter(property=job.booking.property).update(
        latitude=Decimal("-33.868800"), longitude=Decimal("151.209300")
    )
    user, _ = contractor
    r = client.post(
        f"/api/contractor/jobs/{job.id}/arrive",
        data={"latitude": "-33.900000", "longitude": "151.209300"},  # ~3.5 km
        content_type="application/json",
        **auth(user),
    )
    assert r.status_code == 409
    assert r.json()["code"] == "not_at_property"
    job.refresh_from_db()
    assert job.status == JobStatus.ASSIGNED


@pytest.mark.django_db
def test_arrival_closes_tracking_and_notifies_customer(contractor, job, django_capture_on_commit_callbacks):
    from apps.notifications.models import Notification

    user, _ = contractor
    with django_capture_on_commit_callbacks(execute=True):
        arrive(job, user)
    assert job.is_tracking_window_open() is False
    n = Notification.objects.get(user=job.booking.customer)
    assert n.type == "job.arrived"


@pytest.mark.django_db
def test_other_contractor_cannot_report_arrival(client, other_contractor, job):
    r = client.post(
        f"/api/contractor/jobs/{job.id}/arrive",
        data={"latitude": "-33.8688", "longitude": "151.2093"},
        content_type="application/json",
        **auth(other_contractor),
    )
    assert r.status_code == 403
