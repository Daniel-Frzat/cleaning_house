"""
إعادة جدولة حجز لم يجد مقاولًا — POST /api/bookings/{id}/reschedule

يغطي:
  - المسار الناجح: الموعد يتغيّر، والعروض القديمة تُحذف، والحالة تعود
    SEARCHING، وتبدأ دورة إسناد جديدة.
  - 🔒 الحالة المحورية: مقاول رفض الموعد القديم يُعرض عليه الجديد —
    وهي بالضبط ما يفشل لو تُركت العروض القديمة (find_candidates يستبعد
    من عُرض عليه بأي حالة + قيد التفرد).
  - 409 لكل حالة خارج النطاق المسموح.
  - 400 لموعد ماضٍ أو خارج ساعات العمل (نفس قواعد الإنشاء).
  - 404 لحجز الغير.
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
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
    DispatchStatus,
)
from apps.contractors.models import (
    AvailabilityStatus,
    BusinessRegistration,
    ContractorProfile,
    InsuranceDocument,
    VerificationStatus,
)
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.properties.models import Property, PropertyAddress, PropertyType
from apps.services.models import PricingConfig, ServiceType

from tests.conftest import set_contractor_location

# إحداثيات سيدني — نفس ثوابت test_bookings_dispatch.py
SYDNEY = (Decimal("-33.868800"), Decimal("151.209300"))
NEAR = (Decimal("-33.878800"), Decimal("151.209300"))


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


def make_property(owner, coords=SYDNEY):
    prop = Property.objects.create(
        owner=owner, label="Home", property_type=PropertyType.HOUSE
    )
    PropertyAddress.objects.create(
        property=prop,
        street_address="12 Example St",
        suburb="Bondi",
        state="NSW",
        postcode="2026",
        latitude=coords[0],
        longitude=coords[1],
    )
    return prop


def make_contractor(phone, coords=NEAR, available=True, eligible=True):
    """مقاول مؤهَّل للإسناد (ABN + تأمين معتمدان)."""
    user = make_user(phone, role=ConfirmedRole.CONTRACTOR)
    profile = ContractorProfile.objects.create(
        user=user,
        business_name=f"Co {phone[-3:]}",
        latitude=coords[0] if coords else None,
        longitude=coords[1] if coords else None,
        availability_status=(
            AvailabilityStatus.AVAILABLE if available else AvailabilityStatus.UNAVAILABLE
        ),
    )
    # §8: الإسناد يقرأ موقع الهاتف الحالي لا عنوان العمل.
    set_contractor_location(
        profile, (coords[0] if coords else None, coords[1] if coords else None)
    )
    if eligible:
        admin = User.objects.filter(role=ConfirmedRole.ADMIN).first() or make_user(
            "+61400012099", role=ConfirmedRole.ADMIN
        )
        BusinessRegistration.objects.create(
            contractor=profile,
            abn="12345678901",
            business_name="Co",
            status=VerificationStatus.VERIFIED,
            reviewed_by=admin,
            reviewed_at=timezone.now(),
        )
        InsuranceDocument.objects.create(
            contractor=profile,
            document_reference="POL-1",
            expiry_date=timezone.localdate() + timedelta(days=365),
            status=VerificationStatus.VERIFIED,
            reviewed_by=admin,
            reviewed_at=timezone.now(),
        )
    return user, profile


def make_booking(
    customer,
    prop,
    service_type,
    status=BookingStatus.PENDING,
    dispatch_status=DispatchStatus.NO_CONTRACTOR,
    scheduled_at=None,
    **extra,
):
    """حجز في الحالة المطلوبة مباشرةً — تجاوز تدفّق الإسناد."""
    booking = Booking.objects.create(
        customer=customer,
        property=prop,
        status=status,
        dispatch_status=dispatch_status,
        scheduled_at=scheduled_at or (timezone.now() + timedelta(days=1)),
        customer_timezone="Australia/Sydney",
        **extra,
    )
    BookingServiceSelection.objects.create(
        booking=booking, service_type=service_type, room_count=3
    )
    return booking


def next_business_time(days=2, hour=9):
    """لحظة مستقبلية داخل ساعات العمل بتوقيت سيدني."""
    from zoneinfo import ZoneInfo

    local = (timezone.now() + timedelta(days=days)).astimezone(
        ZoneInfo("Australia/Sydney")
    )
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


def payload(when=None, tz=None):
    body = {"scheduled_at": (when or next_business_time()).isoformat()}
    if tz is not None:
        body["timezone"] = tz
    return body


# ------------------------------------------------------------
# Fixtures — كتلة أرقام +614000120xx
# ------------------------------------------------------------
@pytest.fixture
def client():
    return Client()


@pytest.fixture
def customer(db):
    return make_user("+61400012001")


@pytest.fixture
def other_customer(db):
    return make_user("+61400012002")


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
def pricing(db):
    config, _ = PricingConfig.objects.get_or_create(pk=PricingConfig.SINGLETON_PK)
    config.price_per_km = Decimal("2.00")
    config.save()
    return config


# ============================================================
# 1) المسار الناجح
# ============================================================
@pytest.mark.django_db
def test_reschedule_updates_time_and_resumes_search(
    client, customer, prop, service_type, django_capture_on_commit_callbacks
):
    """📌 الموعد يتغيّر والحالة تعود SEARCHING."""
    booking = make_booking(customer, prop, service_type)
    new_time = next_business_time(days=3, hour=10)

    with django_capture_on_commit_callbacks(execute=True):
        r = post(
            client,
            f"/api/bookings/{booking.id}/reschedule",
            payload(new_time),
            **auth(customer),
        )

    assert r.status_code == 200, r.content
    body = r.json()

    booking.refresh_from_db()
    assert booking.scheduled_at == new_time
    assert body["status"] == BookingStatus.PENDING
    # لا مقاول في قاعدة البيانات → البحث انتهى فورًا بلا مرشَّح
    assert booking.dispatch_status == DispatchStatus.NO_CONTRACTOR


@pytest.mark.django_db
def test_reschedule_clears_previous_offers(
    client, customer, prop, service_type, django_capture_on_commit_callbacks
):
    """⚠️ العروض القديمة تُحذف — لا تُعلَّم فقط."""
    booking = make_booking(customer, prop, service_type)
    _, profile = make_contractor("+61400012010")
    old_offer = DispatchOffer.objects.create(
        booking=booking,
        contractor=profile,
        status=DispatchOfferStatus.DECLINED,
        distance_km=Decimal("1.112"),
        expires_at=timezone.now() - timedelta(minutes=5),
    )

    with django_capture_on_commit_callbacks(execute=True):
        r = post(
            client,
            f"/api/bookings/{booking.id}/reschedule",
            payload(),
            **auth(customer),
        )

    assert r.status_code == 200, r.content
    assert not DispatchOffer.objects.filter(pk=old_offer.pk).exists()


@pytest.mark.django_db
def test_contractor_who_declined_is_offered_the_new_time(
    client, customer, prop, service_type, pricing, django_capture_on_commit_callbacks
):
    """
    🔒 الحالة المحورية: من رفض الموعد القديم يُعرض عليه الجديد.

    هذا ما يفشل لو تُركت العروض القديمة: find_candidates يستبعد كل من
    عُرض عليه الحجز بأي حالة، فتعود القائمة فارغة وتبقى NO_CONTRACTOR.
    """
    booking = make_booking(customer, prop, service_type)
    _, profile = make_contractor("+61400012011")

    # المقاول الوحيد رفض الموعد القديم
    DispatchOffer.objects.create(
        booking=booking,
        contractor=profile,
        status=DispatchOfferStatus.DECLINED,
        distance_km=Decimal("1.112"),
        responded_at=timezone.now(),
        expires_at=timezone.now() - timedelta(minutes=5),
    )

    with django_capture_on_commit_callbacks(execute=True):
        r = post(
            client,
            f"/api/bookings/{booking.id}/reschedule",
            payload(),
            **auth(customer),
        )

    assert r.status_code == 200, r.content

    booking.refresh_from_db()
    offers = DispatchOffer.objects.filter(booking=booking)

    assert offers.count() == 1
    fresh = offers.first()
    assert fresh.contractor_id == profile.id
    assert fresh.status == DispatchOfferStatus.PENDING
    assert booking.dispatch_status == DispatchStatus.SEARCHING


@pytest.mark.django_db
def test_timezone_defaults_to_the_bookings_own(
    client, customer, prop, service_type, django_capture_on_commit_callbacks
):
    """📌 حذف timezone يستعمل منطقة الحجز المخزَّنة."""
    booking = make_booking(customer, prop, service_type)

    with django_capture_on_commit_callbacks(execute=True):
        r = post(
            client,
            f"/api/bookings/{booking.id}/reschedule",
            payload(),
            **auth(customer),
        )

    assert r.status_code == 200, r.content
    assert r.json()["customer_timezone"] == "Australia/Sydney"


# ============================================================
# 2) 409 — خارج النطاق المسموح
# ============================================================
@pytest.mark.django_db
def test_confirmed_booking_cannot_be_rescheduled(
    client, customer, prop, service_type
):
    """⚠️ بعد التأكيد مؤجَّل حتى تُحسم سياسة الإلغاء."""
    booking = make_booking(
        customer,
        prop,
        service_type,
        status=BookingStatus.CONFIRMED,
        dispatch_status=DispatchStatus.ASSIGNED,
        computed_price=Decimal("215.00"),
    )

    r = post(
        client, f"/api/bookings/{booking.id}/reschedule", payload(), **auth(customer)
    )

    assert r.status_code == 409, r.content
    assert r.json()["code"] == "booking_not_reschedulable"


@pytest.mark.django_db
def test_booking_still_searching_cannot_be_rescheduled(
    client, customer, prop, service_type
):
    """⚠️ عرض قائم لدى مقاول — لا يُسحب من تحته."""
    booking = make_booking(
        customer, prop, service_type, dispatch_status=DispatchStatus.SEARCHING
    )

    r = post(
        client, f"/api/bookings/{booking.id}/reschedule", payload(), **auth(customer)
    )

    assert r.status_code == 409, r.content
    assert r.json()["code"] == "booking_not_reschedulable"


@pytest.mark.django_db
def test_assigned_booking_cannot_be_rescheduled(client, customer, prop, service_type):
    booking = make_booking(customer, prop, service_type)
    _, profile = make_contractor("+61400012012")
    booking.assigned_contractor = profile
    booking.save(update_fields=["assigned_contractor"])

    r = post(
        client, f"/api/bookings/{booking.id}/reschedule", payload(), **auth(customer)
    )

    assert r.status_code == 409, r.content
    assert r.json()["code"] == "booking_not_reschedulable"


@pytest.mark.django_db
def test_paid_booking_cannot_be_rescheduled(client, customer, prop, service_type):
    """
    ⚠️ الصمّام الرابع: دفعة ناجحة تمنع إعادة الجدولة.

    حالة مُركَّبة يدويًا: التدفّق الطبيعي لا ينتجها (الدفع يقع بعد القبول
    الذي يضبط ASSIGNED)، والصمّام موجود لصفٍّ عُدّل يدويًا.
    """
    booking = make_booking(
        customer, prop, service_type, computed_price=Decimal("215.00")
    )
    Payment.objects.create(
        booking=booking,
        amount=Decimal("215.00"),
        method=PaymentMethod.CARD,
        status=PaymentStatus.SUCCEEDED,
    )

    r = post(
        client, f"/api/bookings/{booking.id}/reschedule", payload(), **auth(customer)
    )

    assert r.status_code == 409, r.content
    assert r.json()["code"] == "booking_not_reschedulable"


@pytest.mark.django_db
def test_failed_payment_does_not_block_rescheduling(
    client, customer, prop, service_type, django_capture_on_commit_callbacks
):
    """📌 الصمّام على الدفعة الناجحة وحدها — الفاشلة لا تمنع."""
    booking = make_booking(
        customer, prop, service_type, computed_price=Decimal("215.00")
    )
    Payment.objects.create(
        booking=booking,
        amount=Decimal("215.00"),
        method=PaymentMethod.CARD,
        status=PaymentStatus.FAILED,
        failure_reason="Card declined.",
    )

    with django_capture_on_commit_callbacks(execute=True):
        r = post(
            client,
            f"/api/bookings/{booking.id}/reschedule",
            payload(),
            **auth(customer),
        )

    assert r.status_code == 200, r.content


# ============================================================
# 3) 400 — قواعد الموعد (نفس قواعد الإنشاء)
# ============================================================
@pytest.mark.django_db
def test_past_time_is_rejected(client, customer, prop, service_type):
    booking = make_booking(customer, prop, service_type)
    past = timezone.now() - timedelta(days=1)

    r = post(
        client,
        f"/api/bookings/{booking.id}/reschedule",
        payload(past),
        **auth(customer),
    )

    assert r.status_code == 400, r.content
    assert r.json()["code"] == "scheduled_at_in_past"


@pytest.mark.django_db
def test_time_outside_business_hours_is_rejected(client, customer, prop, service_type):
    booking = make_booking(customer, prop, service_type)
    late = next_business_time(days=2, hour=22)

    r = post(
        client,
        f"/api/bookings/{booking.id}/reschedule",
        payload(late),
        **auth(customer),
    )

    assert r.status_code == 400, r.content
    assert r.json()["code"] == "outside_business_hours"


@pytest.mark.django_db
def test_rejected_reschedule_leaves_the_booking_untouched(
    client, customer, prop, service_type
):
    """⚠️ الرفض لا يترك أثرًا جزئيًا — الموعد والعروض كما كانا."""
    booking = make_booking(customer, prop, service_type)
    original_time = booking.scheduled_at
    _, profile = make_contractor("+61400012013")
    DispatchOffer.objects.create(
        booking=booking,
        contractor=profile,
        status=DispatchOfferStatus.DECLINED,
        distance_km=Decimal("1.112"),
        expires_at=timezone.now() - timedelta(minutes=5),
    )

    r = post(
        client,
        f"/api/bookings/{booking.id}/reschedule",
        payload(timezone.now() - timedelta(days=1)),
        **auth(customer),
    )

    assert r.status_code == 400, r.content

    booking.refresh_from_db()
    assert booking.scheduled_at == original_time
    assert DispatchOffer.objects.filter(booking=booking).count() == 1


# ============================================================
# 4) الصلاحية
# ============================================================
@pytest.mark.django_db
def test_another_customers_booking_returns_404(
    client, customer, other_customer, prop, service_type
):
    """🔒 حجز الغير كغير الموجود تمامًا."""
    booking = make_booking(customer, prop, service_type)

    r = post(
        client,
        f"/api/bookings/{booking.id}/reschedule",
        payload(),
        **auth(other_customer),
    )

    assert r.status_code == 404, r.content
    assert r.json()["code"] == "booking_not_found"


@pytest.mark.django_db
def test_unknown_booking_returns_404(client, customer):
    import uuid

    r = post(
        client,
        f"/api/bookings/{uuid.uuid4()}/reschedule",
        payload(),
        **auth(customer),
    )

    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_contractor_cannot_reschedule(client, customer, prop, service_type):
    """🔒 بوابة الدور: CUSTOMER حصرًا."""
    booking = make_booking(customer, prop, service_type)
    contractor_user, _ = make_contractor("+61400012014")

    r = post(
        client,
        f"/api/bookings/{booking.id}/reschedule",
        payload(),
        **auth(contractor_user),
    )

    assert r.status_code == 403, r.content
    assert r.json()["code"] == "invalid_customer_role"


def test_reschedule_endpoint_is_post_only():
    """⚠️ إعادة الجدولة فعل لا قراءة."""
    from config.urls import api

    paths = api.get_openapi_schema()["paths"]
    methods = set(paths["/api/bookings/{booking_id}/reschedule"])

    assert methods == {"post"}
