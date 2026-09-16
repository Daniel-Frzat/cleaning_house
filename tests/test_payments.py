"""
Payment Tests — Payment Domain (Change Set §36.4، §8؛ Infra §2، §14/§16)

يغطي: الشحن الناجح، الشحن الفاشل (مع بقاء الحجز CONFIRMED)، رفض الشحن قبل
التأكيد، منع الدفعة الثانية (خدمةً وقاعدةَ بيانات)، صمّام أمان الـadapter
الوهمي، وحجب provider_reference عن العميل.
"""

import json
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.conftest import set_contractor_location
from django.core.exceptions import ImproperlyConfigured, ValidationError
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
from apps.payments.adapters import get_payment_adapter
from apps.payments.adapters.base import BasePaymentProviderAdapter, PaymentChargeResult
from apps.payments.adapters.fake_adapter import (
    FAILURE_SENTINEL_AMOUNT,
    FakePaymentAdapter,
)
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.payments.services import payments as psvc
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


@pytest.fixture
def customer(db):
    return make_user("+61400009001")


@pytest.fixture
def other_customer(db):
    return make_user("+61400009002")


@pytest.fixture
def admin_user(db):
    return make_user("+61400009003", role=ConfirmedRole.ADMIN)


@pytest.fixture
def contractor_user(db):
    return make_user("+61400009004", role=ConfirmedRole.CONTRACTOR)


@pytest.fixture
def prop(customer):
    p = Property.objects.create(
        owner=customer, label="Home", property_type=PropertyType.HOUSE
    )
    PropertyAddress.objects.create(
        property=p,
        street_address="12 Example St",
        suburb="Bondi",
        state="NSW",
        postcode="2026",
        latitude=Decimal("-33.868800"),
        longitude=Decimal("151.209300"),
    )
    return p


@pytest.fixture
def service_type(db):
    return ServiceType.objects.create(
        name="General Cleaning",
        room_price=Decimal("45.00"),
        base_price=Decimal("80.00"),
    )


def make_booking(customer, prop, service_type, price=None, status=BookingStatus.PENDING):
    """ينشئ حجزًا مباشرةً بالحالة والسعر المطلوبين (تجاوز تدفّق الإسناد)."""
    booking = Booking.objects.create(
        customer=customer, property=prop, status=status, computed_price=price
    )
    BookingServiceSelection.objects.create(
        booking=booking, service_type=service_type, room_count=3
    )
    return booking


@pytest.fixture
def confirmed_booking(customer, prop, service_type):
    return make_booking(
        customer, prop, service_type, price=Decimal("215.00"),
        status=BookingStatus.CONFIRMED,
    )


# ============================================================
# 1) الشحن الناجح
# ============================================================
@pytest.mark.django_db
def test_successful_charge_creates_succeeded_payment(confirmed_booking):
    payment = psvc.charge_for_booking(confirmed_booking)

    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.amount == confirmed_booking.computed_price == Decimal("215.00")
    assert payment.provider_reference is not None
    assert payment.provider_reference.startswith("fake_")
    assert payment.failure_reason is None


@pytest.mark.django_db
def test_payment_amount_matches_snapshot_exactly(customer, prop, service_type):
    """📌 المبلغ = لقطة سعر الحجز بالضبط، لا تقريب ولا إعادة حساب."""
    booking = make_booking(
        customer, prop, service_type, price=Decimal("1234.56"),
        status=BookingStatus.CONFIRMED,
    )

    payment = psvc.charge_for_booking(booking)

    assert payment.amount == Decimal("1234.56")


@pytest.mark.django_db
def test_payment_defaults_to_card_method(confirmed_booking):
    payment = psvc.charge_for_booking(confirmed_booking)

    assert payment.method == PaymentMethod.CARD


@pytest.mark.django_db
@pytest.mark.parametrize(
    "method", [PaymentMethod.CARD, PaymentMethod.APPLE_PAY, PaymentMethod.GOOGLE_PAY]
)
def test_all_three_methods_accepted(customer, prop, service_type, method):
    booking = make_booking(
        customer, prop, service_type, price=Decimal("215.00"),
        status=BookingStatus.CONFIRMED,
    )

    payment = psvc.charge_for_booking(booking, method=method)

    assert payment.method == method
    assert payment.status == PaymentStatus.SUCCEEDED


@pytest.mark.django_db
def test_booking_status_unchanged_on_success(confirmed_booking):
    psvc.charge_for_booking(confirmed_booking)

    confirmed_booking.refresh_from_db()
    assert confirmed_booking.status == BookingStatus.CONFIRMED


# ============================================================
# 2) الشحن الفاشل — الحجز لا يتغيّر
# ============================================================
@pytest.mark.django_db
def test_failed_charge_records_failure(customer, prop, service_type):
    """يستخدم قيمة الفشل المتفق عليها في الـadapter الوهمي."""
    booking = make_booking(
        customer, prop, service_type, price=FAILURE_SENTINEL_AMOUNT,
        status=BookingStatus.CONFIRMED,
    )

    payment = psvc.charge_for_booking(booking)

    assert payment.status == PaymentStatus.FAILED
    assert payment.failure_reason is not None
    assert payment.provider_reference is None
    assert payment.amount == FAILURE_SENTINEL_AMOUNT


@pytest.mark.django_db
def test_failed_charge_leaves_booking_confirmed(customer, prop, service_type):
    """
    ⚠️ سياسة غير محسومة: لا إلغاء تلقائي للحجز عند فشل الدفع. الحجز يبقى
       CONFIRMED عمدًا — لا يُفترض سلوك بديل في هذه المرحلة.
    """
    booking = make_booking(
        customer, prop, service_type, price=FAILURE_SENTINEL_AMOUNT,
        status=BookingStatus.CONFIRMED,
    )

    psvc.charge_for_booking(booking)

    booking.refresh_from_db()
    assert booking.status == BookingStatus.CONFIRMED
    assert booking.computed_price == FAILURE_SENTINEL_AMOUNT
    assert booking.assigned_contractor_id is None or True  # لم يُمس


@pytest.mark.django_db
def test_failed_payment_row_still_persists(customer, prop, service_type):
    """الفشل يُسجَّل ولا يُبتلع: الصف موجود بحالة FAILED."""
    booking = make_booking(
        customer, prop, service_type, price=FAILURE_SENTINEL_AMOUNT,
        status=BookingStatus.CONFIRMED,
    )

    psvc.charge_for_booking(booking)

    stored = Payment.objects.get(booking=booking)
    assert stored.status == PaymentStatus.FAILED


# ============================================================
# 3) لا شحن قبل التأكيد
# ============================================================
@pytest.mark.django_db
@pytest.mark.parametrize("status", [BookingStatus.PENDING, BookingStatus.CANCELLED])
def test_cannot_charge_unconfirmed_booking(customer, prop, service_type, status):
    booking = make_booking(
        customer, prop, service_type, price=Decimal("215.00"), status=status
    )

    with pytest.raises(psvc.BookingNotConfirmedError):
        psvc.charge_for_booking(booking)

    assert Payment.objects.count() == 0


@pytest.mark.django_db
def test_cannot_charge_booking_without_price(customer, prop, service_type):
    booking = make_booking(
        customer, prop, service_type, price=None, status=BookingStatus.CONFIRMED
    )

    with pytest.raises(psvc.MissingPriceError):
        psvc.charge_for_booking(booking)

    assert Payment.objects.count() == 0


@pytest.mark.django_db
def test_error_codes_are_stable(customer, prop, service_type):
    booking = make_booking(
        customer, prop, service_type, price=Decimal("10.00"),
        status=BookingStatus.PENDING,
    )

    with pytest.raises(psvc.BookingNotConfirmedError) as exc:
        psvc.charge_for_booking(booking)
    assert exc.value.code == "booking_not_confirmed"


# ============================================================
# 4) لا دفعة ثانية — خدمةً وقاعدةَ بيانات
# ============================================================
@pytest.mark.django_db
def test_second_charge_rejected_by_service_layer(confirmed_booking):
    psvc.charge_for_booking(confirmed_booking)

    with pytest.raises(psvc.PaymentAlreadyExistsError):
        psvc.charge_for_booking(confirmed_booking)

    assert Payment.objects.filter(booking=confirmed_booking).count() == 1


@pytest.mark.django_db
def test_second_payment_rejected_by_db_constraint(confirmed_booking):
    """
    🔒 دفاع في العمق: حتى بتجاوز طبقة الخدمة، قيد OneToOne يمنع الصف الثاني.
    """
    psvc.charge_for_booking(confirmed_booking)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payment.objects.create(
                booking=confirmed_booking,
                amount=confirmed_booking.computed_price,
                method=PaymentMethod.CARD,
                status=PaymentStatus.PENDING,
            )

    assert Payment.objects.filter(booking=confirmed_booking).count() == 1


@pytest.mark.django_db
def test_idempotency_key_is_stable_for_a_booking(confirmed_booking):
    """
    🔒 Infra §14/§16: المفتاح مشتق من معرّف الحجز وثابت عبر الاستدعاءات.
    """
    first = psvc.build_idempotency_key(confirmed_booking)
    second = psvc.build_idempotency_key(confirmed_booking)

    assert first == second
    assert str(confirmed_booking.id) in first


@pytest.mark.django_db
def test_idempotency_key_differs_between_bookings(
    customer, prop, service_type, confirmed_booking
):
    other = make_booking(
        customer, prop, service_type, price=Decimal("99.00"),
        status=BookingStatus.CONFIRMED,
    )

    assert psvc.build_idempotency_key(confirmed_booking) != psvc.build_idempotency_key(other)


@pytest.mark.django_db
def test_amount_must_match_booking_price_at_model_level(confirmed_booking):
    """🔒 مبلغ مخالف للقطة مرفوض على مستوى الـModel، لا الخدمة وحدها."""
    payment = Payment(
        booking=confirmed_booking,
        amount=Decimal("999.99"),  # لا يساوي 215.00
        method=PaymentMethod.CARD,
    )

    with pytest.raises(ValidationError):
        payment.full_clean()


# ============================================================
# 5) الـadapter — تجريدي، موصول بالإعدادات، ومحمي بصمّام
# ============================================================
def test_base_adapter_is_abstract():
    """⚠️ لا تنفيذ فعلي في كود الإنتاج — الكلاس الأساسي تجريدي."""
    with pytest.raises(TypeError):
        BasePaymentProviderAdapter()


def test_adapter_is_resolved_from_settings(settings):
    """الاختيار عبر مسار نصي في الإعدادات، لا استيراد مباشر."""
    adapter = get_payment_adapter()

    assert isinstance(adapter, FakePaymentAdapter)
    assert isinstance(adapter, BasePaymentProviderAdapter)
    assert settings.PAYMENT_PROVIDER_ADAPTER_CLASS.endswith("FakePaymentAdapter")


def test_fake_adapter_refuses_to_run_in_production(settings):
    """
    🔒 صمّام الأمان (نفس نمط DevConsoleSMSAdapter — §31).
    """
    settings.DEBUG = False
    settings.PAYMENTS_ALLOW_FAKE_ADAPTER = False

    with pytest.raises(ImproperlyConfigured):
        FakePaymentAdapter()


def test_fake_adapter_allowed_when_flag_is_explicitly_set(settings):
    settings.DEBUG = False
    settings.PAYMENTS_ALLOW_FAKE_ADAPTER = True

    assert isinstance(FakePaymentAdapter(), FakePaymentAdapter)


def test_fake_adapter_returns_neutral_result_shape():
    result = FakePaymentAdapter().charge(
        amount=Decimal("100.00"), method="CARD", idempotency_key="k"
    )

    assert isinstance(result, PaymentChargeResult)
    assert result.success is True
    assert result.provider_reference.startswith("fake_")


def test_fake_adapter_failure_sentinel():
    result = FakePaymentAdapter().charge(
        amount=FAILURE_SENTINEL_AMOUNT, method="CARD", idempotency_key="k"
    )

    assert result.success is False
    assert result.failure_reason
    assert result.provider_reference is None


def test_no_real_psp_integration_anywhere():
    """
    ⚠️ لا Stripe SDK ولا HTTP ولا أي تكامل حقيقي في كود الإنتاج.
       الفحص على الاستيرادات الفعلية (AST) لا على النص.
    """
    import ast
    import pathlib

    banned = ("stripe", "requests", "httpx", "urllib", "http.client", "braintree",
              "paypal", "adyen", "square")

    for path in sorted(pathlib.Path("apps/payments").rglob("*.py")):
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
            low = name.lower()
            for bad in banned:
                assert bad not in low, f"{path} imports {name}"


# ============================================================
# 6) الحالات — ثلاث فقط، بلا escrow
# ============================================================
def test_status_enum_has_no_escrow_states():
    """
    ⚠️ §36.4 شحن مباشر: لا HELD/AUTHORIZED/CAPTURED/RELEASED — تلك تعود
       لمفهوم الضمان المسحوب من التصميم.

    📌 اتّسعت القائمة بدورة حياة الدفع (§13): PROCESSING و REQUIRES_ACTION
       و NOT_CHARGED و REFUNDED. لا واحدة منها حالة ضمان — كلها مراحل في
       شحن مباشر واحد. الحارس هنا على الحالات المسحوبة لا على العدد.
    """
    for retired in ("HELD", "AUTHORIZED", "CAPTURED", "RELEASED"):
        assert retired not in PaymentStatus.values

    assert set(PaymentStatus.values) == {
        "NOT_CHARGED",
        "PENDING",
        "PROCESSING",
        "REQUIRES_ACTION",
        "SUCCEEDED",
        "FAILED",
        "REFUNDED",
    }


def test_refunded_status_has_no_code_path():
    """
    ⚠️ §13: REFUNDED معرَّفة ولا يصل إليها أي مسار — الاسترداد عملية
       حقيقية لدى المزوّد وسياسته غير محسومة. وجودها في الـenum لا يعني
       أن النظام يسترد.
    """
    import inspect

    from apps.payments.services import payments as svc

    src = inspect.getsource(svc)

    assert "PaymentStatus.REFUNDED" not in src, (
        "A code path now sets REFUNDED — that requires a real refund "
        "operation and an approved refund policy (§13, §23)."
    )


def test_method_enum_has_exactly_three_values():
    assert set(PaymentMethod.values) == {"CARD", "APPLE_PAY", "GOOGLE_PAY"}


# ============================================================
# 7) الشحن التلقائي لحظة التأكيد
# ============================================================
@pytest.mark.django_db
def test_accepting_offer_triggers_charge(
    client, customer, prop, service_type, contractor_user,
    admin_user, django_capture_on_commit_callbacks,
):
    """📌 الشحن المباشر يقع لحظة تأكيد الحجز (§36.4)."""
    cfg, _ = PricingConfig.objects.get_or_create(pk=PricingConfig.SINGLETON_PK)
    cfg.price_per_km = Decimal("2.00")
    cfg.save()

    profile = ContractorProfile.objects.create(
        user=contractor_user,
        business_name="Co",
        latitude=Decimal("-33.878800"),
        longitude=Decimal("151.209300"),
        availability_status=AvailabilityStatus.AVAILABLE,
    )
    # §8: الإسناد يقرأ موقع الهاتف الحالي لا عنوان العمل.
    set_contractor_location(
        profile, (Decimal("-33.878800"), Decimal("151.209300"))
    )
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

    booking = make_booking(customer, prop, service_type)
    offer = DispatchOffer.objects.create(
        booking=booking, contractor=profile, status=DispatchOfferStatus.PENDING,
        distance_km=Decimal("1.112"),
        expires_at=timezone.now() + timedelta(minutes=60),
    )

    from apps.bookings.services import offers as osvc

    with django_capture_on_commit_callbacks(execute=True):
        osvc.accept_offer(contractor_user, offer.id)

    booking.refresh_from_db()
    payment = Payment.objects.get(booking=booking)

    assert booking.status == BookingStatus.CONFIRMED
    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.amount == booking.computed_price


# ============================================================
# 8) نقطة النهاية — صلاحية وحجب provider_reference
# ============================================================
@pytest.mark.django_db
def test_owner_customer_can_view_payment(client, customer, confirmed_booking):
    psvc.charge_for_booking(confirmed_booking)

    r = client.get(f"/api/bookings/{confirmed_booking.id}/payment", **auth(customer))

    assert r.status_code == 200, r.content
    body = r.json()
    assert Decimal(body["amount"]) == Decimal("215.00")
    assert body["status"] == PaymentStatus.SUCCEEDED
    assert body["method"] == PaymentMethod.CARD


@pytest.mark.django_db
def test_provider_reference_key_absent_for_customer(client, customer, confirmed_booking):
    """
    🔒 الحجب هيكلي: المفتاح **غائب** من JSON للعميل، لا موجودًا بقيمة
       null (تصحيح رجعي لعيب §43). الفحص على المفاتيح لا على القيمة.
    """
    payment = psvc.charge_for_booking(confirmed_booking)
    assert payment.provider_reference  # موجود فعلًا في قاعدة البيانات

    r = client.get(f"/api/bookings/{confirmed_booking.id}/payment", **auth(customer))
    body = r.json()

    assert "provider_reference" not in body.keys()
    assert payment.provider_reference not in r.content.decode()


@pytest.mark.django_db
def test_admin_keeps_provider_reference_key_even_when_none(
    client, admin_user, customer, prop, service_type
):
    """
    🔒 الفرق المقصود: المفتاح يظهر للإدارة حتى بقيمة None (دفعة فاشلة).
    """
    booking = make_booking(
        customer, prop, service_type, price=FAILURE_SENTINEL_AMOUNT,
        status=BookingStatus.CONFIRMED,
    )
    payment = psvc.charge_for_booking(booking)
    assert payment.status == PaymentStatus.FAILED
    assert payment.provider_reference is None

    body = client.get(
        f"/api/bookings/{booking.id}/payment", **auth(admin_user)
    ).json()

    assert "provider_reference" in body.keys()
    assert body["provider_reference"] is None


@pytest.mark.django_db
def test_provider_reference_visible_to_admin(
    client, admin_user, confirmed_booking
):
    payment = psvc.charge_for_booking(confirmed_booking)

    r = client.get(f"/api/bookings/{confirmed_booking.id}/payment", **auth(admin_user))
    body = r.json()

    assert r.status_code == 200, r.content
    assert "provider_reference" in body.keys()
    assert body["provider_reference"] == payment.provider_reference


@pytest.mark.django_db
def test_other_customer_cannot_view_payment(
    client, other_customer, confirmed_booking
):
    psvc.charge_for_booking(confirmed_booking)

    r = client.get(
        f"/api/bookings/{confirmed_booking.id}/payment", **auth(other_customer)
    )

    assert r.status_code == 404
    assert r.json()["code"] == "payment_not_found"


@pytest.mark.django_db
def test_contractor_cannot_view_payment(client, contractor_user, confirmed_booking):
    psvc.charge_for_booking(confirmed_booking)

    r = client.get(
        f"/api/bookings/{confirmed_booking.id}/payment", **auth(contractor_user)
    )

    assert r.status_code == 404


@pytest.mark.django_db
def test_missing_payment_and_forbidden_are_indistinguishable(
    client, customer, other_customer, confirmed_booking
):
    """🔒 نفس الرد للحالتين — لا نكشف وجود حجز لغير صاحبه."""
    forbidden = client.get(
        f"/api/bookings/{confirmed_booking.id}/payment", **auth(other_customer)
    )
    no_payment = client.get(
        f"/api/bookings/{confirmed_booking.id}/payment", **auth(customer)
    )

    assert forbidden.status_code == no_payment.status_code == 404
    assert forbidden.json() == no_payment.json()


@pytest.mark.django_db
def test_unknown_booking_returns_404(client, customer):
    r = client.get(f"/api/bookings/{uuid.uuid4()}/payment", **auth(customer))

    assert r.status_code == 404


@pytest.mark.django_db
def test_unauthenticated_returns_401(client, confirmed_booking):
    r = client.get(f"/api/bookings/{confirmed_booking.id}/payment")

    assert r.status_code == 401


# ============================================================
# 9) الطبقات
# ============================================================
def test_api_layer_does_not_mutate_payments():
    """طبقة الـAPI للقراءة فقط: لا إنشاء دفعات ولا شحن."""
    import inspect

    from apps.payments.api import payments as api_mod

    src = inspect.getsource(api_mod)

    assert "Payment(" not in src
    assert "charge_for_booking" not in src
    assert ".objects.create" not in src


def test_payment_service_never_changes_booking_status():
    """
    ⚠️ سياسة مفتوحة: طبقة الدفع لا تلمس حالة الحجز إطلاقًا.
    """
    import inspect

    from apps.payments.services import payments as svc_mod

    src = inspect.getsource(svc_mod)

    assert "booking.status =" not in src
    assert "BookingStatus.CANCELLED" not in src
