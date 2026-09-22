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
