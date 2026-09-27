"""
سبب إغلاق العرض — تقرير التطبيق B15.

العامل الذي فاته إشعار offer.cancelled يفتح العرض فيجب أن يعرف أن الحجز
أُلغي، لا أن "المهلة انتهت".
"""

import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.bookings.models import DispatchOffer, DispatchOfferStatus, OfferCloseReason
from apps.bookings.services import bookings as bookings_svc
from apps.bookings.services.dispatch import assign_next_contractor
from apps.bookings.tasks import expire_pending_offers
from tests.test_audit_fixes import make_pending_booking
from tests.test_bookings_dispatch import (  # noqa: F401
    auth, customer, general, make_contractor, pricing, prop,
)


def offer_for(customer, prop, general, phone="+61400095001"):
    user, _profile = make_contractor(phone)
    booking = make_pending_booking(customer, prop, general)
    offer = assign_next_contractor(booking)
    assert offer is not None
    return user, booking, offer


def detail(client, user, offer):
    r = client.get(f"/api/contractor/offers/{offer.id}", **auth(user))
    assert r.status_code == 200, r.content
    return r.json()


@pytest.mark.django_db
def test_open_offer_has_no_close_reason(client, customer, prop, general, pricing):
    user, _booking, offer = offer_for(customer, prop, general)
    body = detail(client, user, offer)
    assert body["status"] == "PENDING" and body["close_reason"] is None


@pytest.mark.django_db
def test_declined_offer_says_declined(client, customer, prop, general, pricing):
    user, _booking, offer = offer_for(customer, prop, general)
    r = client.post(f"/api/contractor/offers/{offer.id}/decline", **auth(user))
    assert r.status_code == 200, r.content
    assert detail(client, user, offer)["close_reason"] == "DECLINED"


@pytest.mark.django_db
def test_unanswered_offer_says_timed_out(client, customer, prop, general, pricing):
    user, _booking, offer = offer_for(customer, prop, general)
    DispatchOffer.objects.filter(pk=offer.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    expire_pending_offers()
    body = detail(client, user, offer)
    assert body["status"] == "EXPIRED" and body["close_reason"] == "TIMED_OUT"


@pytest.mark.django_db
def test_cancelled_booking_is_distinguishable_from_a_timeout(client, customer, prop, general, pricing):
    user, booking, offer = offer_for(customer, prop, general)
    bookings_svc.cancel_booking(customer, booking.id, reason="Changed my mind")

    body = detail(client, user, offer)
    assert body["status"] == "EXPIRED"
    assert body["close_reason"] == "BOOKING_CANCELLED"
    listed = client.get("/api/contractor/offers?include_closed=true", **auth(user)).json()
    assert [o["close_reason"] for o in listed] == ["BOOKING_CANCELLED"]


@pytest.mark.django_db
def test_backfill_classifies_offers_closed_before_the_field(customer, prop, general, pricing):
    _u1, cancelled, c_offer = offer_for(customer, prop, general, phone="+61400095011")
    bookings_svc.cancel_booking(customer, cancelled.id)
    _u2, _b2, t_offer = offer_for(customer, prop, general, phone="+61400095012")
    DispatchOffer.objects.filter(pk=t_offer.pk).update(status=DispatchOfferStatus.EXPIRED, responded_at=timezone.now())
    _u3, _b3, d_offer = offer_for(customer, prop, general, phone="+61400095013")
    DispatchOffer.objects.filter(pk=d_offer.pk).update(status=DispatchOfferStatus.DECLINED, responded_at=timezone.now())
    DispatchOffer.objects.update(close_reason="")  # كما كانت قبل الحقل

    migration = importlib.import_module("apps.bookings.migrations.0012_dispatchoffer_close_reason")
    migration.backfill_close_reason(django_apps, None)

    reasons = dict(DispatchOffer.objects.values_list("pk", "close_reason"))
    assert reasons[c_offer.pk] == OfferCloseReason.BOOKING_CANCELLED
    assert reasons[t_offer.pk] == OfferCloseReason.TIMED_OUT
    assert reasons[d_offer.pk] == OfferCloseReason.DECLINED
