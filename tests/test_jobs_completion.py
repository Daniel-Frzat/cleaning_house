"""
Job Completion Flow Tests — Jobs Domain (Change Set §36.1، §36.3)

يغطي: إعلان الإنجاز (دليل مصوَّر إلزامي، حالة صحيحة، المقاول المُسنَد)،
وتأكيد العميل (عميل الحجز بعينه، من الحالة الصحيحة وحدها)، وغياب أي آلية
تأكيد تلقائي أو أثر جانبي (Payout/إشعارات).
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.bookings.models import Booking, BookingServiceSelection, BookingStatus
from apps.contractors.models import AvailabilityStatus, ContractorProfile
from apps.jobs.models import Job, JobStatus, PhotoType
from apps.jobs.services import jobs as jobs_svc
from apps.jobs.services import photos as photos_svc
from apps.properties.models import Property, PropertyType
from apps.services.models import ServiceType


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


def make_contractor(phone):
    user = make_user(phone, role=ConfirmedRole.CONTRACTOR)
    profile = ContractorProfile.objects.create(
        user=user,
        business_name=f"Co {phone[-3:]}",
        availability_status=AvailabilityStatus.AVAILABLE,
    )
    return user, profile


@pytest.fixture
def customer(db):
    return make_user("+61400011001")


@pytest.fixture
def other_customer(db):
    """عميل شرعي آخر — دوره CUSTOMER لكنه ليس صاحب الحجز."""
    return make_user("+61400011002")


@pytest.fixture
def admin_user(db):
    return make_user("+61400011003", role=ConfirmedRole.ADMIN)


@pytest.fixture
def contractor(db):
    return make_contractor("+61400011004")


@pytest.fixture
def other_contractor(db):
    return make_contractor("+61400011005")


@pytest.fixture
def service_type(db):
    return ServiceType.objects.create(
        name="General Cleaning",
        room_price=Decimal("45.00"),
        base_price=Decimal("80.00"),
    )


@pytest.fixture
def job(customer, service_type, contractor):
    _, profile = contractor
    prop = Property.objects.create(
        owner=customer, label="Home", property_type=PropertyType.HOUSE
    )
    booking = Booking.objects.create(
        customer=customer,
        property=prop,
        status=BookingStatus.CONFIRMED,
        computed_price=Decimal("215.00"),
        assigned_contractor=profile,
    )
    BookingServiceSelection.objects.create(
        booking=booking, service_type=service_type, room_count=3
    )
    created = jobs_svc.create_job_for_booking(booking)
    # ⚠️ المهمة تُنشأ ASSIGNED؛ هذه الاختبارات تفترض عملًا جاريًا
    return jobs_svc.start_job(created, profile.user)


def add_photo(contractor_user, job, photo_type):
    return photos_svc.upload_job_photo(
        contractor_user, job.id, photo_type, b"bytes", "image/jpeg"
    )


def add_both_photos(contractor_user, job):
    add_photo(contractor_user, job, PhotoType.BEFORE)
    add_photo(contractor_user, job, PhotoType.AFTER)


# ============================================================
# 1) إعلان الإنجاز — الدليل المصوَّر إلزامي
# ============================================================
@pytest.mark.django_db
def test_mark_done_rejected_with_zero_photos(client, contractor, job):
    contractor_user, _ = contractor

    r = client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(contractor_user))

    assert r.status_code == 400, r.content
    assert r.json()["code"] == "missing_proof_photos"

    job.refresh_from_db()
    assert job.status == JobStatus.IN_PROGRESS
    assert job.marked_done_at is None


@pytest.mark.django_db
def test_mark_done_rejected_with_only_before(client, contractor, job):
    contractor_user, _ = contractor
    add_photo(contractor_user, job, PhotoType.BEFORE)

    r = client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(contractor_user))

    assert r.status_code == 400, r.content
    assert "AFTER" in r.json()["detail"]

    job.refresh_from_db()
    assert job.status == JobStatus.IN_PROGRESS


@pytest.mark.django_db
def test_mark_done_rejected_with_only_after(client, contractor, job):
    contractor_user, _ = contractor
    add_photo(contractor_user, job, PhotoType.AFTER)

    r = client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(contractor_user))

    assert r.status_code == 400, r.content
    assert "BEFORE" in r.json()["detail"]

    job.refresh_from_db()
    assert job.status == JobStatus.IN_PROGRESS


@pytest.mark.django_db
def test_mark_done_succeeds_with_both_photo_types(client, contractor, job):
    contractor_user, _ = contractor
    add_both_photos(contractor_user, job)

    before = timezone.now()
    r = client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(contractor_user))

    assert r.status_code == 200, r.content
    assert r.json()["status"] == JobStatus.AWAITING_CUSTOMER_CONFIRMATION

    job.refresh_from_db()
    assert job.status == JobStatus.AWAITING_CUSTOMER_CONFIRMATION
    assert job.marked_done_at is not None
    assert job.marked_done_at >= before
    # ⚠️ لا يُكمل المهمة — الإكمال فعل العميل
    assert job.confirmed_at is None


@pytest.mark.django_db
def test_mark_done_succeeds_with_multiple_photos_of_each_type(
    client, contractor, job
):
    """"واحدة على الأقل" لا "واحدة بالضبط"."""
    contractor_user, _ = contractor
    add_photo(contractor_user, job, PhotoType.BEFORE)
    add_photo(contractor_user, job, PhotoType.BEFORE)
    add_photo(contractor_user, job, PhotoType.AFTER)

    r = client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(contractor_user))

    assert r.status_code == 200, r.content


@pytest.mark.django_db
def test_photo_requirement_enforced_in_service_layer(db, contractor, job):
    contractor_user, _ = contractor

    with pytest.raises(jobs_svc.MissingProofPhotosError):
        jobs_svc.mark_job_done(job, contractor_user)


# ============================================================
# 2) إعلان الإنجاز — الحالة والصلاحية
# ============================================================
@pytest.mark.django_db
@pytest.mark.parametrize(
    "status", [JobStatus.AWAITING_CUSTOMER_CONFIRMATION, JobStatus.COMPLETED]
)
def test_mark_done_rejected_when_not_in_progress(client, contractor, job, status):
    contractor_user, _ = contractor
    add_both_photos(contractor_user, job)
    Job.objects.filter(pk=job.pk).update(status=status)

    r = client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(contractor_user))

    assert r.status_code == 409, r.content
    assert r.json()["code"] == "invalid_job_status"


@pytest.mark.django_db
def test_mark_done_is_not_repeatable(client, contractor, job):
    """الإعلان مرة واحدة — التكرار يتعارض مع الحالة الجديدة."""
    contractor_user, _ = contractor
    add_both_photos(contractor_user, job)

    first = client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(contractor_user))
    second = client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(contractor_user))

    assert first.status_code == 200
    assert second.status_code == 409, second.content


@pytest.mark.django_db
def test_mark_done_rejected_for_unassigned_contractor(
    client, contractor, other_contractor, job
):
    """🔒 المقاول غير المُسنَد — 403."""
    contractor_user, _ = contractor
    intruder, _ = other_contractor
    add_both_photos(contractor_user, job)

    r = client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(intruder))

    assert r.status_code == 403, r.content

    job.refresh_from_db()
    assert job.status == JobStatus.IN_PROGRESS


@pytest.mark.django_db
@pytest.mark.parametrize("role", [ConfirmedRole.CUSTOMER, ConfirmedRole.ADMIN])
def test_mark_done_rejected_for_non_contractor_roles(client, db, contractor, job, role):
    contractor_user, _ = contractor
    add_both_photos(contractor_user, job)
    actor = make_user("+61400011100", role=role)

    r = client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(actor))

    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_mark_done_unknown_job_returns_404(client, contractor):
    contractor_user, _ = contractor

    r = client.post(f"/api/contractor/jobs/{uuid.uuid4()}/mark-done", **auth(contractor_user))

    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_mark_done_unauthenticated_returns_401(client, job):
    assert client.post(f"/api/contractor/jobs/{job.id}/mark-done").status_code == 401


# ============================================================
# 3) تأكيد العميل — الحالة
# ============================================================
@pytest.mark.django_db
def test_confirm_succeeds_from_awaiting_confirmation(client, customer, contractor, job):
    contractor_user, _ = contractor
    add_both_photos(contractor_user, job)
    jobs_svc.mark_job_done(job, contractor_user)

    before = timezone.now()
    r = client.post(f"/api/bookings/{job.booking_id}/job/confirm", **auth(customer))

    assert r.status_code == 200, r.content
    assert r.json()["status"] == JobStatus.COMPLETED

    job.refresh_from_db()
    assert job.status == JobStatus.COMPLETED
    assert job.confirmed_at is not None
    assert job.confirmed_at >= before


@pytest.mark.django_db
@pytest.mark.parametrize("status", [JobStatus.IN_PROGRESS, JobStatus.COMPLETED])
def test_confirm_rejected_when_not_awaiting_confirmation(client, customer, job, status):
    Job.objects.filter(pk=job.pk).update(status=status)

    r = client.post(f"/api/bookings/{job.booking_id}/job/confirm", **auth(customer))

    assert r.status_code == 409, r.content
    assert r.json()["code"] == "invalid_job_status"


@pytest.mark.django_db
def test_confirm_is_not_repeatable(client, customer, contractor, job):
    contractor_user, _ = contractor
    add_both_photos(contractor_user, job)
    jobs_svc.mark_job_done(job, contractor_user)

    first = client.post(f"/api/bookings/{job.booking_id}/job/confirm", **auth(customer))
    second = client.post(f"/api/bookings/{job.booking_id}/job/confirm", **auth(customer))

    assert first.status_code == 200
    assert second.status_code == 409, second.content


@pytest.mark.django_db
def test_customer_cannot_confirm_before_contractor_marks_done(client, customer, job):
    """العميل لا يستبق المقاول — الترتيب محفوظ."""
    r = client.post(f"/api/bookings/{job.booking_id}/job/confirm", **auth(customer))

    assert r.status_code == 409, r.content

    job.refresh_from_db()
    assert job.status == JobStatus.IN_PROGRESS
    assert job.confirmed_at is None


# ============================================================
# 4) تأكيد العميل — العميل بعينه حصرًا (§36.3)
# ============================================================
@pytest.mark.django_db
def test_confirm_rejected_for_another_legitimate_customer(
    client, customer, other_customer, contractor, job
):
    """
    🔒 §36.3: عميل الحجز بعينه — لا يكفي أن يكون الدور CUSTOMER.
    """
    contractor_user, _ = contractor
    add_both_photos(contractor_user, job)
    jobs_svc.mark_job_done(job, contractor_user)

    r = client.post(f"/api/bookings/{job.booking_id}/job/confirm", **auth(other_customer))

    assert r.status_code == 403, r.content

    job.refresh_from_db()
    assert job.status == JobStatus.AWAITING_CUSTOMER_CONFIRMATION
    assert job.confirmed_at is None


@pytest.mark.django_db
def test_admin_cannot_confirm_on_behalf_of_customer(
    client, admin_user, contractor, job
):
    """
    ⚠️ §36.3 يمنع البديل الإداري صراحةً — لا تفويض ولا تجاوز.
    """
    contractor_user, _ = contractor
    add_both_photos(contractor_user, job)
    jobs_svc.mark_job_done(job, contractor_user)

    r = client.post(f"/api/bookings/{job.booking_id}/job/confirm", **auth(admin_user))

    assert r.status_code == 403, r.content

    job.refresh_from_db()
    assert job.status == JobStatus.AWAITING_CUSTOMER_CONFIRMATION


@pytest.mark.django_db
def test_contractor_cannot_confirm_own_work(client, contractor, job):
    """المقاول لا يؤكّد عمله بنفسه — وإلا لانعدم معنى التحقق."""
    contractor_user, _ = contractor
    add_both_photos(contractor_user, job)
    jobs_svc.mark_job_done(job, contractor_user)

    r = client.post(f"/api/bookings/{job.booking_id}/job/confirm", **auth(contractor_user))

    assert r.status_code == 403, r.content

    job.refresh_from_db()
    assert job.status == JobStatus.AWAITING_CUSTOMER_CONFIRMATION


@pytest.mark.django_db
def test_ownership_enforced_in_service_layer(db, other_customer, contractor, job):
    contractor_user, _ = contractor
    add_both_photos(contractor_user, job)
    jobs_svc.mark_job_done(job, contractor_user)

    with pytest.raises(jobs_svc.JobPermissionError):
        jobs_svc.confirm_job_completion(job, other_customer)


@pytest.mark.django_db
def test_confirm_unknown_booking_returns_404(client, customer):
    r = client.post(f"/api/bookings/{uuid.uuid4()}/job/confirm", **auth(customer))

    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_confirm_unauthenticated_returns_401(client, job):
    assert client.post(f"/api/bookings/{job.booking_id}/job/confirm").status_code == 401


# ============================================================
# 5) المسار الكامل
# ============================================================
@pytest.mark.django_db
def test_full_completion_lifecycle(client, customer, contractor, job):
    """IN_PROGRESS → AWAITING_CUSTOMER_CONFIRMATION → COMPLETED."""
    contractor_user, _ = contractor

    assert job.status == JobStatus.IN_PROGRESS

    add_both_photos(contractor_user, job)
    client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(contractor_user))
    job.refresh_from_db()
    assert job.status == JobStatus.AWAITING_CUSTOMER_CONFIRMATION

    client.post(f"/api/bookings/{job.booking_id}/job/confirm", **auth(customer))
    job.refresh_from_db()
    assert job.status == JobStatus.COMPLETED
    assert job.marked_done_at is not None and job.confirmed_at is not None
    assert job.confirmed_at >= job.marked_done_at


@pytest.mark.django_db
def test_photos_frozen_after_mark_done(client, contractor, job):
    """الأدلة تتجمّد بعد الإعلان — متسق مع المرحلة السابقة."""
    contractor_user, _ = contractor
    add_both_photos(contractor_user, job)
    jobs_svc.mark_job_done(job, contractor_user)

    with pytest.raises(photos_svc.JobNotAcceptingPhotosError):
        add_photo(contractor_user, job, PhotoType.AFTER)


# ============================================================
# 6) لا تأكيد تلقائي ولا أثر جانبي
# ============================================================
def test_no_timeout_or_auto_confirmation_anywhere():
    """
    ⚠️ §36.3: لا آلية زمنية تنقل الحالة. الفحص على الكود المنفَّذ (AST)
       عبر كل النطاقات — لا مهمة دورية تستدعي التأكيد أو تنقل الحالة.
    """
    import ast
    import pathlib

    scheduling_names = {
        "shared_task",
        "periodic_task",
        "crontab",
        "apply_async",
        "delay",
    }

    for path in sorted(pathlib.Path("apps").rglob("*.py")):
        if "__pycache__" in str(path) or "migrations" in str(path):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        identifiers |= {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
        }

        # أي ملف يحمل بدائية جدولة يجب ألا يمس تأكيد المهام إطلاقًا
        if identifiers & scheduling_names:
            assert "confirm_job_completion" not in source, (
                f"{path} schedules job confirmation"
            )
            assert "JobStatus.COMPLETED" not in source, (
                f"{path} auto-transitions job status"
            )


def test_no_celery_task_references_job_confirmation():
    """لا مهمة Celery قائمة تشير إلى إكمال المهام."""
    import pathlib

    for path in sorted(pathlib.Path("apps").rglob("tasks.py")):
        if "__pycache__" in str(path):
            continue
        source = path.read_text(encoding="utf-8")
        assert "confirm_job_completion" not in source, path
        assert "JobStatus" not in source, path
        assert "AWAITING_CUSTOMER_CONFIRMATION" not in source, path


def test_confirm_triggers_payout_only_as_isolated_post_commit_effect():
    """
    ⚠️ تغيّر النطاق في مرحلة Payout (§36.5): الدفع صار موصولًا — لكن
       كأثر جانبي معزول عبر transaction.on_commit، لا داخل الانتقال.

    🔒 ما يبقى محفوظًا من الحارس الأصلي:
       - جسم confirm_job_completion نفسه لا يستدعي الدفع مباشرة، ولا
         يُشعر، ولا يُجدول: يسجّل الأثر على on_commit فقط.
       - لا إشعارات ولا Celery في وحدة المهام إطلاقًا.
       - استيراد نطاق الدفع يحدث داخل الدالة المؤجَّلة (lazy) لا على
         مستوى الوحدة، فلا اقتران وقت الاستيراد.
    """
    import ast
    import inspect

    from apps.jobs.services import jobs as mod

    tree = ast.parse(inspect.getsource(mod))

    # 1) لا استيراد على مستوى الوحدة لإشعارات/Celery/فواتير
    module_imports = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            module_imports.add(node.module)
        elif isinstance(node, ast.Import):
            module_imports.update(a.name for a in node.names)

    for name in module_imports:
        low = name.lower()
        for forbidden in ("notification", "notify", "celery", "invoice", "payout"):
            assert forbidden not in low, (
                f"jobs service imports {name} at module level"
            )

    # 2) جسم confirm_job_completion: لا دفع مباشر ولا إشعار ولا جدولة
    func = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "confirm_job_completion"
    )
    called = {
        n.func.attr for n in ast.walk(func)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    called |= {
        n.func.id for n in ast.walk(func)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }

    for forbidden in (
        "release_payout_for_booking", "send", "notify", "delay", "apply_async",
        "charge",
    ):
        assert forbidden not in called, f"confirm_job_completion calls {forbidden}"

    # 3) الأثر مسجَّل على on_commit لا منفَّذ فورًا
    assert "on_commit" in called, "payout must be deferred to transaction.on_commit"


def test_payout_domain_exists_without_any_batching():
    """
    ⚠️ تغيّر النطاق في مرحلة Payout: النطاق موجود الآن.

    🔒 ما يبقى محفوظًا: القرار المحسوم أن الدفع فوري لكل حجز — فلا
       كيان تجميع ولا حالة مؤجَّلة في نطاق الدفع.
    """
    import pathlib

    from apps.payouts.models import PayoutStatus

    assert pathlib.Path("apps/payouts").is_dir()
    assert set(PayoutStatus.values) == {"PENDING", "SUCCEEDED", "FAILED"}

    for forbidden in ("BATCHED", "SCHEDULED", "QUEUED"):
        assert forbidden not in PayoutStatus.values


def test_api_layer_still_has_no_orm():
    """§43: يبقى الفحص قائمًا بعد إضافة المسارات."""
    import inspect

    from apps.jobs.api import jobs as api_mod

    src = inspect.getsource(api_mod)

    assert ".objects." not in src
    assert "Job(" not in src
