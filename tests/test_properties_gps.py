"""
GPS coordinate tests — Properties + Contractors

📌 الإحداثيات تصل من GPS الجهاز، لا من geocoding على الخادم (§34/§4).

⚠️ السبب الحقيقي لأهميتها: محرّك الإسناد يقيس المسافة بين العقار والمقاول.
   من لا موقع له **يسقط من الترشيح صامتًا** بلا رسالة خطأ — لا للعميل ولا
   للمقاول. لذلك تُختبر هنا سلسلة كاملة: الإدخال ← التخزين ← الإسناد.
"""

import datetime
import json
from decimal import Decimal

import pytest
from django.test import Client
from zoneinfo import ZoneInfo

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.bookings.models import DispatchOffer
from apps.contractors.models import (
    AvailabilityStatus,
    BusinessRegistration,
    ContractorProfile,
    InsuranceDocument,
    VerificationStatus,
)
from apps.properties.models import PropertyAddress
from apps.services.models import ServiceType

from tests.conftest import set_contractor_location

SYDNEY_LAT = "-33.870000"
SYDNEY_LNG = "151.210000"


@pytest.fixture
def client():
    return Client()


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


def post(client, url, payload, **extra):
    return client.post(
        url, data=json.dumps(payload), content_type="application/json", **extra
    )


def patch(client, url, payload, **extra):
    return client.patch(
        url, data=json.dumps(payload), content_type="application/json", **extra
    )


def address(**overrides):
    body = {
        "street_address": "12 George St",
        "suburb": "Sydney",
        "state": "NSW",
        "postcode": "2000",
    }
    body.update(overrides)
    return body


@pytest.fixture
def customer(db):
    return User.objects.create_user(phone="+61400660101", role=ConfirmedRole.CUSTOMER)


@pytest.fixture
def worker(db):
    return User.objects.create_user(phone="+61400660102", role=ConfirmedRole.CUSTOMER)


@pytest.fixture
def service(db):
    return ServiceType.objects.create(
        name="General Cleaning",
        room_price=Decimal("45.00"),
        base_price=Decimal("80.00"),
    )


# ============================================================
# 1) العقار — إدخال الإحداثيات
# ============================================================
@pytest.mark.django_db
def test_property_accepts_gps_on_creation(client, customer):
    r = post(client, "/api/properties", {
        "label": "Home", "property_type": "HOUSE",
        "address": address(latitude=SYDNEY_LAT, longitude=SYDNEY_LNG),
    }, **auth(customer))

    assert r.status_code == 201, r.content
    assert r.json()["address"]["latitude"] == SYDNEY_LAT
    assert r.json()["address"]["longitude"] == SYDNEY_LNG


@pytest.mark.django_db
def test_property_gps_is_persisted(client, customer):
    body = post(client, "/api/properties", {
        "label": "Home", "property_type": "HOUSE",
        "address": address(latitude=SYDNEY_LAT, longitude=SYDNEY_LNG),
    }, **auth(customer)).json()

    stored = PropertyAddress.objects.get(property_id=body["id"])
    assert stored.latitude == Decimal(SYDNEY_LAT)
    assert stored.longitude == Decimal(SYDNEY_LNG)


@pytest.mark.django_db
def test_property_gps_stays_optional_for_backwards_compatibility(client, customer):
    """
    ⚠️ اختياري في الـschema لا تشجيعًا على تركه، بل توافقًا مع العقارات
       القائمة التي أُنشئت قبل وجود الحقل.
    """
    r = post(client, "/api/properties", {
        "label": "Home", "property_type": "HOUSE", "address": address(),
    }, **auth(customer))

    assert r.status_code == 201, r.content
    assert r.json()["address"]["latitude"] is None


@pytest.mark.django_db
def test_property_gps_can_be_added_later_by_patch(client, customer):
    """عقار بلا موقع يُصحَّح فيصير قابلًا للإسناد."""
    created = post(client, "/api/properties", {
        "label": "Home", "property_type": "HOUSE", "address": address(),
    }, **auth(customer)).json()

    r = patch(client, f"/api/properties/{created['id']}", {
        "address": {"latitude": SYDNEY_LAT, "longitude": SYDNEY_LNG},
    }, **auth(customer))

    assert r.status_code == 200, r.content
    assert r.json()["address"]["latitude"] == SYDNEY_LAT


@pytest.mark.django_db
def test_patching_gps_leaves_the_rest_of_the_address_alone(client, customer):
    created = post(client, "/api/properties", {
        "label": "Home", "property_type": "HOUSE", "address": address(),
    }, **auth(customer)).json()

    patch(client, f"/api/properties/{created['id']}", {
        "address": {"latitude": SYDNEY_LAT, "longitude": SYDNEY_LNG},
    }, **auth(customer))

    stored = PropertyAddress.objects.get(property_id=created["id"])
    assert stored.street_address == "12 George St"
    assert stored.postcode == "2000"


# ============================================================
# 2) التحقق من الحدود
# ============================================================
@pytest.mark.django_db
@pytest.mark.parametrize(
    "lat,lng",
    [
        ("-91.0", "151.0"),   # خط عرض أقل من -90
        ("91.0", "151.0"),    # أكبر من 90
        ("-33.8", "181.0"),   # خط طول أكبر من 180
        ("-33.8", "-181.0"),  # أقل من -180
    ],
)
def test_impossible_coordinates_are_rejected(client, customer, lat, lng):
    """🔒 فحص شكلي: قيمة مستحيلة جغرافيًا تُرفض قبل التخزين."""
    r = post(client, "/api/properties", {
        "label": "Bad", "property_type": "HOUSE",
        "address": address(latitude=lat, longitude=lng),
    }, **auth(customer))

    assert r.status_code == 422, r.content
    assert PropertyAddress.objects.count() == 0


@pytest.mark.django_db
def test_coordinates_outside_australia_are_accepted(client, customer):
    """
    📌 لا نقيّدها بحدود أستراليا: عنوان قرب الحدود البحرية أو انحراف GPS
       بسيط كان سيُرفض بلا مبرر. الرفض للمستحيل فقط.
    """
    r = post(client, "/api/properties", {
        "label": "Edge", "property_type": "HOUSE",
        "address": address(latitude="0.000000", longitude="0.000000"),
    }, **auth(customer))

    assert r.status_code == 201, r.content


@pytest.mark.django_db
def test_the_exact_bounds_are_accepted(client, customer):
    """الحدود شاملة: ±90 و±180 قيم صحيحة."""
    r = post(client, "/api/properties", {
        "label": "Pole", "property_type": "HOUSE",
        "address": address(latitude="-90.000000", longitude="180.000000"),
    }, **auth(customer))

    assert r.status_code == 201, r.content


# ============================================================
# 3) المقاول
# ============================================================
@pytest.mark.django_db
def test_contractor_profile_accepts_gps(client, worker):
    r = post(client, "/api/contractor/profile", {
        "business_name": "Sparkle Co",
        "latitude": "-33.868800", "longitude": "151.209300",
    }, **auth(worker))

    assert r.status_code == 201, r.content
    assert r.json()["latitude"] == "-33.868800"


@pytest.mark.django_db
def test_contractor_gps_can_be_updated(client, worker):
    post(client, "/api/contractor/profile", {"business_name": "Sparkle Co"},
         **auth(worker))

    r = patch(client, "/api/contractor/profile", {
        "latitude": "-37.813600", "longitude": "144.963100",
    }, **auth(worker))

    assert r.status_code == 200, r.content
    assert r.json()["latitude"] == "-37.813600"


@pytest.mark.django_db
@pytest.mark.parametrize("lat,lng", [("-91.0", "151.0"), ("-33.8", "181.0")])
def test_contractor_impossible_coordinates_are_rejected(client, worker, lat, lng):
    """نفس قاعدة العقار — لا ازدواج في المعايير بين الطرفين."""
    r = post(client, "/api/contractor/profile", {
        "business_name": "X", "latitude": lat, "longitude": lng,
    }, **auth(worker))

    assert r.status_code == 422, r.content
    assert ContractorProfile.objects.count() == 0


# ============================================================
# 4) 📌 الأثر الحقيقي — الإسناد
# ============================================================
def _make_eligible(worker, client, lat="-33.868800", lng="151.209300"):
    post(client, "/api/contractor/profile", {
        "business_name": "Sparkle Co", "latitude": lat, "longitude": lng,
    }, **auth(worker))
    profile = ContractorProfile.objects.get(user=worker)
    profile.availability_status = AvailabilityStatus.AVAILABLE
    profile.save(update_fields=["availability_status"])
    # §8: الإسناد يقرأ موقع الهاتف الحالي لا عنوان العمل.
    set_contractor_location(profile, (Decimal(lat), Decimal(lng)))
    BusinessRegistration.objects.create(
        contractor=profile, abn="12345678901", business_name="Sparkle Co",
        status=VerificationStatus.VERIFIED)
    InsuranceDocument.objects.create(
        contractor=profile, document_reference="POL-1",
        expiry_date=datetime.date.today() + datetime.timedelta(days=300),
        status=VerificationStatus.VERIFIED)
    return profile


def _book(client, customer, property_id, service, capture):
    """
    ⚠️ الإسناد يقع في hook بعد تثبيت المعاملة، وpytest لا يثبّتها —
       فبدون capture لا يُنشأ أي عرض ويبدو الاختبار ناجحًا كذبًا.
       نفس نمط tests/test_bookings_dispatch.py.
    """
    slot = (datetime.datetime.now(ZoneInfo("Australia/Sydney"))
            + datetime.timedelta(days=3)).replace(
        hour=10, minute=0, second=0, microsecond=0)
    with capture(execute=True):
        response = post(client, "/api/bookings", {
            "property_id": property_id,
            "service_selections": [
                {"service_type_id": str(service.id), "room_count": 2}
            ],
            "scheduled_at": slot.isoformat(),
        }, **auth(customer))
    return response


@pytest.mark.django_db
def test_a_property_with_gps_gets_dispatched(
    client, customer, worker, service, django_capture_on_commit_callbacks
):
    _make_eligible(worker, client)
    prop = post(client, "/api/properties", {
        "label": "Home", "property_type": "HOUSE",
        "address": address(latitude=SYDNEY_LAT, longitude=SYDNEY_LNG),
    }, **auth(customer)).json()

    booking = _book(client, customer, prop["id"], service,
                  django_capture_on_commit_callbacks).json()

    offers = DispatchOffer.objects.filter(booking_id=booking["id"])
    assert offers.count() == 1
    assert offers.first().distance_km is not None


@pytest.mark.django_db
def test_a_property_without_gps_is_never_dispatched(
    client, customer, worker, service, django_capture_on_commit_callbacks
):
    """
    ⚠️ السلوك الذي يجب أن تعرفه الواجهة: الحجز يُقبل (201) ثم لا يحدث
       شيء — لا عرض ولا خطأ. لهذا يجب جعل الإحداثيات إلزامية في الواجهة.
    """
    _make_eligible(worker, client)
    prop = post(client, "/api/properties", {
        "label": "NoGps", "property_type": "HOUSE", "address": address(),
    }, **auth(customer)).json()

    r = _book(client, customer, prop["id"], service,
                  django_capture_on_commit_callbacks)

    assert r.status_code == 201, r.content  # الحجز نفسه صالح
    assert DispatchOffer.objects.filter(booking_id=r.json()["id"]).count() == 0


@pytest.mark.django_db
def test_a_contractor_without_gps_is_never_offered_work(
    client, customer, worker, service, django_capture_on_commit_callbacks
):
    """الطرف الآخر من نفس القاعدة."""
    post(client, "/api/contractor/profile", {"business_name": "NoGps"},
         **auth(worker))
    profile = ContractorProfile.objects.get(user=worker)
    profile.availability_status = AvailabilityStatus.AVAILABLE
    profile.save(update_fields=["availability_status"])
    BusinessRegistration.objects.create(
        contractor=profile, abn="12345678901", business_name="X",
        status=VerificationStatus.VERIFIED)
    InsuranceDocument.objects.create(
        contractor=profile, document_reference="P",
        expiry_date=datetime.date.today() + datetime.timedelta(days=300),
        status=VerificationStatus.VERIFIED)

    prop = post(client, "/api/properties", {
        "label": "Home", "property_type": "HOUSE",
        "address": address(latitude=SYDNEY_LAT, longitude=SYDNEY_LNG),
    }, **auth(customer)).json()

    booking = _book(client, customer, prop["id"], service,
                  django_capture_on_commit_callbacks).json()

    assert DispatchOffer.objects.filter(booking_id=booking["id"]).count() == 0


@pytest.mark.django_db
def test_adding_gps_later_makes_a_booking_dispatchable(
    client, customer, worker, service, django_capture_on_commit_callbacks
):
    """📌 مسار الإصلاح الكامل: عقار بلا موقع ← PATCH ← حجز جديد يُسنَد."""
    _make_eligible(worker, client)
    prop = post(client, "/api/properties", {
        "label": "Home", "property_type": "HOUSE", "address": address(),
    }, **auth(customer)).json()

    first = _book(client, customer, prop["id"], service,
                  django_capture_on_commit_callbacks).json()
    assert DispatchOffer.objects.filter(booking_id=first["id"]).count() == 0

    patch(client, f"/api/properties/{prop['id']}", {
        "address": {"latitude": SYDNEY_LAT, "longitude": SYDNEY_LNG},
    }, **auth(customer))

    second = _book(client, customer, prop["id"], service,
                  django_capture_on_commit_callbacks).json()
    assert DispatchOffer.objects.filter(booking_id=second["id"]).count() == 1
