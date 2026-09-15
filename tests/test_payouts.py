"""
Payout Tests — Payout Domain (Change Set §36.5؛ Infra §14/§16)

يغطي: الدفع الناجح بالمبلغ الكامل، التكرارية الصارمة (بقياس عدد
استدعاءات الـadapter فعليًا)، رفض الدفع قبل COMPLETED، فشل المزوّد بلا
أثر على Job/Booking، غياب أي تجميع، وصمّام الـadapter الوهمي.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, transaction
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
from apps.payouts.adapters import get_payout_adapter
from apps.payouts.adapters.base import BasePayoutProviderAdapter, PayoutResult
from apps.payouts.adapters.fake_adapter import (
    FAILURE_SENTINEL_AMOUNT,
    FakePayoutAdapter,
)
from apps.payouts.models import Payout, PayoutStatus
from apps.payouts.services import payouts as psvc
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
    return make_user("+61400012001")


@pytest.fixture
def admin_user(db):
    return make_user("+61400012002", role=ConfirmedRole.ADMIN)


@pytest.fixture
def contractor(db):
    return make_contractor("+61400012003")


@pytest.fixture
def other_contractor(db):
    return make_contractor("+61400012004")


@pytest.fixture
def service_type(db):
    return ServiceType.objects.create(
        name="General Cleaning",
        room_price=Decimal("45.00"),
        base_price=Decimal("80.00"),
    )


def make_booking(customer, service_type, profile, price=Decimal("215.00")):
    prop = Property.objects.create(
        owner=customer, label="Home", property_type=PropertyType.HOUSE
    )
    booking = Booking.objects.create(
        customer=customer,
        property=prop,
        status=BookingStatus.CONFIRMED,
        computed_price=price,
        assigned_contractor=profile,
    )
    BookingServiceSelection.objects.create(
        booking=booking, service_type=service_type, room_count=3
    )
    return booking


def make_completed_job(customer, service_type, profile, price=Decimal("215.00")):
    """حجز مؤكَّد + مهمة وصلت COMPLETED (بلا hook الدفع)."""
    booking = make_booking(customer, service_type, profile, price)
    job = Job.objects.create(
        booking=booking,
        status=JobStatus.COMPLETED,
        marked_done_at=timezone.now(),
        confirmed_at=timezone.now(),
    )
    return booking, job


@pytest.fixture
def completed(customer, service_type, contractor):
    _, profile = contractor
    return make_completed_job(customer, service_type, profile)


class CountingAdapter(BasePayoutProviderAdapter):
    """adapter يعدّ استدعاءاته الفعلية — لإثبات التكرارية."""

    calls = 0
    keys = []

    def payout(self, amount, contractor_reference, idempotency_key):
        type(self).calls += 1
        type(self).keys.append(idempotency_key)
        return PayoutResult(success=True, provider_reference="counted")

    @classmethod
    def reset(cls):
        cls.calls = 0
        cls.keys = []


# ============================================================
# 1) الدفع الناجح — المبلغ الكامل بلا عمولة
# ============================================================
@pytest.mark.django_db
def test_successful_payout(completed, contractor):
    booking, _ = completed
    contractor_user, _ = contractor

    payout = psvc.release_payout_for_booking(booking)

    assert payout.status == PayoutStatus.SUCCEEDED
    assert payout.amount == booking.computed_price == Decimal("215.00")
    assert payout.contractor_id == contractor_user.id
    assert payout.provider_reference.startswith("fake_payout_")
    assert payout.failure_reason is None


@pytest.mark.django_db
def test_payout_amount_is_full_price_zero_commission(
    customer, service_type, contractor
):
    """📌 §36.5: المبلغ الكامل — صفر عمولة، لا خصم بأي نسبة."""
    _, profile = contractor
    booking, _ = make_completed_job(
        customer, service_type, profile, price=Decimal("1234.56")
    )

    payout = psvc.release_payout_for_booking(booking)

    assert payout.amount == Decimal("1234.56")
    assert payout.amount == booking.computed_price


@pytest.mark.django_db
def test_contractor_is_copied_at_creation_not_derived_later(
    completed, contractor, other_contractor
):
    """
    📌 يُنسخ المقاول وقت الإنشاء: تغيير إسناد الحجز لاحقًا لا يحوّل
       المستحِق، ولا حتى تفريغ الإسناد.
    """
    booking, _ = completed
    original_user, _ = contractor
    _, other_profile = other_contractor

    payout = psvc.release_payout_for_booking(booking)
    assert payout.contractor_id == original_user.id

    booking.assigned_contractor = other_profile
    booking.save(update_fields=["assigned_contractor"])
    payout.refresh_from_db()
    assert payout.contractor_id == original_user.id

    booking.assigned_contractor = None
    booking.save(update_fields=["assigned_contractor"])
    payout.refresh_from_db()
    assert payout.contractor_id == original_user.id


@pytest.mark.django_db
def test_amount_mismatch_rejected_at_model_level(completed, contractor):
    """🔒 الإنفاذ في clean() لا طبقة الخدمة وحدها (نفس §43)."""
    booking, _ = completed
    contractor_user, _ = contractor

    payout = Payout(
        booking=booking,
        contractor=contractor_user,
        amount=Decimal("200.00"),  # أقل من 215.00 — "عمولة" مدسوسة
    )

    with pytest.raises(ValidationError) as exc:
        payout.full_clean()
    assert "amount" in exc.value.message_dict


@pytest.mark.django_db
def test_booking_without_price_cannot_be_paid(customer, service_type, contractor):
    _, profile = contractor
    booking, _ = make_completed_job(customer, service_type, profile)
    Booking.objects.filter(pk=booking.pk).update(computed_price=None)
    booking.refresh_from_db()

    with pytest.raises(psvc.MissingPriceError):
        psvc.release_payout_for_booking(booking)

    assert Payout.objects.count() == 0


@pytest.mark.django_db
def test_booking_without_contractor_cannot_be_paid(
    customer, service_type, contractor
):
    _, profile = contractor
    booking, _ = make_completed_job(customer, service_type, profile)
    Booking.objects.filter(pk=booking.pk).update(assigned_contractor=None)
    booking.refresh_from_db()

    with pytest.raises(psvc.MissingContractorError):
        psvc.release_payout_for_booking(booking)

    assert Payout.objects.count() == 0


# ============================================================
# 2) المُحفِّز الوحيد: Job.COMPLETED
# ============================================================
@pytest.mark.django_db
@pytest.mark.parametrize(
    "status", [JobStatus.IN_PROGRESS, JobStatus.AWAITING_CUSTOMER_CONFIRMATION]
)
def test_payout_rejected_before_job_completed(
    customer, service_type, contractor, status
):
    _, profile = contractor
    booking = make_booking(customer, service_type, profile)
    Job.objects.create(booking=booking, status=status)

    with pytest.raises(psvc.JobNotCompletedError):
        psvc.release_payout_for_booking(booking)

    assert Payout.objects.count() == 0


@pytest.mark.django_db
def test_payout_rejected_when_no_job_exists(customer, service_type, contractor):
    """لا مهمة = لا استحقاق، ولا يُستنتج من Booking.status."""
    _, profile = contractor
    booking = make_booking(customer, service_type, profile)

    with pytest.raises(psvc.JobNotCompletedError):
        psvc.release_payout_for_booking(booking)

    assert Payout.objects.count() == 0


@pytest.mark.django_db
def test_confirmed_booking_alone_is_not_enough(customer, service_type, contractor):
    """
    📌 المصدر الوحيد للتحقق هو Job.status — لا Booking.status ولا Payment.
    """
    _, profile = contractor
    booking = make_booking(customer, service_type, profile)
    assert booking.status == BookingStatus.CONFIRMED

    with pytest.raises(psvc.JobNotCompletedError):
        psvc.release_payout_for_booking(booking)


# ============================================================
# 3) التكرارية الصارمة — بقياس استدعاءات الـadapter فعليًا
# ============================================================
@pytest.mark.django_db
def test_second_call_creates_no_payout_and_no_adapter_call(completed, monkeypatch):
    """
    🔒 منهجية §43: استدعاءان متتاليان = استدعاء adapter واحد فقط.
    """
    booking, _ = completed
    CountingAdapter.reset()
    monkeypatch.setattr(psvc, "get_payout_adapter", lambda: CountingAdapter())

    first = psvc.release_payout_for_booking(booking)
    assert first.status == PayoutStatus.SUCCEEDED
    assert CountingAdapter.calls == 1

    with pytest.raises(psvc.PayoutAlreadyExistsError):
        psvc.release_payout_for_booking(booking)

    assert CountingAdapter.calls == 1, "الـadapter استُدعي مرتين — دفع مزدوج"
    assert Payout.objects.filter(booking=booking).count() == 1


@pytest.mark.django_db
def test_existing_failed_payout_also_blocks_retry(
    customer, service_type, contractor, monkeypatch
):
    """
    🔒 الدفعة الفاشلة تمنع المحاولة الثانية أيضًا — "بأي حالة".
    """
    _, profile = contractor
    booking, _ = make_completed_job(
        customer, service_type, profile, price=FAILURE_SENTINEL_AMOUNT
    )

    failed = psvc.release_payout_for_booking(booking)
    assert failed.status == PayoutStatus.FAILED

    CountingAdapter.reset()
    monkeypatch.setattr(psvc, "get_payout_adapter", lambda: CountingAdapter())

    with pytest.raises(psvc.PayoutAlreadyExistsError):
        psvc.release_payout_for_booking(booking)

    assert CountingAdapter.calls == 0, "أعاد الاستدعاء رغم وجود دفعة فاشلة"
    assert Payout.objects.filter(booking=booking).count() == 1


@pytest.mark.django_db
def test_second_payout_rejected_by_db_constraint(completed, contractor):
    """🔒 دفاع في العمق: قيد OneToOne يمنع الصف الثاني."""
    booking, _ = completed
    contractor_user, _ = contractor
    psvc.release_payout_for_booking(booking)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payout.objects.create(
                booking=booking,
                contractor=contractor_user,
                amount=booking.computed_price,
            )

    assert Payout.objects.filter(booking=booking).count() == 1


@pytest.mark.django_db
def test_idempotency_key_is_stable_and_booking_derived(completed):
    booking, _ = completed

    first = psvc.build_idempotency_key(booking)
    second = psvc.build_idempotency_key(booking)

    assert first == second
    assert str(booking.id) in first


@pytest.mark.django_db
def test_idempotency_key_differs_from_payment_key(completed):
    """
    مفتاح الدفع للمقاول يجب أن يغاير مفتاح الشحن من العميل، وإلا تصادما
    عند مزوّد يخدم الاتجاهين.
    """
    from apps.payments.services.payments import build_idempotency_key as payment_key

    booking, _ = completed

    assert psvc.build_idempotency_key(booking) != payment_key(booking)


# ============================================================
# 4) الفشل — لا أثر على Job أو Booking
# ============================================================
@pytest.mark.django_db
def test_failed_payout_records_failure(customer, service_type, contractor):
    _, profile = contractor
    booking, _ = make_completed_job(
        customer, service_type, profile, price=FAILURE_SENTINEL_AMOUNT
    )

    payout = psvc.release_payout_for_booking(booking)

    assert payout.status == PayoutStatus.FAILED
    assert payout.failure_reason
    assert payout.provider_reference is None


@pytest.mark.django_db
def test_failed_payout_leaves_job_and_booking_untouched(
    customer, service_type, contractor
):
    """
    ⚠️ سياسة غير محسومة: لا إعادة محاولة ولا تنبيه ولا تغيير حالة.
    """
    _, profile = contractor
    booking, job = make_completed_job(
        customer, service_type, profile, price=FAILURE_SENTINEL_AMOUNT
    )

    psvc.release_payout_for_booking(booking)

    job.refresh_from_db()
    booking.refresh_from_db()
    assert job.status == JobStatus.COMPLETED
    assert booking.status == BookingStatus.CONFIRMED
    assert booking.computed_price == FAILURE_SENTINEL_AMOUNT


@pytest.mark.django_db
def test_payout_service_never_mutates_job_or_booking():
    """الفحص على الكود: لا إسناد لحالة Job أو Booking في طبقة الدفع."""
    import inspect

    from apps.payouts.services import payouts as mod

    src = inspect.getsource(mod)

    assert "job.status =" not in src
    assert "booking.status =" not in src
    assert "JobStatus.COMPLETED =" not in src


# ============================================================
# 5) لا تجميع ولا دفعات مجمَّعة — قرار محسوم
# ============================================================
@pytest.mark.django_db
def test_status_enum_has_no_batch_or_schedule_states():
    assert set(PayoutStatus.values) == {"PENDING", "SUCCEEDED", "FAILED"}

    for forbidden in ("BATCHED", "SCHEDULED", "QUEUED", "GROUPED", "HELD"):
        assert forbidden not in PayoutStatus.values


def test_no_batch_entity_anywhere_in_payouts():
    """
    ⚠️ ممنوع أي كيان تجميع. الفحص على الأسماء المعرَّفة فعلًا (AST).
    """
    import ast
    import pathlib

    for path in sorted(pathlib.Path("apps/payouts").rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                low = node.name.lower()
                assert "batch" not in low, f"{path} defines {node.name}"
                assert "schedule" not in low, f"{path} defines {node.name}"
            if isinstance(node, ast.FunctionDef):
                low = node.name.lower()
                assert "batch" not in low, f"{path} defines {node.name}()"


@pytest.mark.django_db
def test_payout_model_has_no_batch_field():
    names = {f.name for f in Payout._meta.get_fields()}

    for forbidden in ("batch", "batch_id", "payout_batch", "scheduled_for", "group"):
        assert forbidden not in names


def test_no_celery_task_in_payouts():
    """الدفع فوري — لا مهمة دورية تجمع أو تؤجّل."""
    import pathlib

    assert not list(pathlib.Path("apps/payouts").glob("tasks.py"))

    for path in pathlib.Path("apps/payouts").rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        src = path.read_text(encoding="utf-8")
        assert "shared_task" not in src, path
        assert "apply_async" not in src, path


# ============================================================
# 6) الـadapter — تجريدي، من الإعدادات، محروس
# ============================================================
def test_base_adapter_is_abstract():
    with pytest.raises(TypeError):
        BasePayoutProviderAdapter()


def test_base_adapter_has_real_abstract_methods():
    assert BasePayoutProviderAdapter.__abstractmethods__ == frozenset({"payout"})


def test_adapter_resolved_from_settings(settings):
    adapter = get_payout_adapter()

    assert isinstance(adapter, FakePayoutAdapter)
    assert isinstance(adapter, BasePayoutProviderAdapter)
    assert settings.PAYOUT_PROVIDER_ADAPTER_CLASS.endswith("FakePayoutAdapter")


def test_fake_adapter_is_only_concrete_subclass():
    subs = BasePayoutProviderAdapter.__subclasses__()
    names = {c.__name__ for c in subs}

    # CountingAdapter معرَّف في هذا الملف للاختبار فقط، لا في كود الإنتاج
    assert "FakePayoutAdapter" in names
    production = names - {"CountingAdapter"}
    assert production == {"FakePayoutAdapter"}, production


def test_fake_adapter_refuses_in_production(settings):
    settings.DEBUG = False
    settings.PAYOUTS_ALLOW_FAKE_ADAPTER = False

    with pytest.raises(ImproperlyConfigured):
        FakePayoutAdapter()


def test_fake_adapter_allowed_with_explicit_flag(settings):
    settings.DEBUG = False
    settings.PAYOUTS_ALLOW_FAKE_ADAPTER = True

    assert isinstance(FakePayoutAdapter(), FakePayoutAdapter)


def test_fake_adapter_failure_sentinel():
    result = FakePayoutAdapter().payout(
        amount=FAILURE_SENTINEL_AMOUNT, contractor_reference="x", idempotency_key="k"
    )

    assert result.success is False
    assert result.provider_reference is None


def test_no_real_psp_or_network_import_anywhere():
    """
    ⚠️ لا مكتبة PSP/HTTP/شبكة في كود الإنتاج — فحص AST لا grep.
    """
    import ast
    import pathlib

    banned = (
        "stripe", "requests", "httpx", "urllib", "http.client", "socket",
        "paypal", "wise", "airwallex", "boto3", "botocore", "aiohttp",
        "braintree", "adyen", "square", "plaid",
    )

    for path in sorted(pathlib.Path("apps/payouts").rglob("*.py")):
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


# ============================================================
# 7) الـhook: التأكيد يُطلق الدفع
# ============================================================
@pytest.mark.django_db
def test_confirming_job_triggers_payout(
    client, customer, service_type, contractor, django_capture_on_commit_callbacks
):
    """📌 §36.5: تأكيد العميل هو المُحفِّز."""
    contractor_user, profile = contractor
    booking = make_booking(customer, service_type, profile)
    job = jobs_svc.create_job_for_booking(booking)
    # ⚠️ المهمة تُنشأ ASSIGNED — تبدأ صراحةً قبل إعلان الإنجاز
    job = jobs_svc.start_job(job, contractor_user)
    photos_svc.upload_job_photo(
        contractor_user, job.id, PhotoType.BEFORE, b"b", "image/jpeg"
    )
    photos_svc.upload_job_photo(
        contractor_user, job.id, PhotoType.AFTER, b"a", "image/jpeg"
    )
    jobs_svc.mark_job_done(job, contractor_user)

    assert Payout.objects.filter(booking=booking).count() == 0

    with django_capture_on_commit_callbacks(execute=True):
        jobs_svc.confirm_job_completion(job, customer)

    payout = Payout.objects.get(booking=booking)
    assert payout.status == PayoutStatus.SUCCEEDED
    assert payout.amount == booking.computed_price
    assert payout.contractor_id == contractor_user.id


@pytest.mark.django_db
def test_no_payout_before_confirmation(
    customer, service_type, contractor, django_capture_on_commit_callbacks
):
    contractor_user, profile = contractor
    booking = make_booking(customer, service_type, profile)
    job = jobs_svc.create_job_for_booking(booking)
    # ⚠️ المهمة تُنشأ ASSIGNED — تبدأ صراحةً قبل إعلان الإنجاز
    job = jobs_svc.start_job(job, contractor_user)
    photos_svc.upload_job_photo(
        contractor_user, job.id, PhotoType.BEFORE, b"b", "image/jpeg"
    )
    photos_svc.upload_job_photo(
        contractor_user, job.id, PhotoType.AFTER, b"a", "image/jpeg"
    )

    with django_capture_on_commit_callbacks(execute=True):
        jobs_svc.mark_job_done(job, contractor_user)

    assert Payout.objects.count() == 0


# ============================================================
# 8) نقطة النهاية — صلاحية وحجب
# ============================================================
@pytest.mark.django_db
def test_payee_contractor_can_view_payout(client, completed, contractor):
    booking, _ = completed
    contractor_user, _ = contractor
    psvc.release_payout_for_booking(booking)

    r = client.get(f"/api/bookings/{booking.id}/payout", **auth(contractor_user))

    assert r.status_code == 200, r.content
    assert Decimal(r.json()["amount"]) == Decimal("215.00")
    assert r.json()["status"] == PayoutStatus.SUCCEEDED


@pytest.mark.django_db
def test_provider_reference_key_absent_for_contractor(client, completed, contractor):
    """
    🔒 الحجب هيكلي: المفتاح **غائب** من JSON لغير الإدارة، لا موجودًا
       بقيمة null. الفحص على المفاتيح نفسها لا على القيمة.
    """
    booking, _ = completed
    contractor_user, _ = contractor
    payout = psvc.release_payout_for_booking(booking)
    assert payout.provider_reference  # موجود فعلًا في قاعدة البيانات

    r = client.get(f"/api/bookings/{booking.id}/payout", **auth(contractor_user))
    body = r.json()

    assert "provider_reference" not in body.keys()
    assert payout.provider_reference not in r.content.decode()


@pytest.mark.django_db
def test_admin_keeps_provider_reference_key_even_when_none(
    client, customer, service_type, contractor, admin_user
):
    """
    🔒 الفرق المقصود: المفتاح يظهر للإدارة حتى بقيمة None (دفعة فاشلة
       بلا مرجع) — وهذا يغاير غيابه الكامل لغير الإدارة.
    """
    _, profile = contractor
    booking, _ = make_completed_job(
        customer, service_type, profile, price=FAILURE_SENTINEL_AMOUNT
    )
    payout = psvc.release_payout_for_booking(booking)
    assert payout.status == PayoutStatus.FAILED
    assert payout.provider_reference is None

    body = client.get(
        f"/api/bookings/{booking.id}/payout", **auth(admin_user)
    ).json()

    assert "provider_reference" in body.keys()
    assert body["provider_reference"] is None


@pytest.mark.django_db
def test_provider_reference_visible_to_admin(client, completed, admin_user):
    booking, _ = completed
    payout = psvc.release_payout_for_booking(booking)

    r = client.get(f"/api/bookings/{booking.id}/payout", **auth(admin_user))
    body = r.json()

    assert r.status_code == 200, r.content
    assert "provider_reference" in body.keys()
    assert body["provider_reference"] == payout.provider_reference


@pytest.mark.django_db
def test_customer_cannot_view_contractor_payout(client, completed, customer):
    """🔒 ما يستلمه المقاول ليس شأن العميل."""
    booking, _ = completed
    psvc.release_payout_for_booking(booking)

    r = client.get(f"/api/bookings/{booking.id}/payout", **auth(customer))

    assert r.status_code == 404
    assert r.json()["code"] == "payout_not_found"


@pytest.mark.django_db
def test_other_contractor_cannot_view_payout(client, completed, other_contractor):
    booking, _ = completed
    intruder, _ = other_contractor
    psvc.release_payout_for_booking(booking)

    r = client.get(f"/api/bookings/{booking.id}/payout", **auth(intruder))

    assert r.status_code == 404


@pytest.mark.django_db
def test_forbidden_and_missing_are_indistinguishable(
    client, completed, customer, contractor
):
    booking, _ = completed
    contractor_user, _ = contractor
    psvc.release_payout_for_booking(booking)

    forbidden = client.get(f"/api/bookings/{booking.id}/payout", **auth(customer))
    missing = client.get(f"/api/bookings/{uuid.uuid4()}/payout", **auth(contractor_user))

    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json() == missing.json()


@pytest.mark.django_db
def test_unauthenticated_returns_401(client, completed):
    booking, _ = completed

    assert client.get(f"/api/bookings/{booking.id}/payout").status_code == 401


@pytest.mark.django_db
def test_no_manual_payout_trigger_endpoint():
    """⚠️ لا مسار يُطلق الدفع يدويًا — المُحفِّز هو التأكيد وحده."""
    from config.urls import api

    paths = api.get_openapi_schema()["paths"]

    for path, methods in paths.items():
        if "payout" in path:
            assert set(methods) == {"get"}, f"{path} exposes {sorted(methods)}"


def test_api_layer_has_no_orm():
    """§43: يُتحقَّق قبل التقرير لا بعده."""
    import inspect

    from apps.payouts.api import payouts as api_mod

    src = inspect.getsource(api_mod)

    assert ".objects." not in src
    assert "Payout(" not in src
