"""
الرقم المرجعي المقروء — Booking.public_reference

يغطي:
  - الشكل (CLN-XXXXXX) والأبجدية المقيَّدة.
  - التوليد التلقائي والتفرد.
  - الثبات: لا يتغيّر مهما حُفظ الحجز بعد ذلك.
  - الظهور في الإنشاء والقائمة والتفاصيل.
  - أنه ليس معرّفًا: لا مسار يقبله.
"""

import json
import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.bookings.models import (
    PUBLIC_REFERENCE_ALPHABET,
    PUBLIC_REFERENCE_LENGTH,
    PUBLIC_REFERENCE_PREFIX,
    Booking,
    BookingServiceSelection,
    generate_public_reference,
)
from apps.properties.models import Property, PropertyAddress, PropertyType
from apps.services.models import ServiceType

# 🔒 الشكل مكتوب هنا صراحةً لا مبنيًا من الثوابت: لو بُني منها لصار
#    الاختبار يقارن الكود بنفسه ومرّ مهما تغيّر.
REFERENCE_PATTERN = re.compile(r"^CLN-[0123456789ACDEFGHJKLMNPQRTUVWXY]{6}$")


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
        latitude=Decimal("-33.868800"),
        longitude=Decimal("151.209300"),
    )
    return prop


def make_booking(customer, prop, service_type):
    booking = Booking.objects.create(
        customer=customer,
        property=prop,
        scheduled_at=timezone.now() + timedelta(days=1),
        customer_timezone="Australia/Sydney",
    )
    BookingServiceSelection.objects.create(
        booking=booking, service_type=service_type, room_count=3
    )
    return booking


# Fixtures — كتلة أرقام +614000130xx
@pytest.fixture
def client():
    return Client()


@pytest.fixture
def customer(db):
    return make_user("+61400013001")


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


# ============================================================
# 1) الشكل
# ============================================================
def test_generated_reference_matches_the_published_format():
    """📌 CLN-7F3K9Q — بادئة ثابتة وست خانات."""
    for _ in range(50):
        assert REFERENCE_PATTERN.match(generate_public_reference())


def test_alphabet_excludes_confusable_characters():
    """🔒 بلا B و I و O و S و Z — تُخلط صوتيًا بـ8 و1 و0 و5 و2."""
    for char in "BIOSZ":
        assert char not in PUBLIC_REFERENCE_ALPHABET

    assert PUBLIC_REFERENCE_LENGTH == 6
    assert PUBLIC_REFERENCE_PREFIX == "CLN"


def test_references_are_not_sequential():
    """🔒 غير تسلسلي: لا يكشف حجم الأعمال ولا يسمح بتخمين مرجع آخر."""
    references = [generate_public_reference() for _ in range(20)]

    # التسلسل يعني تكرار البادئة نفسها مع تغيّر الخانة الأخيرة وحدها
    assert len({r[:-1] for r in references}) > 1


# ============================================================
# 2) التوليد والتفرد
# ============================================================
@pytest.mark.django_db
def test_reference_is_assigned_automatically(customer, prop, service_type):
    booking = make_booking(customer, prop, service_type)

    assert booking.public_reference
    assert REFERENCE_PATTERN.match(booking.public_reference)


@pytest.mark.django_db
def test_references_are_unique_across_many_bookings(customer, prop, service_type):
    bookings = [make_booking(customer, prop, service_type) for _ in range(30)]
    references = {b.public_reference for b in bookings}

    assert len(references) == 30


@pytest.mark.django_db
def test_reference_survives_further_saves(customer, prop, service_type):
    """⚠️ الثبات: مرجع رآه العميل لا يتغيّر."""
    booking = make_booking(customer, prop, service_type)
    original = booking.public_reference

    booking.customer_timezone = "Australia/Perth"
    booking.save()
    booking.refresh_from_db()

    assert booking.public_reference == original


@pytest.mark.django_db
def test_explicit_reference_is_not_overwritten(customer, prop, service_type):
    """📌 التوليد لأول حفظ فقط — الصف الذي يحمل مرجعًا يُحفظ كما هو."""
    booking = Booking(
        customer=customer,
        property=prop,
        public_reference="CLN-AAAAAA",
        scheduled_at=timezone.now() + timedelta(days=1),
    )
    booking.save()
    booking.refresh_from_db()

    assert booking.public_reference == "CLN-AAAAAA"


@pytest.mark.django_db
def test_no_existing_booking_is_missing_a_reference(customer, prop, service_type):
    """📌 ما تضمنه هجرة البيانات 0007 للصفوف السابقة."""
    make_booking(customer, prop, service_type)

    assert not Booking.objects.filter(public_reference=None).exists()
    assert not Booking.objects.filter(public_reference="").exists()


# ============================================================
# 3) الظهور في الـAPI
# ============================================================
@pytest.mark.django_db
def test_reference_is_returned_on_create(client, customer, prop, service_type):
    r = post(
        client,
        "/api/bookings",
        {
            "property_id": str(prop.id),
            "service_selections": [
                {"service_type_id": str(service_type.id), "room_count": 2}
            ],
            "scheduled_at": (timezone.now() + timedelta(days=2))
            .replace(hour=9, minute=0, second=0, microsecond=0)
            .isoformat(),
        },
        **auth(customer),
    )

    assert r.status_code == 201, r.content
    assert REFERENCE_PATTERN.match(r.json()["public_reference"])


@pytest.mark.django_db
def test_reference_is_returned_in_list_and_detail(
    client, customer, prop, service_type
):
    booking = make_booking(customer, prop, service_type)

    listed = client.get("/api/bookings", **auth(customer))
    detail = client.get(f"/api/bookings/{booking.id}", **auth(customer))

    assert listed.status_code == 200, listed.content
    assert detail.status_code == 200, detail.content
    assert listed.json()[0]["public_reference"] == booking.public_reference
    assert detail.json()["public_reference"] == booking.public_reference


@pytest.mark.django_db
def test_reference_is_not_an_identifier(client, customer, prop, service_type):
    """
    ⚠️ المعرّف هو الـUUID وحده — المرجع لا يفتح أي مورد.

    📌 الرد 422 لا 404: المرجع ليس UUID أصلًا فيُرفض في طبقة التحقق قبل
       أي بحث (راجع tests/test_api_malformed_ids.py).
    """
    booking = make_booking(customer, prop, service_type)

    read = client.get(f"/api/bookings/{booking.public_reference}", **auth(customer))
    rescheduled = post(
        client,
        f"/api/bookings/{booking.public_reference}/reschedule",
        {"scheduled_at": (timezone.now() + timedelta(days=2)).isoformat()},
        **auth(customer),
    )

    assert read.status_code == 422, read.content
    assert rescheduled.status_code == 422, rescheduled.content


@pytest.mark.django_db
def test_reference_is_not_accepted_on_create(client, customer, prop, service_type):
    """🔒 لا يُقبل من العميل — يُتجاهل ويُولَّد واحد حقيقي."""
    r = post(
        client,
        "/api/bookings",
        {
            "property_id": str(prop.id),
            "service_selections": [
                {"service_type_id": str(service_type.id), "room_count": 1}
            ],
            "scheduled_at": (timezone.now() + timedelta(days=2))
            .replace(hour=9, minute=0, second=0, microsecond=0)
            .isoformat(),
            "public_reference": "CLN-HACKED",
        },
        **auth(customer),
    )

    assert r.status_code == 201, r.content
    assert r.json()["public_reference"] != "CLN-HACKED"
