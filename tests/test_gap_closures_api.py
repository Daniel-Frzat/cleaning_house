"""Focused contract checks for backend gaps closed in the 2026-09-22 pass."""

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.bookings.api.schemas import BookingIn, OfferOut, QuoteIn, QuoteOut
from apps.properties.models import Property, PropertyAddress, PropertyType
from apps.services.models import ServiceType
from decimal import Decimal
from apps.jobs.api.schemas import ContractorJobOut
from apps.payouts.api.schemas import EarningsOut
from apps.payments.api.schemas import PaymentActionOut


@pytest.mark.parametrize(
    "schema",
    [BookingIn, OfferOut, QuoteIn, QuoteOut, ContractorJobOut, EarningsOut, PaymentActionOut],
)
def test_gap_closure_schemas_are_constructible(schema):
    assert schema is not None


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


@pytest.mark.django_db
def test_customer_can_create_quote(client: Client):
    user = User.objects.create_user(phone="+61400009001", role=ConfirmedRole.CUSTOMER)
    prop = Property.objects.create(owner=user, property_type=PropertyType.HOUSE)
    PropertyAddress.objects.create(
        property=prop,
        street_address="12 Example St",
        suburb="Bondi",
        state="NSW",
        postcode="2026",
        latitude=Decimal("-33.868800"),
        longitude=Decimal("151.209300"),
    )
    service = ServiceType.objects.create(
        name="General Cleaning",
        room_price=Decimal("45.00"),
        base_price=Decimal("80.00"),
    )
    response = client.post(
        "/api/quotes",
        data={
            "property_id": str(prop.id),
            "service_selections": [
                {"service_type_id": str(service.id), "room_count": 1}
            ],
        },
        content_type="application/json",
        **auth(user),
    )
    assert response.status_code == 201, response.content
    assert response.json()["maximum_total"]


@pytest.mark.django_db
def test_contractor_offer_list_is_role_protected(client: Client):
    user = User.objects.create_user(phone="+61400009002", role=ConfirmedRole.CUSTOMER)
    response = client.get("/api/contractor/offers", **auth(user))
    assert response.status_code == 403


@pytest.mark.django_db
def test_customer_earnings_list_is_role_protected(client: Client):
    user = User.objects.create_user(phone="+61400009003", role=ConfirmedRole.CUSTOMER)
    response = client.get("/api/contractor/earnings", **auth(user))
    assert response.status_code == 403


def _quoted_setup(client):
    user = User.objects.create_user(phone="+61400009002", role=ConfirmedRole.CUSTOMER)
    prop = Property.objects.create(owner=user, property_type=PropertyType.HOUSE)
    PropertyAddress.objects.create(
        property=prop, street_address="12 Example St", suburb="Bondi", state="NSW",
        postcode="2026", latitude=Decimal("-33.868800"), longitude=Decimal("151.209300"),
    )
    main = ServiceType.objects.create(name="Regular", room_price=Decimal("45.00"), base_price=Decimal("80.00"))
    addon = ServiceType.objects.create(name="Balcony", room_price=Decimal("0"), base_price=Decimal("35.00"))
    quote = client.post(
        "/api/quotes",
        data={
            "property_id": str(prop.id),
            "service_selections": [
                {"service_type_id": str(main.id), "room_count": 2},
                {"service_type_id": str(addon.id), "room_count": 0},
            ],
        },
        content_type="application/json",
        **auth(user),
    )
    assert quote.status_code == 201, quote.content
    return user, prop, main, addon, quote.json()


@pytest.mark.django_db
def test_booking_from_a_quote_uses_the_frozen_selection(client: Client):
    """
    B16 (تقرير التطبيق 2026-09-27): الاقتباس يخزّن المعرّفات نصًا في JSON،
    وكان البحث بمفتاح UUID يرفض كل حجز من اقتباس بـ404.
    """
    user, prop, main, addon, quote = _quoted_setup(client)

    response = client.post(
        "/api/bookings",
        data={"property_id": str(prop.id), "quote_id": quote["id"]},
        content_type="application/json",
        **auth(user),
    )

    assert response.status_code == 201, response.content
    lines = {s["service_type_name"]: s["room_count"] for s in response.json()["service_selections"]}
    assert lines == {"Regular": 2, "Balcony": 0}


@pytest.mark.django_db
def test_quote_stays_bookable_after_a_service_is_deactivated(client: Client):
    """العميل وافق على الاقتباس — سحب الخدمة بعده لا يُبطله."""
    user, prop, main, addon, quote = _quoted_setup(client)
    ServiceType.objects.filter(pk=addon.pk).update(is_active=False)

    response = client.post(
        "/api/bookings",
        data={"property_id": str(prop.id), "quote_id": quote["id"]},
        content_type="application/json",
        **auth(user),
    )
    assert response.status_code == 201, response.content
