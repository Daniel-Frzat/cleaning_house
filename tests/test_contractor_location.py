"""الموقع الحالي للمقاول — مدخل الإسناد الفوري (كان بلا endpoint)."""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.bookings.models import DispatchOffer
from apps.bookings.services.dispatch import assign_next_contractor
from apps.contractors.models import ContractorCurrentLocation
from tests.test_audit_fixes import make_pending_booking
from tests.test_bookings_dispatch import (  # noqa: F401
    NEAR, auth, customer, general, make_contractor, pricing, prop,
)


def put(client, url, payload, **extra):
    return client.put(url, data=json.dumps(payload), content_type="application/json", **extra)


@pytest.mark.django_db
def test_reported_location_makes_the_contractor_dispatchable(customer, prop, general, pricing):
    user, profile = make_contractor("+61400090001", coords=NEAR)
    ContractorCurrentLocation.objects.filter(contractor=profile).delete()
    booking = make_pending_booking(customer, prop, general)
    assert assign_next_contractor(booking) is None  # لا موقع حالي → لا مرشَّح

    client = Client()
    r = put(client, "/api/contractor/location", {
        "latitude": str(NEAR[0]), "longitude": str(NEAR[1]), "accuracy": "12",
        "recorded_at": timezone.now().isoformat(),
    }, **auth(user))
    assert r.status_code == 200, r.content
    assert r.json()["is_fresh"] is True and r.json()["fresh_for_seconds"] > 290

    booking2 = make_pending_booking(customer, prop, general)
    offer = assign_next_contractor(booking2)
    assert offer is not None and offer.contractor_id == profile.id
    assert client.get("/api/contractor/location", **auth(user)).json()["is_fresh"] is True


@pytest.mark.django_db
def test_future_timestamp_and_non_contractor_are_refused(customer):
    user, _ = make_contractor("+61400090002", coords=NEAR)
    client = Client()
    r = put(client, "/api/contractor/location", {
        "latitude": "-33.86", "longitude": "151.2",
        "recorded_at": (timezone.now() + timedelta(minutes=5)).isoformat(),
    }, **auth(user))
    assert r.status_code == 400 and r.json()["code"] == "invalid_location"

    r = put(client, "/api/contractor/location", {
        "latitude": "-33.86", "longitude": "151.2", "recorded_at": timezone.now().isoformat(),
    }, **auth(customer))
    assert r.status_code == 403


@pytest.mark.django_db
def test_unconfigured_directions_provider_falls_back_instead_of_crashing(customer, prop, general, pricing, settings):
    """الإنتاج: لا مزوّد اتجاهات → رجوع للخط المستقيم (إن سُمح) لا انهيار."""
    settings.DIRECTIONS_PROVIDER_ADAPTER_CLASS = "adapters.directions.fake.FakeDirectionsAdapter"
    settings.DIRECTIONS_ALLOW_FAKE_ADAPTER = False
    settings.DEBUG = False
    settings.DISPATCH_ALLOW_HAVERSINE_FALLBACK = True
    make_contractor("+61400090003", coords=NEAR)
    offer = assign_next_contractor(make_pending_booking(customer, prop, general))
    assert offer is not None and offer.distance_source == "HAVERSINE"

    settings.DISPATCH_ALLOW_HAVERSINE_FALLBACK = False
    assert assign_next_contractor(make_pending_booking(customer, prop, general)) is None


@pytest.mark.django_db
def test_raw_gps_precision_is_rounded_not_rejected(client, customer):
    """
    GPS الجهاز يعيد 12+ خانة عشرية — كانت تُرفض بـ422 decimal_max_places
    عند إضافة عقار من التطبيق. تُقرَّب الآن إلى دقة التخزين (6 خانات).
    """
    r = client.post(
        "/api/properties",
        data=json.dumps({
            "label": "home",
            "property_type": "HOUSE",
            "address": {
                "street_address": "1 Test St",
                "suburb": "Adelaide",
                "state": "SA",
                "postcode": "5000",
                "latitude": -34.92850012345678,
                "longitude": 138.60074567891234,
            },
        }),
        content_type="application/json",
        **auth(customer),
    )
    assert r.status_code == 201, r.content
    address = r.json()["address"]
    assert Decimal(str(address["latitude"])) == Decimal("-34.928500")
    assert Decimal(str(address["longitude"])) == Decimal("138.600746")


@pytest.mark.django_db
def test_impossible_coordinates_are_still_rejected(client, customer):
    r = client.post(
        "/api/properties",
        data=json.dumps({
            "label": "home",
            "property_type": "HOUSE",
            "address": {
                "street_address": "1 Test St",
                "suburb": "Adelaide",
                "state": "SA",
                "postcode": "5000",
                "latitude": 91.0000001,
                "longitude": 138.6,
            },
        }),
        content_type="application/json",
        **auth(customer),
    )
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
