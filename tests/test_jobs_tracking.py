"""
تتبع موقع المقاول أثناء التوجّه — Job Location Tracking

يغطي:
  - 🔒 الكتابة: المقاول المُسنَد وحده — لا العميل ولا الإدارة ولا مقاول آخر.
  - 🔒 القراءة: العميل المالك والإدارة والمقاول المُسنَد — ولا أحد سواهم.
  - ⚠️ النافذة ASSIGNED وحدها: تُغلق فور البدء، فلا كتابة ولا عرض.
  - ⚠️ الموقع لا يُعرض بعد انتهاء الخدمة حتى لو كان مخزَّنًا.
  - is_stale و age_seconds محسوبان على ختم الخادم لا على ساعة الجهاز.
  - ⚠️ لا مسار: صف واحد يُكتب فوقه مهما تعدّدت التحديثات.
  - أن الموقع الحيّ لا يمسّ إحداثيات ملف المقاول (مدخلات Dispatch).
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.helpers import JPEG_BYTES, mark_paid
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.bookings.models import Booking, BookingServiceSelection, BookingStatus
from apps.contractors.models import AvailabilityStatus, ContractorProfile
from apps.jobs.models import Job, JobLocation, JobStatus
from apps.jobs.services import jobs as jobs_svc
from apps.properties.models import Property, PropertyAddress, PropertyType
from apps.services.models import ServiceType

PROPERTY_COORDS = (Decimal("-33.868800"), Decimal("151.209300"))
CONTRACTOR_HOME = (Decimal("-33.878800"), Decimal("151.209300"))
# نقطة "في الطريق" — مختلفة عن عنوان عمل المقاول
EN_ROUTE = (Decimal("-33.870000"), Decimal("151.205000"))


# ------------------------------------------------------------
# أدوات
# ------------------------------------------------------------
def make_user(phone, role=ConfirmedRole.CUSTOMER):
    return User.objects.create_user(phone=phone, role=role)


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


def post(client, url, payload=None, **extra):
    return client.post(
        url, data=json.dumps(payload or {}), content_type="application/json", **extra
    )


def make_property(owner):
    prop = Property.objects.create(
        owner=owner, label="Home", property_type=PropertyType.HOUSE
    )
    PropertyAddress.objects.create(
        property=prop,
        street_address="12 Example St",
        suburb="Bondi",
        state="NSW",
        postcode="2026",
        latitude=PROPERTY_COORDS[0],
        longitude=PROPERTY_COORDS[1],
    )
    return prop


def make_contractor(phone):
    user = make_user(phone, role=ConfirmedRole.CONTRACTOR)
    profile = ContractorProfile.objects.create(
        user=user,
        business_name=f"Co {phone[-3:]}",
        latitude=CONTRACTOR_HOME[0],
        longitude=CONTRACTOR_HOME[1],
        availability_status=AvailabilityStatus.AVAILABLE,
    )
    return user, profile


def make_job(customer, prop, service_type, profile, status=JobStatus.ASSIGNED):
    booking = Booking.objects.create(
        customer=customer,
        property=prop,
        status=BookingStatus.CONFIRMED,
        computed_price=Decimal("215.00"),
        assigned_contractor=profile,
        scheduled_at=timezone.now() + timedelta(days=1),
    )
    BookingServiceSelection.objects.create(
        booking=booking, service_type=service_type, room_count=3
    )
    job = jobs_svc.create_job_for_booking(booking)
    if status != JobStatus.ASSIGNED:
        job.status = status
        job.save(update_fields=["status"])
    return job


def location_payload(coords=EN_ROUTE, accuracy="12.50", recorded_at=None):
    body = {
        "latitude": str(coords[0]),
        "longitude": str(coords[1]),
        "recorded_at": (recorded_at or timezone.now()).isoformat(),
    }
    if accuracy is not None:
        body["accuracy"] = accuracy
    return body


# Fixtures — كتلة أرقام +614000160xx
@pytest.fixture
def client():
    return Client()


@pytest.fixture
def customer(db):
    return make_user("+61400016001")


@pytest.fixture
def other_customer(db):
    return make_user("+61400016002")


@pytest.fixture
def admin_user(db):
    return make_user("+61400016003", role=ConfirmedRole.ADMIN)


@pytest.fixture
def contractor(db):
    return make_contractor("+61400016004")


@pytest.fixture
def other_contractor(db):
    return make_contractor("+61400016005")


@pytest.fixture
def prop(customer):
    return make_property(customer)


@pytest.fixture
def service_type(db):
    return ServiceType.objects.create(
        name="General Cleaning",
        room_price=Decimal("45.00"),
        base_price=Decimal("80.00"),
    )


@pytest.fixture
def job(customer, prop, service_type, contractor):
    _, profile = contractor
    return make_job(customer, prop, service_type, profile)


# ============================================================
# 1) الكتابة
# ============================================================
@pytest.mark.django_db
def test_assigned_contractor_reports_location(client, job, contractor):
    user, _ = contractor

    r = post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(),
        **auth(user),
    )

    assert r.status_code == 200, r.content
    body = r.json()

    assert body["tracking_active"] is True
    assert body["job_status"] == JobStatus.ASSIGNED
    assert Decimal(body["contractor_location"]["latitude"]) == EN_ROUTE[0]
    assert Decimal(body["contractor_location"]["accuracy"]) == Decimal("12.50")
    assert body["contractor_location"]["is_stale"] is False
    assert Decimal(body["property_location"]["latitude"]) == PROPERTY_COORDS[0]


@pytest.mark.django_db
def test_accuracy_is_optional(client, job, contractor):
    """⚠️ أجهزة لا تبلّغ الدقة — رفض التحديث لغيابها يُسقط تتبعًا صالحًا."""
    user, _ = contractor

    r = post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(accuracy=None),
        **auth(user),
    )

    assert r.status_code == 200, r.content
    assert r.json()["contractor_location"]["accuracy"] is None


@pytest.mark.django_db
def test_updates_overwrite_and_keep_no_history(client, job, contractor):
    """⚠️ لا مسار: صف واحد لكل مهمة مهما تعدّدت التحديثات."""
    user, _ = contractor

    for lat in ("-33.871000", "-33.872000", "-33.873000"):
        r = post(
            client,
            f"/api/contractor/jobs/{job.id}/location",
            location_payload(coords=(Decimal(lat), EN_ROUTE[1])),
            **auth(user),
        )
        assert r.status_code == 200, r.content

    assert JobLocation.objects.filter(job=job).count() == 1
    assert JobLocation.objects.get(job=job).latitude == Decimal("-33.873000")


@pytest.mark.django_db
def test_live_location_does_not_touch_the_dispatch_coordinates(
    client, job, contractor
):
    """
    🔒 الموقع الحيّ كيان منفصل عن عنوان العمل.

    الكتابة فوق ContractorProfile.latitude/longitude كانت ستغيّر صامتًا
    أي الحجوزات هذا المقاول أقربها إليها.
    """
    user, profile = contractor

    post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(),
        **auth(user),
    )

    profile.refresh_from_db()
    assert profile.latitude == CONTRACTOR_HOME[0]
    assert profile.longitude == CONTRACTOR_HOME[1]


@pytest.mark.django_db
def test_future_recorded_at_is_rejected(client, job, contractor):
    """⚠️ ساعة متقدّمة كانت ستُبقي is_stale=False إلى الأبد."""
    user, _ = contractor

    r = post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(recorded_at=timezone.now() + timedelta(minutes=10)),
        **auth(user),
    )

    assert r.status_code == 400, r.content
    assert r.json()["code"] == "invalid_location"
    assert not JobLocation.objects.filter(job=job).exists()


@pytest.mark.django_db
def test_small_clock_skew_is_tolerated(client, job, contractor):
    """📌 انحراف ثوانٍ طبيعي — لا يُرفض."""
    user, _ = contractor

    r = post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(recorded_at=timezone.now() + timedelta(seconds=20)),
        **auth(user),
    )

    assert r.status_code == 200, r.content


@pytest.mark.django_db
def test_out_of_range_coordinates_are_rejected(client, job, contractor):
    user, _ = contractor

    r = post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(coords=(Decimal("-91.000000"), Decimal("151.200000"))),
        **auth(user),
    )

    assert r.status_code == 422, r.content
    assert not JobLocation.objects.filter(job=job).exists()


# ============================================================
# 2) صلاحية الكتابة
# ============================================================
@pytest.mark.django_db
def test_customer_cannot_report_location(client, job, customer):
    """🔒 الكتابة فعل المقاول — لا العميل."""
    r = post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(),
        **auth(customer),
    )

    assert r.status_code == 403, r.content
    assert not JobLocation.objects.filter(job=job).exists()


@pytest.mark.django_db
def test_admin_cannot_report_location(client, job, admin_user):
    """🔒 ولا الإدارة: الموقع شهادة المقاول عن نفسه."""
    r = post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(),
        **auth(admin_user),
    )

    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_unassigned_contractor_cannot_report_location(
    client, job, other_contractor
):
    """🔒 مقاول آخر — ولو كان مقاولًا مؤهَّلًا."""
    user, _ = other_contractor

    r = post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(),
        **auth(user),
    )

    assert r.status_code == 403, r.content


# ============================================================
# 3) النافذة الزمنية
# ============================================================
@pytest.mark.django_db
def test_window_closes_when_work_starts(client, job, contractor):
    """⚠️ فور البدء يكون العامل داخل المنزل — لا كتابة بعدها."""
    user, _ = contractor
    job.status = JobStatus.IN_PROGRESS
    job.save(update_fields=["status"])

    r = post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(),
        **auth(user),
    )

    assert r.status_code == 409, r.content
    assert r.json()["code"] == "tracking_window_closed"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [JobStatus.IN_PROGRESS, JobStatus.AWAITING_CUSTOMER_CONFIRMATION, JobStatus.COMPLETED],
)
def test_stored_location_is_hidden_outside_the_window(
    client, customer, prop, service_type, contractor, status
):
    """
    ⚠️ لا يُعرض موقع العامل بعد انتهاء الخدمة حتى لو كان مخزَّنًا.

    النقطة تُكتب أثناء ASSIGNED ثم تنتقل المهمة — يجب أن تختفي من الرد.
    """
    user, profile = contractor
    job = make_job(customer, prop, service_type, profile)

    post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(),
        **auth(user),
    )
    assert JobLocation.objects.filter(job=job).exists()

    job.status = status
    job.save(update_fields=["status"])

    r = client.get(f"/api/bookings/{job.booking_id}/tracking", **auth(customer))

    assert r.status_code == 200, r.content
    body = r.json()
    assert body["tracking_active"] is False
    assert body["contractor_location"] is None


# ============================================================
# 4) القراءة وصلاحيتها
# ============================================================
@pytest.mark.django_db
def test_customer_reads_tracking(client, job, contractor, customer):
    user, _ = contractor
    post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(),
        **auth(user),
    )

    r = client.get(f"/api/bookings/{job.booking_id}/tracking", **auth(customer))

    assert r.status_code == 200, r.content
    assert r.json()["contractor_location"] is not None


@pytest.mark.django_db
def test_admin_reads_tracking(client, job, contractor, admin_user):
    user, _ = contractor
    post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(),
        **auth(user),
    )

    r = client.get(f"/api/bookings/{job.booking_id}/tracking", **auth(admin_user))

    assert r.status_code == 200, r.content


@pytest.mark.django_db
def test_other_customer_gets_404(client, job, other_customer):
    """🔒 حجز الغير كغير الموجود."""
    r = client.get(f"/api/bookings/{job.booking_id}/tracking", **auth(other_customer))

    assert r.status_code == 404, r.content
    assert r.json()["code"] == "job_not_found"


@pytest.mark.django_db
def test_unassigned_contractor_gets_404(client, job, other_contractor):
    """🔒 مقاول غير مُسنَد لا يعرف حتى أن المهمة موجودة."""
    user, _ = other_contractor

    r = client.get(f"/api/bookings/{job.booking_id}/tracking", **auth(user))

    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_missing_location_is_not_an_error(client, job, customer):
    """⚠️ المقاول لم يرسل بعد — حالة عادية لا خطأ."""
    r = client.get(f"/api/bookings/{job.booking_id}/tracking", **auth(customer))

    assert r.status_code == 200, r.content
    body = r.json()

    assert body["tracking_active"] is True
    assert body["contractor_location"] is None
    # الوجهة تُعاد دائمًا حتى ترسم الواجهة العقار وحده
    assert Decimal(body["property_location"]["latitude"]) == PROPERTY_COORDS[0]


# ============================================================
# 5) التقادم
# ============================================================
@pytest.mark.django_db
def test_fresh_location_is_not_stale(client, job, contractor, customer):
    user, _ = contractor
    post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(),
        **auth(user),
    )

    r = client.get(f"/api/bookings/{job.booking_id}/tracking", **auth(customer))
    loc = r.json()["contractor_location"]

    assert loc["is_stale"] is False
    assert loc["age_seconds"] < 5


@pytest.mark.django_db
def test_old_location_is_marked_stale(client, job, contractor, customer):
    """⚠️ توقّف الإرسال يجب أن يظهر — لا نقطة توحي بأنها حيّة."""
    from apps.jobs.models import LOCATION_STALE_AFTER_SECONDS

    user, _ = contractor
    post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(),
        **auth(user),
    )

    # نُقدّم ختم الخادم إلى الماضي مباشرةً (auto_now يمنع ضبطه بالحفظ)
    stale_moment = timezone.now() - timedelta(
        seconds=LOCATION_STALE_AFTER_SECONDS + 30
    )
    JobLocation.objects.filter(job=job).update(received_at=stale_moment)

    r = client.get(f"/api/bookings/{job.booking_id}/tracking", **auth(customer))
    loc = r.json()["contractor_location"]

    assert loc["is_stale"] is True
    assert loc["age_seconds"] >= LOCATION_STALE_AFTER_SECONDS


@pytest.mark.django_db
def test_staleness_ignores_the_device_clock(client, job, contractor, customer):
    """
    🔒 الحساب على ختم الخادم لا على recorded_at.

    جهاز يبلّغ وقتًا قديمًا جدًا يجب ألا يجعل نقطة وصلت الآن متقادمة.
    """
    user, _ = contractor

    post(
        client,
        f"/api/contractor/jobs/{job.id}/location",
        location_payload(recorded_at=timezone.now() - timedelta(hours=3)),
        **auth(user),
    )

    r = client.get(f"/api/bookings/{job.booking_id}/tracking", **auth(customer))
    loc = r.json()["contractor_location"]

    # وصلت الآن → ليست متقادمة، رغم أن ساعة الجهاز تقول غير ذلك
    assert loc["is_stale"] is False
    assert loc["age_seconds"] < 5


# ============================================================
# 6) قواعد بنيوية
# ============================================================
def test_tracking_window_is_the_inverse_of_photo_uploads():
    """
    🔒 التتبع قبل البدء والصور بعده — لا لحظة تقبل الاثنين.
    """
    for status in JobStatus.values:
        job = Job(status=status)
        assert not (job.is_tracking_window_open() and job.accepts_photos()), status


def test_only_assigned_status_opens_the_window():
    for status in JobStatus.values:
        job = Job(status=status)
        assert job.is_tracking_window_open() == (status == JobStatus.ASSIGNED)


def test_no_path_history_model_exists():
    """⚠️ لا كيان مسار: صف واحد لكل مهمة بحكم العلاقة واحد-لواحد."""
    field = JobLocation._meta.get_field("job")

    assert field.one_to_one, "JobLocation.job must be OneToOne — no path history"


def test_api_layer_has_no_orm():
    """§43: طبقة الـAPI لا تستدعي الـORM."""
    import inspect

    from apps.jobs.api import jobs as api_mod

    src = inspect.getsource(api_mod)

    assert ".objects." not in src
    assert "JobLocation(" not in src
