"""
ساعات الخدمة من الداشبورد (قرار PO — 2026-09-27).

الافتراضي: معطّلة — الخدمة متاحة على مدار الساعة. الإدارة تفعّلها وتضبط
البداية والنهاية بتوقيت العقار، وقد تعبر النافذة منتصف الليل.
"""

import datetime
import json
from zoneinfo import ZoneInfo

import pytest

from apps.bookings.services import scheduling
from apps.services.models import PricingConfig
from tests.test_bookings_scheduling import auth, customer, general, post, property_nsw  # noqa: F401
from tests.test_services_catalog_api import admin_user  # noqa: F401

SYDNEY = ZoneInfo("Australia/Sydney")


def pin(monkeypatch, hour, minute=0):
    fixed = (datetime.datetime.now(SYDNEY) + datetime.timedelta(days=2)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    monkeypatch.setattr(scheduling.dj_timezone, "now", lambda: fixed)
    return fixed


def patch_config(client, admin, payload):
    return client.patch(
        "/api/admin/pricing-config", data=json.dumps(payload),
        content_type="application/json", **auth(admin),
    )


def request_now(client, customer, prop, service):
    return post(client, "/api/bookings", {
        "property_id": str(prop.id),
        "service_selections": [{"service_type_id": str(service.id), "room_count": 1}],
    }, **auth(customer))


@pytest.mark.django_db
def test_default_is_open_around_the_clock(client, customer, property_nsw, general, monkeypatch):
    pin(monkeypatch, 3)
    assert PricingConfig.objects.count() == 0 or not PricingConfig.objects.get().service_hours_enabled

    r = request_now(client, customer, property_nsw, general)
    assert r.status_code == 201, r.content
    hours = scheduling.service_hours("Australia/Sydney")
    assert hours["enabled"] is False and hours["is_open_now"] is True
    assert hours["opens_at"] is None and hours["closes_at"] is None


@pytest.mark.django_db
def test_admin_enables_hours_from_the_dashboard(client, admin_user, customer, property_nsw, general, monkeypatch):
    r = patch_config(client, admin_user, {
        "service_hours_enabled": True, "service_hours_start": "08:00", "service_hours_end": "17:30",
    })
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["service_hours_enabled"] is True
    assert body["service_hours_start"].startswith("08:00") and body["service_hours_end"].startswith("17:30")

    pin(monkeypatch, 18)
    refused = request_now(client, customer, property_nsw, general)
    assert refused.status_code == 400 and refused.json()["code"] == "outside_business_hours"
    assert "08:00" in refused.json()["detail"] and "17:30" in refused.json()["detail"]

    pin(monkeypatch, 9)
    assert request_now(client, customer, property_nsw, general).status_code == 201


@pytest.mark.django_db
def test_turning_hours_off_reopens_around_the_clock(client, admin_user, customer, property_nsw, general, monkeypatch, business_hours):
    pin(monkeypatch, 23)
    assert request_now(client, customer, property_nsw, general).status_code == 400
    assert patch_config(client, admin_user, {"service_hours_enabled": False}).status_code == 200
    assert request_now(client, customer, property_nsw, general).status_code == 201


@pytest.mark.django_db
@pytest.mark.parametrize("hour, minute, is_open", [(21, 0, True), (1, 30, True), (2, 0, True), (2, 1, False), (19, 59, False)])
def test_overnight_window(monkeypatch, hour, minute, is_open, db):
    config, _ = PricingConfig.objects.get_or_create(pk=PricingConfig.SINGLETON_PK)
    config.service_hours_enabled = True
    config.service_hours_start = datetime.time(20, 0)
    config.service_hours_end = datetime.time(2, 0)
    config.save()

    now = pin(monkeypatch, hour, minute)
    hours = scheduling.service_hours("Australia/Sydney", now=now)
    assert hours["is_open_now"] is is_open
    if not is_open:
        opening = datetime.datetime.fromisoformat(str(hours["next_open_at"])).astimezone(SYDNEY)
        assert (opening.hour, opening.minute) == (20, 0) and opening.date() == now.date()


@pytest.mark.django_db
def test_identical_start_and_end_are_refused(client, admin_user):
    r = patch_config(client, admin_user, {
        "service_hours_enabled": True, "service_hours_start": "09:00", "service_hours_end": "09:00",
    })
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_hours_are_not_a_pricing_change(client, admin_user):
    v0 = client.get("/api/admin/pricing-config", **auth(admin_user)).json()["pricing_version"]
    v1 = patch_config(client, admin_user, {"service_hours_enabled": True}).json()["pricing_version"]
    assert v1 == v0


@pytest.mark.django_db
def test_scheduled_visits_follow_the_same_window(client, customer, property_nsw, general, business_hours):
    late = (datetime.datetime.now(SYDNEY) + datetime.timedelta(days=3)).replace(hour=22, minute=0, second=0, microsecond=0)
    r = post(client, "/api/bookings", {
        "property_id": str(property_nsw.id),
        "service_selections": [{"service_type_id": str(general.id), "room_count": 1}],
        "scheduled_at": late.isoformat(),
    }, **auth(customer))
    assert r.status_code == 400 and r.json()["code"] == "outside_business_hours"

    business_hours.service_hours_enabled = False
    business_hours.save()
    r = post(client, "/api/bookings", {
        "property_id": str(property_nsw.id),
        "service_selections": [{"service_type_id": str(general.id), "room_count": 1}],
        "scheduled_at": late.isoformat(),
    }, **auth(customer))
    assert r.status_code == 201, r.content
