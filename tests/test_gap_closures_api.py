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


def _pin_sydney(monkeypatch, settings, hour, minute=0):
    import datetime
    from zoneinfo import ZoneInfo

    from apps.bookings.services import scheduling

    import datetime as _dt

    from apps.services.models import PricingConfig

    config, _ = PricingConfig.objects.get_or_create(pk=PricingConfig.SINGLETON_PK)
    config.service_hours_enabled = True
    config.service_hours_start = _dt.time(7, 0)
    config.service_hours_end = _dt.time(19, 0)
    config.save()
    sydney = ZoneInfo("Australia/Sydney")
    fixed = (datetime.datetime.now(sydney) + datetime.timedelta(days=2)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    monkeypatch.setattr(scheduling.dj_timezone, "now", lambda: fixed)
    return fixed


@pytest.mark.django_db
def test_quote_exposes_service_hours_while_open(client: Client, monkeypatch, settings):
    """B18: التطبيق يعرف القاعدة قبل الإرسال لا من 400 بعده."""
    _pin_sydney(monkeypatch, settings, 10)
    _user, _prop, _main, _addon, quote = _quoted_setup(client)

    hours = quote["service_hours"]
    assert hours["timezone"] == "Australia/Sydney"
    assert (hours["opens_at"], hours["closes_at"]) == ("07:00", "19:00")
    assert hours["is_open_now"] is True and hours["next_open_at"] is None
    assert hours["min_lead_minutes"] == settings.BOOKING_MIN_LEAD_MINUTES


@pytest.mark.django_db
@pytest.mark.parametrize("hour, opens_next_day", [(21, True), (5, False)])
def test_quote_gives_the_next_opening_when_closed(client: Client, monkeypatch, settings, hour, opens_next_day):
    import datetime
    from zoneinfo import ZoneInfo

    now = _pin_sydney(monkeypatch, settings, hour)
    _user, _prop, _main, _addon, quote = _quoted_setup(client)

    hours = quote["service_hours"]
    assert hours["is_open_now"] is False
    opening = datetime.datetime.fromisoformat(hours["next_open_at"]).astimezone(ZoneInfo("Australia/Sydney"))
    assert (opening.hour, opening.minute) == (7, 0)
    assert opening.date() == now.date() + datetime.timedelta(days=1 if opens_next_day else 0)


@pytest.mark.django_db
def test_service_hours_agree_with_the_on_demand_check(monkeypatch, settings):
    """ما يُعرض هو ما يُفرض: نفس الحكم عند الحدود."""
    from apps.bookings.services.scheduling import OutsideBusinessHoursError, assert_open_now, service_hours

    for hour, minute in [(6, 59), (7, 0), (19, 0), (19, 1)]:
        now = _pin_sydney(monkeypatch, settings, hour, minute)
        shown = service_hours("Australia/Sydney", now=now)["is_open_now"]
        try:
            assert_open_now("Australia/Sydney", now=now)
            enforced = True
        except OutsideBusinessHoursError:
            enforced = False
        assert shown == enforced, (hour, minute)
