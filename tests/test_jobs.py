"""
Job Execution Tests — Jobs Domain (Change Set §20، §36.3؛ Infra §7)

يغطي: الإنشاء التلقائي عند تأكيد الحجز، تقييد رفع الصور بالمقاول المُسنَد،
رفض الرفع بعد انتهاء التنفيذ، حجب storage_key عن العميل، صمّام الـadapter
الوهمي، وإنفاذ الصلاحية على عرض المهمة عبر الأدوار الأربعة.
"""

import json
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.bookings.models import (
    Booking,
    BookingServiceSelection,
    BookingStatus,
    DispatchOffer,
    DispatchOfferStatus,
)
from apps.contractors.models import (
    AvailabilityStatus,
    BusinessRegistration,
    ContractorProfile,
    InsuranceDocument,
    VerificationStatus,
)
from apps.jobs.adapters import get_storage_adapter
from apps.jobs.adapters.base import BaseStorageProviderAdapter, StorageUploadResult
from apps.jobs.adapters.fake_adapter import FAKE_URL_PREFIX, FakeStorageAdapter
from apps.jobs.models import Job, JobPhoto, JobStatus, PhotoType
from apps.jobs.services import jobs as jobs_svc
from apps.jobs.services import photos as photos_svc
from apps.properties.models import Property, PropertyAddress, PropertyType
from apps.services.models import PricingConfig, ServiceType


# ------------------------------------------------------------
# أدوات
# ------------------------------------------------------------
@pytest.fixture
def client():
    return Client()


def make_user(phone, role=ConfirmedRole.CUSTOMER):
    return User.objects.create_user(phone=phone, role=role)


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


def make_photo_file(name="before.jpg", content=b"fake-image-bytes"):
    return SimpleUploadedFile(name, content, content_type="image/jpeg")


@pytest.fixture
def customer(db):
    return make_user("+61400010001")


@pytest.fixture
def other_customer(db):
    return make_user("+61400010002")


@pytest.fixture
def admin_user(db):
    return make_user("+61400010003", role=ConfirmedRole.ADMIN)


def make_contractor(phone):
    user = make_user(phone, role=ConfirmedRole.CONTRACTOR)
    profile = ContractorProfile.objects.create(
        user=user,
        business_name=f"Co {phone[-3:]}",
        latitude=Decimal("-33.878800"),
        longitude=Decimal("151.209300"),
        availability_status=AvailabilityStatus.AVAILABLE,
    )
    return user, profile


@pytest.fixture
def contractor(db):
    return make_contractor("+61400010004")


@pytest.fixture
def other_contractor(db):
    return make_contractor("+61400010005")


@pytest.fixture
def prop(customer):
    p = Property.objects.create(
        owner=customer, label="Home", property_type=PropertyType.HOUSE
    )
    PropertyAddress.objects.create(
        property=p, street_address="12 Example St", suburb="Bondi",
        state="NSW", postcode="2026",
        latitude=Decimal("-33.868800"), longitude=Decimal("151.209300"),
    )
    return p


@pytest.fixture
def service_type(db):
    return ServiceType.objects.create(
        name="General Cleaning",
        room_price=Decimal("45.00"),
        base_price=Decimal("80.00"),
    )


def make_confirmed_booking(customer, prop, service_type, contractor_profile):
    booking = Booking.objects.create(
        customer=customer,
        property=prop,
        status=BookingStatus.CONFIRMED,
        computed_price=Decimal("215.00"),
        assigned_contractor=contractor_profile,
    )
    BookingServiceSelection.objects.create(
        booking=booking, service_type=service_type, room_count=3
    )
    return booking


@pytest.fixture
def job(customer, prop, service_type, contractor):
    """
    مهمة جارية (IN_PROGRESS) — الحالة التي تفترضها أغلب الاختبارات.

    ⚠️ المهمة تُنشأ ASSIGNED منذ إضافة فعل البدء، فالـfixture يبدأها
       صراحةً. الحالة الابتدائية نفسها تُختبر في
       tests/test_jobs_start.py.
    """
    user, profile = contractor
    booking = make_confirmed_booking(customer, prop, service_type, profile)
    created = jobs_svc.create_job_for_booking(booking)
    return jobs_svc.start_job(created, user)


# ============================================================
# 1) الإنشاء التلقائي عند تأكيد الحجز
# ============================================================
@pytest.mark.django_db
def test_job_auto_created_when_booking_confirmed(
    customer, prop, service_type, admin_user, django_capture_on_commit_callbacks
):
    """📌 المهمة تُنشأ لحظة قبول العرض، لا عند إنشاء الحجز."""
    cfg, _ = PricingConfig.objects.get_or_create(pk=PricingConfig.SINGLETON_PK)
    cfg.price_per_km = Decimal("2.00")
    cfg.save()

    contractor_user, profile = make_contractor("+61400010100")
    BusinessRegistration.objects.create(
        contractor=profile, abn="12345678901", business_name="Co",
        status=VerificationStatus.VERIFIED, reviewed_by=admin_user,
        reviewed_at=timezone.now(),
    )
    InsuranceDocument.objects.create(
        contractor=profile, document_reference="POL",
        expiry_date=timezone.localdate() + timedelta(days=90),
        status=VerificationStatus.VERIFIED, reviewed_by=admin_user,
        reviewed_at=timezone.now(),
    )

    booking = Booking.objects.create(
        customer=customer, property=prop, status=BookingStatus.PENDING
    )
    BookingServiceSelection.objects.create(
        booking=booking, service_type=service_type, room_count=3
    )

    # قبل القبول: لا مهمة
    assert Job.objects.filter(booking=booking).count() == 0

    offer = DispatchOffer.objects.create(
        booking=booking, contractor=profile, status=DispatchOfferStatus.PENDING,
        distance_km=Decimal("1.112"),
        expires_at=timezone.now() + timedelta(minutes=60),
    )

    from apps.bookings.services import offers as osvc

    with django_capture_on_commit_callbacks(execute=True):
        osvc.accept_offer(contractor_user, offer.id)

    booking.refresh_from_db()
    created = Job.objects.get(booking=booking)

    assert booking.status == BookingStatus.CONFIRMED
    # 📌 القبول يُنشئ المهمة ASSIGNED لا IN_PROGRESS: البدء فعل منفصل
    #    للمقاول، وهو ما يميّز "قَبِل ولم يصل" من "يعمل الآن".
    assert created.status == JobStatus.ASSIGNED
    assert created.started_at is None
    assert created.marked_done_at is None
    assert created.confirmed_at is None


@pytest.mark.django_db
def test_no_job_for_pending_booking(customer, prop, service_type):
    """⚠️ لا مهمة لحجز غير مؤكَّد."""
    booking = Booking.objects.create(
        customer=customer, property=prop, status=BookingStatus.PENDING
    )

    with pytest.raises(jobs_svc.BookingNotConfirmedError):
        jobs_svc.create_job_for_booking(booking)

    assert Job.objects.count() == 0


@pytest.mark.django_db
def test_second_job_rejected_by_service_and_db(job):
    """🔒 دفاع في العمق: حجز واحد = مهمة واحدة."""
    with pytest.raises(jobs_svc.JobAlreadyExistsError):
        jobs_svc.create_job_for_booking(job.booking)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Job.objects.create(booking=job.booking, status=JobStatus.IN_PROGRESS)

    assert Job.objects.filter(booking=job.booking).count() == 1


@pytest.mark.django_db
def test_job_status_enum_has_no_cancelled(db):
    """⚠️ الإلغاء بند مفتوح (#12) — لا حالة له هنا."""
    assert set(JobStatus.values) == {
        "ASSIGNED",
        "IN_PROGRESS",
        "AWAITING_CUSTOMER_CONFIRMATION",
        "COMPLETED",
    }
    for absent in ("CANCELLED", "CANCELED", "ABORTED", "REFUNDED"):
        assert absent not in JobStatus.values


# ============================================================
# 2) رفع الصور — المقاول المُسنَد وحده
# ============================================================
@pytest.mark.django_db
def test_assigned_contractor_uploads_photo(client, contractor, job):
    contractor_user, _ = contractor

    r = client.post(
        f"/api/contractor/jobs/{job.id}/photos?photo_type=BEFORE",
        {"file": make_photo_file()},
        **auth(contractor_user),
    )

    assert r.status_code == 201, r.content
    body = r.json()
    assert body["photo_type"] == PhotoType.BEFORE
    assert body["signed_url"].startswith(FAKE_URL_PREFIX)

    photo = JobPhoto.objects.get(pk=body["id"])
    assert photo.job_id == job.id
    assert photo.uploaded_by_id == contractor_user.id
    assert photo.storage_key.startswith("fake/")


@pytest.mark.django_db
@pytest.mark.parametrize("photo_type", ["BEFORE", "AFTER"])
def test_both_photo_types_accepted(client, contractor, job, photo_type):
    contractor_user, _ = contractor

    r = client.post(
        f"/api/contractor/jobs/{job.id}/photos?photo_type={photo_type}",
        {"file": make_photo_file()},
        **auth(contractor_user),
    )

    assert r.status_code == 201, r.content
    assert r.json()["photo_type"] == photo_type


@pytest.mark.django_db
def test_other_contractor_cannot_upload(client, other_contractor, job):
    """🔒 المقاول غير المُسنَد يُرفض بـ403."""
    intruder, _ = other_contractor

    r = client.post(
        f"/api/contractor/jobs/{job.id}/photos?photo_type=BEFORE",
        {"file": make_photo_file()},
        **auth(intruder),
    )

    assert r.status_code == 403, r.content
    assert JobPhoto.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("role", [ConfirmedRole.CUSTOMER, ConfirmedRole.ADMIN])
def test_non_contractor_cannot_upload(client, db, job, role):
    user = make_user("+61400010200", role=role)

    r = client.post(
        f"/api/contractor/jobs/{job.id}/photos?photo_type=BEFORE",
        {"file": make_photo_file()},
        **auth(user),
    )

    assert r.status_code == 403, r.content
    assert JobPhoto.objects.count() == 0


@pytest.mark.django_db
def test_customer_who_owns_booking_still_cannot_upload(client, customer, job):
    """حتى مالك الحجز لا يرفع صور التنفيذ — فعل المقاول وحده."""
    r = client.post(
        f"/api/contractor/jobs/{job.id}/photos?photo_type=AFTER",
        {"file": make_photo_file()},
        **auth(customer),
    )

    assert r.status_code == 403
    assert JobPhoto.objects.count() == 0


@pytest.mark.django_db
def test_upload_to_unknown_job_returns_404(client, contractor):
    contractor_user, _ = contractor

    r = client.post(
        f"/api/contractor/jobs/{uuid.uuid4()}/photos?photo_type=BEFORE",
        {"file": make_photo_file()},
        **auth(contractor_user),
    )

    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_unauthenticated_upload_returns_401(client, job):
    r = client.post(
        f"/api/contractor/jobs/{job.id}/photos?photo_type=BEFORE",
        {"file": make_photo_file()},
    )

    assert r.status_code == 401


@pytest.mark.django_db
def test_ownership_enforced_in_service_layer(db, other_contractor, job):
    """الإنفاذ في طبقة الخدمة لا الـAPI وحدها."""
    intruder, _ = other_contractor

    with pytest.raises(jobs_svc.JobPermissionError):
        jobs_svc.get_job_for_contractor(intruder, job.id)


# ============================================================
# 3) الرفع مرفوض بعد انتهاء التنفيذ
# ============================================================
@pytest.mark.django_db
@pytest.mark.parametrize(
    "status", [JobStatus.AWAITING_CUSTOMER_CONFIRMATION, JobStatus.COMPLETED]
)
def test_upload_rejected_once_job_left_in_progress(client, contractor, job, status):
    """⚠️ الأدلة تتجمّد بعد إعلان الإنجاز — 409."""
    contractor_user, _ = contractor
    Job.objects.filter(pk=job.pk).update(status=status)

    r = client.post(
        f"/api/contractor/jobs/{job.id}/photos?photo_type=AFTER",
        {"file": make_photo_file()},
        **auth(contractor_user),
    )

    assert r.status_code == 409, r.content
    assert r.json()["code"] == "job_not_accepting_photos"
    assert JobPhoto.objects.count() == 0


@pytest.mark.django_db
def test_service_layer_rejects_upload_when_not_in_progress(db, contractor, job):
    contractor_user, _ = contractor
    Job.objects.filter(pk=job.pk).update(status=JobStatus.COMPLETED)

    with pytest.raises(photos_svc.JobNotAcceptingPhotosError):
        photos_svc.upload_job_photo(
            contractor_user, job.id, PhotoType.AFTER, b"bytes", "image/jpeg"
        )


@pytest.mark.django_db
def test_invalid_photo_type_rejected(db, contractor, job):
    contractor_user, _ = contractor

    with pytest.raises(photos_svc.InvalidPhotoTypeError):
        photos_svc.upload_job_photo(
            contractor_user, job.id, "SIDEWAYS", b"bytes", "image/jpeg"
        )


@pytest.mark.django_db
def test_empty_file_rejected(db, contractor, job):
    contractor_user, _ = contractor

    with pytest.raises(photos_svc.EmptyPhotoError):
        photos_svc.upload_job_photo(
            contractor_user, job.id, PhotoType.BEFORE, b"", "image/jpeg"
        )


# ============================================================
# 4) storage_key لا يُكشف للعميل
# ============================================================
@pytest.mark.django_db
def test_storage_key_hidden_from_customer(client, customer, contractor, job):
    """
    🔒 العميل لا يحصل على حقل storage_key — يحصل على signed_url فقط.

    ⚠️ الفحص على الحقل لا على النص: الـadapter الوهمي يبني رابطه بصيغة
       ".../signed/{storage_key}" كما تنص المواصفة، فالمفتاح يظهر داخل
       الرابط حتمًا. وهذا ليس تسريبًا: الرابط نفسه هو وسيلة الوصول
       المقصودة، والمزوّدات الحقيقية أيضًا تُضمّن مسار الكائن في روابطها
       الموقَّعة. المحمي هو الحقل الخام الذي يُستخدم للوصول المباشر
       المستقل عن الرابط المؤقّت.
    """
    contractor_user, _ = contractor
    photos_svc.upload_job_photo(
        contractor_user, job.id, PhotoType.BEFORE, b"bytes", "image/jpeg"
    )

    r = client.get(f"/api/bookings/{job.booking_id}/job", **auth(customer))

    assert r.status_code == 200, r.content
    photo = r.json()["photos"][0]

    assert photo["storage_key"] is None
    assert photo["signed_url"].startswith(FAKE_URL_PREFIX)


@pytest.mark.django_db
def test_storage_key_field_is_null_for_every_non_admin_role(
    client, customer, contractor, other_customer, job
):
    """
    🔒 الضمان الحقيقي عبر الأدوار: الحقل الخام لا يُملأ إلا للإدارة.

    يبقى هذا الفحص صحيحًا حتى بعد استبدال الـadapter الوهمي بمزوّد حقيقي،
    لأنه لا يعتمد على شكل الرابط إطلاقًا.
    """
    contractor_user, _ = contractor
    photos_svc.upload_job_photo(
        contractor_user, job.id, PhotoType.BEFORE, b"bytes", "image/jpeg"
    )

    for actor in (customer, contractor_user):
        body = client.get(
            f"/api/bookings/{job.booking_id}/job", **auth(actor)
        ).json()
        assert body["photos"][0]["storage_key"] is None, actor.role


@pytest.mark.django_db
def test_storage_key_hidden_from_contractor(client, contractor, job):
    """المقاول أيضًا لا يرى المرجع الخام — الإدارة وحدها."""
    contractor_user, _ = contractor
    photos_svc.upload_job_photo(
        contractor_user, job.id, PhotoType.BEFORE, b"bytes", "image/jpeg"
    )

    r = client.get(f"/api/bookings/{job.booking_id}/job", **auth(contractor_user))

    assert r.status_code == 200, r.content
    assert r.json()["photos"][0]["storage_key"] is None


@pytest.mark.django_db
def test_storage_key_visible_to_admin(client, admin_user, contractor, job):
    contractor_user, _ = contractor
    photos_svc.upload_job_photo(
        contractor_user, job.id, PhotoType.BEFORE, b"bytes", "image/jpeg"
    )
    stored = JobPhoto.objects.get(job=job)

    r = client.get(f"/api/bookings/{job.booking_id}/job", **auth(admin_user))

    assert r.status_code == 200, r.content
    assert r.json()["photos"][0]["storage_key"] == stored.storage_key


# ============================================================
# 5) صمّام الـadapter الوهمي
# ============================================================
def test_base_storage_adapter_is_abstract():
    with pytest.raises(TypeError):
        BaseStorageProviderAdapter()


def test_storage_adapter_resolved_from_settings(settings):
    adapter = get_storage_adapter()

    assert isinstance(adapter, FakeStorageAdapter)
    assert isinstance(adapter, BaseStorageProviderAdapter)
    assert settings.JOB_STORAGE_ADAPTER_CLASS.endswith("FakeStorageAdapter")


def test_fake_storage_refuses_to_run_in_production(settings):
    """🔒 نفس نمط FakePaymentAdapter و DevConsoleSMSAdapter."""
    settings.DEBUG = False
    settings.JOBS_ALLOW_FAKE_STORAGE_ADAPTER = False

    with pytest.raises(ImproperlyConfigured):
        FakeStorageAdapter()


def test_fake_storage_allowed_with_explicit_flag(settings):
    settings.DEBUG = False
    settings.JOBS_ALLOW_FAKE_STORAGE_ADAPTER = True

    assert isinstance(FakeStorageAdapter(), FakeStorageAdapter)


def test_fake_storage_discards_bytes_and_returns_synthetic_key():
    """⚠️ لا تخزين فعلي: البايتات تُهمَل، والمفتاح مُصطنع."""
    adapter = FakeStorageAdapter()

    result = adapter.upload(
        file_bytes=b"some-real-looking-bytes",
        content_type="image/png",
        path_hint="jobs/x/before",
    )

    assert isinstance(result, StorageUploadResult)
    assert result.storage_key.startswith("fake/")
    assert result.storage_key.endswith(".png")


def test_fake_signed_url_is_obviously_not_real():
    url = FakeStorageAdapter().get_signed_url("fake/abc.jpg")

    assert url == f"{FAKE_URL_PREFIX}fake/abc.jpg"
    assert ".local/" in url


def test_no_real_storage_sdk_anywhere():
    """
    ⚠️ لا boto3 ولا S3 SDK ولا كتابة قرص — الفحص على الاستيرادات (AST).
    """
    import ast
    import pathlib

    banned = ("boto3", "botocore", "s3transfer", "google.cloud", "azure",
              "requests", "httpx", "urllib", "shutil", "tempfile")

    for path in sorted(pathlib.Path("apps/jobs").rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)

        for name in imported:
            for bad in banned:
                assert bad not in name.lower(), f"{path} imports {name}"

        # لا فتح ملفات على القرص
        calls = {
            n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "open" not in calls, f"{path} opens a file directly"


# ============================================================
# 6) صلاحية عرض المهمة — الأدوار الأربعة
# ============================================================
@pytest.mark.django_db
def test_owner_customer_can_view_job(client, customer, job):
    r = client.get(f"/api/bookings/{job.booking_id}/job", **auth(customer))

    assert r.status_code == 200, r.content
    assert r.json()["status"] == JobStatus.IN_PROGRESS
    assert r.json()["booking_id"] == str(job.booking_id)


@pytest.mark.django_db
def test_assigned_contractor_can_view_job(client, contractor, job):
    contractor_user, _ = contractor

    r = client.get(f"/api/bookings/{job.booking_id}/job", **auth(contractor_user))

    assert r.status_code == 200, r.content


@pytest.mark.django_db
def test_admin_can_view_job(client, admin_user, job):
    r = client.get(f"/api/bookings/{job.booking_id}/job", **auth(admin_user))

    assert r.status_code == 200, r.content


@pytest.mark.django_db
def test_other_customer_cannot_view_job(client, other_customer, job):
    r = client.get(f"/api/bookings/{job.booking_id}/job", **auth(other_customer))

    assert r.status_code == 404
    assert r.json()["code"] == "job_not_found"


@pytest.mark.django_db
def test_other_contractor_cannot_view_job(client, other_contractor, job):
    intruder, _ = other_contractor

    r = client.get(f"/api/bookings/{job.booking_id}/job", **auth(intruder))

    assert r.status_code == 404


@pytest.mark.django_db
def test_unknown_booking_and_forbidden_are_indistinguishable(
    client, other_customer, job
):
    """🔒 نفس الرد للحالتين — لا كشف عن وجود المورد."""
    forbidden = client.get(f"/api/bookings/{job.booking_id}/job", **auth(other_customer))
    missing = client.get(f"/api/bookings/{uuid.uuid4()}/job", **auth(other_customer))

    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json() == missing.json()


@pytest.mark.django_db
def test_booking_without_job_returns_404(client, customer, prop, service_type):
    booking = Booking.objects.create(
        customer=customer, property=prop, status=BookingStatus.PENDING
    )

    r = client.get(f"/api/bookings/{booking.id}/job", **auth(customer))

    assert r.status_code == 404


@pytest.mark.django_db
def test_unauthenticated_view_returns_401(client, job):
    assert client.get(f"/api/bookings/{job.booking_id}/job").status_code == 401


# ============================================================
# 7) الطبقات
# ============================================================
def test_api_layer_does_not_touch_orm():
    """§43: تُحقَّق فعليًا قبل التقرير، لا بعده."""
    import inspect

    from apps.jobs.api import jobs as api_mod

    src = inspect.getsource(api_mod)

    assert ".objects." not in src
    assert "Job(" not in src
    assert "JobPhoto(" not in src


def test_api_layer_does_not_reimplement_permission_logic():
    import inspect

    from apps.jobs.api import jobs as api_mod

    src = inspect.getsource(api_mod)

    assert "customer_id ==" not in src
    assert "assigned_contractor" not in src
