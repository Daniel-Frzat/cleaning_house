"""
إصلاحات المراجعة الشاملة (2026-09-24) — الحجوزات والإرسال والدفع والمهام
والعقارات والدعم.

كل اختبار هنا يثبّت سلوكًا كان خاطئًا قبل المراجعة، والتعليق يذكر السيناريو.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserStatus
from apps.bookings.models import (
    Booking,
    BookingServiceSelection,
    BookingStatus,
    DispatchOffer,
    DispatchOfferStatus,
    DispatchStatus,
)
from apps.bookings.services import bookings as booking_svc
from apps.bookings.services import offers as offers_svc
from apps.bookings.services.dispatch import (
    assign_next_contractor,
    find_candidates,
    redispatch_stranded_bookings,
)
from apps.bookings.services.scheduling import ScheduledTooSoonError
from apps.bookings.tasks import expire_pending_offers, repair_confirmed_bookings
from apps.jobs.models import Job, JobStatus
from apps.jobs.services import jobs as jobs_svc
from apps.jobs.services import photos as photos_svc
from apps.payments.models import Payment, PaymentStatus
from apps.payouts.models import Payout
from apps.payouts.services import payouts as payouts_svc
from apps.properties.services import properties as properties_svc
from apps.properties.services.postcodes import postcode_matches_state
from apps.support.models import SupportRequest, SupportStatus
from apps.support.services import support as support_svc
from tests.helpers import JPEG_BYTES, PNG_BYTES, mark_paid
from tests.test_bookings_dispatch import (  # noqa: F401 — fixtures
    FAR,
    NEAR,
    SYDNEY,
    auth,
    client,
    create_booking,
    customer,
    general,
    make_contractor,
    make_property,
    make_user,
    post,
    pricing,
    prop,
)

PERTH = (Decimal("-31.950500"), Decimal("115.860500"))


def tomorrow_10am():
    return (timezone.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def make_pending_booking(customer, prop, service, scheduled_at=None):
    booking = Booking.objects.create(
        customer=customer,
        property=prop,
        status=BookingStatus.PENDING,
        scheduled_at=scheduled_at or timezone.now() + timedelta(days=1),
        customer_timezone="Australia/Sydney",
    )
    BookingServiceSelection.objects.create(booking=booking, service_type=service, room_count=3)
    return booking


# ============================================================
# 1) قبول العرض — أقفال وحالة الحجز
# ============================================================
@pytest.mark.django_db
def test_second_acceptance_cannot_overwrite_frozen_price(customer, prop, general, pricing):
    """
    السيناريو: قبول من جهاز ورفض من آخر أوصلا عرضًا ثانيًا لمقاول آخر.
    قبول المقاول الثاني كان يكتب فوق السعر المجمّد والمقاول المُسنَد.
    """
    user_a, a = make_contractor("+61400070001", coords=NEAR)
    user_b, b = make_contractor("+61400070002", coords=FAR)
    booking = make_pending_booking(customer, prop, general)
    offer_a = assign_next_contractor(booking)
    offers_svc.accept_offer(user_a, offer_a.id)
    booking.refresh_from_db()
    frozen = booking.computed_price

    stale = DispatchOffer.objects.create(
        booking=booking, contractor=b, status=DispatchOfferStatus.PENDING,
        distance_km=Decimal("22"), expires_at=timezone.now() + timedelta(minutes=30),
    )
    with pytest.raises(offers_svc.OfferNotActionableError):
        offers_svc.accept_offer(user_b, stale.id)

    booking.refresh_from_db()
    assert booking.computed_price == frozen
    assert booking.assigned_contractor_id == a.id


@pytest.mark.django_db
def test_decline_on_confirmed_booking_does_not_cascade(customer, prop, general, pricing):
    user_a, _ = make_contractor("+61400070011", coords=NEAR)
    user_b, b = make_contractor("+61400070012", coords=FAR)
    booking = make_pending_booking(customer, prop, general)
    offer_a = assign_next_contractor(booking)
    offers_svc.accept_offer(user_a, offer_a.id)
    stale = DispatchOffer.objects.create(
        booking=booking, contractor=b, status=DispatchOfferStatus.PENDING,
        distance_km=Decimal("22"), expires_at=timezone.now() + timedelta(minutes=30),
    )

    with pytest.raises(offers_svc.OfferNotActionableError):
        offers_svc.decline_offer(user_b, stale.id)
    assert DispatchOffer.objects.filter(booking=booking).count() == 2


@pytest.mark.django_db
def test_acceptance_after_visit_time_is_refused(customer, prop, general, pricing):
    user, profile = make_contractor("+61400070021", coords=NEAR)
    booking = make_pending_booking(customer, prop, general)
    offer = DispatchOffer.objects.create(
        booking=booking, contractor=profile, status=DispatchOfferStatus.PENDING,
        distance_km=Decimal("1"), expires_at=timezone.now() + timedelta(minutes=30),
    )
    Booking.objects.filter(pk=booking.pk).update(scheduled_at=timezone.now() - timedelta(minutes=1))

    with pytest.raises(offers_svc.OfferNotActionableError):
        offers_svc.accept_offer(user, offer.id)


@pytest.mark.django_db
def test_service_deactivated_after_booking_can_still_be_accepted(customer, prop, general, pricing):
    """كان: InactiveServiceError غير ملتقط → 500 لكل مقاول في التتابع."""
    user, _ = make_contractor("+61400070031", coords=NEAR)
    booking = make_pending_booking(customer, prop, general)
    offer = assign_next_contractor(booking)
    general.is_active = False
    general.save()

    offers_svc.accept_offer(user, offer.id)
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CONFIRMED
    assert booking.computed_price is not None


# ============================================================
# 2) محرّك الإرسال
# ============================================================
@pytest.mark.django_db
@pytest.mark.parametrize("status", [UserStatus.SUSPENDED, UserStatus.INACTIVE])
def test_suspended_contractor_receives_no_offers(customer, prop, general, status):
    user, _ = make_contractor("+61400070041", coords=NEAR)
    user.status = status
    user.save()
    booking = make_pending_booking(customer, prop, general)
    assert find_candidates(booking) == []


@pytest.mark.django_db
def test_contractor_beyond_max_distance_is_not_offered(customer, prop, general, settings):
    settings.DISPATCH_MAX_DISTANCE_KM = 50
    make_contractor("+61400070051", coords=PERTH)
    booking = make_pending_booking(customer, prop, general)
    assert find_candidates(booking) == []

    settings.DISPATCH_MAX_DISTANCE_KM = 0  # بلا حد
    assert len(find_candidates(booking)) == 1


@pytest.mark.django_db
def test_no_second_live_offer_for_the_same_booking(customer, prop, general):
    """كان: إعادة جدولة مزدوجة تطلق جولتين فيتلقى مقاولان عرضين حيّين."""
    make_contractor("+61400070061", coords=NEAR)
    make_contractor("+61400070062", coords=FAR)
    booking = make_pending_booking(customer, prop, general)
    assert assign_next_contractor(booking) is not None
    assert assign_next_contractor(booking) is None
    assert DispatchOffer.objects.filter(booking=booking).count() == 1


@pytest.mark.django_db
def test_offer_never_outlives_the_visit(customer, prop, general):
    make_contractor("+61400070071", coords=NEAR)
    visit = timezone.now() + timedelta(minutes=20)
    booking = make_pending_booking(customer, prop, general, scheduled_at=visit)
    offer = assign_next_contractor(booking)
    assert offer.expires_at == visit


@pytest.mark.django_db
def test_past_visit_ends_the_search(customer, prop, general):
    make_contractor("+61400070081", coords=NEAR)
    booking = make_pending_booking(customer, prop, general)
    Booking.objects.filter(pk=booking.pk).update(scheduled_at=timezone.now() - timedelta(hours=1))
    assert assign_next_contractor(booking) is None
    booking.refresh_from_db()
    assert booking.dispatch_status == DispatchStatus.NO_CONTRACTOR


@pytest.mark.django_db
def test_stranded_searching_booking_is_redispatched(customer, prop, general):
    make_contractor("+61400070091", coords=NEAR)
    booking = make_pending_booking(customer, prop, general)
    Booking.objects.filter(pk=booking.pk).update(
        dispatch_status=DispatchStatus.SEARCHING,
        updated_at=timezone.now() - timedelta(minutes=30),
    )
    assert redispatch_stranded_bookings() == 1
    assert DispatchOffer.objects.filter(booking=booking, status=DispatchOfferStatus.PENDING).exists()


@pytest.mark.django_db
def test_one_failing_cascade_does_not_strand_the_others(customer, prop, general, monkeypatch):
    _, c1 = make_contractor("+61400070101", coords=NEAR)
    b1 = make_pending_booking(customer, prop, general)
    b2 = make_pending_booking(customer, prop, general)
    for b in (b1, b2):
        DispatchOffer.objects.create(
            booking=b, contractor=c1, status=DispatchOfferStatus.PENDING,
            distance_km=Decimal("1"), expires_at=timezone.now() - timedelta(minutes=1),
        )

    from apps.bookings import tasks

    calls = []

    def flaky(booking):
        calls.append(booking.id)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return None

    monkeypatch.setattr(tasks, "assign_next_contractor", flaky)
    result = expire_pending_offers()
    assert result["expired"] == 2
    assert len(calls) == 2


# ============================================================
# 3) المهلة الدنيا والمنطقة الزمنية وعقار محذوف
# ============================================================
def test_booking_too_soon_is_refused(settings, monkeypatch):
    """الساعة مثبّتة على 10:00 بسيدني حتى لا يعتمد الاختبار على وقت تشغيله."""
    import datetime
    from zoneinfo import ZoneInfo

    from apps.bookings.services import scheduling

    sydney = ZoneInfo("Australia/Sydney")
    fixed_now = datetime.datetime(2030, 3, 5, 10, 0, tzinfo=sydney)
    monkeypatch.setattr(scheduling.dj_timezone, "now", lambda: fixed_now)
    settings.BOOKING_MIN_LEAD_MINUTES = 120

    with pytest.raises(ScheduledTooSoonError):
        scheduling.normalize_scheduled_at(fixed_now + timedelta(minutes=90), "Australia/Sydney")
    # ساعتان بالضبط مقبولة
    scheduling.normalize_scheduled_at(fixed_now + timedelta(minutes=120), "Australia/Sydney")


@pytest.mark.django_db
def test_reschedule_rejects_a_foreign_timezone(client, customer, prop, general):
    booking = make_pending_booking(customer, prop, general)
    Booking.objects.filter(pk=booking.pk).update(dispatch_status=DispatchStatus.NO_CONTRACTOR)
    r = client.post(
        f"/api/bookings/{booking.id}/reschedule",
        data={"scheduled_at": (timezone.now() + timedelta(days=2)).isoformat(),
              "timezone": "Pacific/Kiritimati"},
        content_type="application/json",
        **auth(customer),
    )
    assert r.status_code == 400, r.content
    assert r.json()["code"] == "timezone_not_allowed"


@pytest.mark.django_db
def test_booking_on_removed_property_is_refused(client, customer, prop, general):
    prop.is_active = False
    prop.save()
    with pytest.raises(booking_svc.PropertyInactiveError):
        booking_svc.create_booking(
            customer, property_id=prop.id,
            service_selections=[{"service_type_id": general.id, "room_count": 1}],
        )


# ============================================================
# 4) عروض المقاول — قائمة وتفاصيل
# ============================================================
@pytest.mark.django_db
def test_contractor_sees_own_pending_offers_without_street_address(client, customer, prop, general):
    user, _ = make_contractor("+61400070111", coords=NEAR)
    other, _ = make_contractor("+61400070112", coords=FAR)
    booking = make_pending_booking(customer, prop, general)
    offer = assign_next_contractor(booking)

    r = client.get("/api/contractor/offers", **auth(user))
    assert r.status_code == 200
    body = r.json()
    assert [o["id"] for o in body] == [str(offer.id)]
    assert body[0]["suburb"] == "Bondi"
    assert "street_address" not in body[0]
    assert "access_notes" not in body[0]
    assert body[0]["services"][0]["room_count"] == 3

    assert client.get("/api/contractor/offers", **auth(other)).json() == []
    assert client.get(f"/api/contractor/offers/{offer.id}", **auth(user)).status_code == 200
    assert client.get(f"/api/contractor/offers/{offer.id}", **auth(other)).status_code == 403
    assert client.get("/api/contractor/offers", **auth(customer)).status_code == 403


# ============================================================
# 5) الدفع — لا دفع للمقاول قبل نجاح دفع العميل
# ============================================================
def make_confirmed(customer, prop, general, profile):
    booking = Booking.objects.create(
        customer=customer, property=prop, status=BookingStatus.CONFIRMED,
        computed_price=Decimal("215.00"), assigned_contractor=profile,
    )
    BookingServiceSelection.objects.create(booking=booking, service_type=general, room_count=3)
    return booking


@pytest.mark.django_db
@pytest.mark.parametrize("payment_status", [None, PaymentStatus.PENDING, PaymentStatus.FAILED])
def test_no_payout_without_successful_customer_payment(customer, prop, general, payment_status):
    _, profile = make_contractor("+61400070121")
    booking = make_confirmed(customer, prop, general, profile)
    if payment_status:
        Payment.objects.create(booking=booking, amount=booking.computed_price,
                               method="CARD", status=payment_status)
    Job.objects.create(booking=booking, status=JobStatus.COMPLETED, confirmed_at=timezone.now())

    with pytest.raises(payouts_svc.CustomerPaymentNotSettledError):
        payouts_svc.release_payout_for_booking(booking)
    assert not Payout.objects.filter(booking=booking).exists()


@pytest.mark.django_db
def test_job_cannot_start_before_customer_payment_succeeds(client, customer, prop, general):
    user, profile = make_contractor("+61400070131")
    booking = make_confirmed(customer, prop, general, profile)
    job = jobs_svc.create_job_for_booking(booking)
    r = client.post(f"/api/contractor/jobs/{job.id}/start", **auth(user))
    assert r.status_code == 409
    assert r.json()["code"] == "payment_not_settled"


@pytest.mark.django_db
def test_provider_exception_keeps_a_pending_payment_record(customer, prop, general, monkeypatch):
    """كان: الاستثناء يُرجع المعاملة كلها فيضيع أثر خصم ربما تم."""
    _, profile = make_contractor("+61400070141")
    booking = make_confirmed(customer, prop, general, profile)

    class Exploding:
        def charge(self, **kwargs):
            raise TimeoutError("read timed out")

    from apps.payments.services import payments as payments_svc

    monkeypatch.setattr(payments_svc, "get_payment_adapter", lambda: Exploding())
    payment = payments_svc.charge_for_booking(booking)

    stored = Payment.objects.get(booking=booking)
    assert stored.pk == payment.pk
    assert stored.status == PaymentStatus.PENDING
    assert "TimeoutError" in stored.failure_reason


@pytest.mark.django_db
def test_repair_task_finishes_missing_side_effects(customer, prop, general):
    _, profile = make_contractor("+61400070151")
    no_job = make_confirmed(customer, prop, general, profile)
    no_payment = make_confirmed(customer, prop, general, profile)
    Job.objects.create(booking=no_payment, status=JobStatus.ASSIGNED)
    unpaid_contractor = make_confirmed(customer, prop, general, profile)
    mark_paid(unpaid_contractor)
    Job.objects.create(booking=unpaid_contractor, status=JobStatus.COMPLETED,
                       confirmed_at=timezone.now() - timedelta(hours=1))
    mark_paid(no_job)
    Booking.objects.all().update(updated_at=timezone.now() - timedelta(hours=1))

    counts = repair_confirmed_bookings()

    assert counts == {"jobs": 1, "charges": 1, "payouts": 1}
    assert Job.objects.filter(booking=no_job).exists()
    assert Payment.objects.get(booking=no_payment).status == PaymentStatus.SUCCEEDED
    assert Payout.objects.filter(booking=unpaid_contractor).exists()
    # تشغيل ثانٍ لا يفعل شيئًا
    assert repair_confirmed_bookings() == {"jobs": 0, "charges": 0, "payouts": 0}


# ============================================================
# 6) الصور
# ============================================================
@pytest.fixture
def running_job(customer, prop, general):
    user, profile = make_contractor("+61400070161")
    booking = make_confirmed(customer, prop, general, profile)
    mark_paid(booking)
    job = jobs_svc.create_job_for_booking(booking)
    jobs_svc.start_job(job, user)
    return user, job


@pytest.mark.django_db
def test_non_image_upload_is_refused(running_job):
    user, job = running_job
    with pytest.raises(photos_svc.UnsupportedPhotoFormatError):
        photos_svc.upload_job_photo(user, job.id, "BEFORE", b"<svg onload=alert(1)>", "image/jpeg")


@pytest.mark.django_db
def test_detected_type_replaces_declared_type(running_job, monkeypatch):
    user, job = running_job
    seen = {}
    from apps.jobs.adapters import fake_adapter

    original = fake_adapter.FakeStorageAdapter.upload

    def spy(self, file_bytes, content_type, path_hint):
        seen["content_type"] = content_type
        return original(self, file_bytes, content_type, path_hint)

    monkeypatch.setattr(fake_adapter.FakeStorageAdapter, "upload", spy)
    photos_svc.upload_job_photo(user, job.id, "BEFORE", PNG_BYTES, "text/html")
    assert seen["content_type"] == "image/png"


@pytest.mark.django_db
def test_oversized_and_excess_photos_are_refused(running_job, settings):
    user, job = running_job
    settings.JOB_PHOTO_MAX_BYTES = 100
    with pytest.raises(photos_svc.PhotoTooLargeError):
        photos_svc.upload_job_photo(user, job.id, "BEFORE", JPEG_BYTES * 10, "image/jpeg")

    settings.JOB_PHOTO_MAX_BYTES = 10_000
    settings.JOB_PHOTO_MAX_PER_JOB = 2
    photos_svc.upload_job_photo(user, job.id, "BEFORE", JPEG_BYTES, "image/jpeg")
    photos_svc.upload_job_photo(user, job.id, "AFTER", JPEG_BYTES, "image/jpeg")
    with pytest.raises(photos_svc.TooManyPhotosError):
        photos_svc.upload_job_photo(user, job.id, "AFTER", JPEG_BYTES, "image/jpeg")


# ============================================================
# 7) العقار — لا نقل لعنوان مرتبط بمقاول
# ============================================================
@pytest.mark.django_db
def test_address_locked_while_a_contractor_is_tied_to_it(customer, prop, general):
    make_contractor("+61400070171", coords=NEAR)
    booking = make_pending_booking(customer, prop, general)
    assign_next_contractor(booking)  # عرض حيّ

    with pytest.raises(properties_svc.PropertyHasActiveBookingsError):
        properties_svc.update_address(customer, prop.id, street_address="99 Other St")
    with pytest.raises(properties_svc.PropertyHasActiveBookingsError):
        properties_svc.deactivate_property(customer, prop.id)


@pytest.mark.django_db
def test_address_editable_when_the_search_found_nobody(customer, prop, general):
    """إضافة إحداثيات لعقار لم يجد مقاولًا هي الإصلاح نفسه — لا تُمنع."""
    booking = make_pending_booking(customer, prop, general)
    Booking.objects.filter(pk=booking.pk).update(dispatch_status=DispatchStatus.NO_CONTRACTOR)
    properties_svc.update_address(customer, prop.id, street_address="99 Other St")

    with pytest.raises(properties_svc.PropertyHasActiveBookingsError):
        properties_svc.update_address(customer, prop.id, state="VIC", postcode="3000")


@pytest.mark.django_db
def test_address_editable_after_the_job_completed(customer, prop, general):
    _, profile = make_contractor("+61400070181")
    booking = make_confirmed(customer, prop, general, profile)
    Job.objects.create(booking=booking, status=JobStatus.COMPLETED)
    properties_svc.update_address(customer, prop.id, street_address="99 Other St")
    properties_svc.deactivate_property(customer, prop.id)


@pytest.mark.django_db
def test_null_clears_optional_coordinates(client, customer, prop):
    r = client.patch(
        f"/api/properties/{prop.id}",
        data={"address": {"latitude": None, "longitude": None}},
        content_type="application/json",
        **auth(customer),
    )
    assert r.status_code == 200, r.content
    prop.address.refresh_from_db()
    assert prop.address.latitude is None


def test_postcode_edge_cases_are_accepted():
    assert postcode_matches_state("2620", "ACT")   # Hume / Tharwa
    assert postcode_matches_state("2620", "NSW")   # Queanbeyan
    assert postcode_matches_state("6798", "WA")    # Christmas Island
    assert postcode_matches_state("6799", "WA")    # Cocos (Keeling) Islands


# ============================================================
# 8) الدعم
# ============================================================
@pytest.mark.django_db
def test_assigned_contractor_can_link_their_booking(customer, prop, general):
    user, profile = make_contractor("+61400070191")
    booking = make_confirmed(customer, prop, general, profile)
    request = support_svc.create_support_request(
        user, category="BOOKING_ISSUE", message="Customer not home", booking_id=booking.id
    )
    assert request.booking_id == booking.id

    stranger, _ = make_contractor("+61400070192")
    with pytest.raises(support_svc.InvalidBookingReferenceError):
        support_svc.create_support_request(
            stranger, category="BOOKING_ISSUE", message="x", booking_id=booking.id
        )


@pytest.mark.django_db
def test_resolved_request_cannot_be_reopened(customer):
    request = SupportRequest.objects.create(
        user=customer, category="OTHER", message="hi", status=SupportStatus.RESOLVED
    )
    request.status = SupportStatus.SUBMITTED
    with pytest.raises(ValidationError):
        request.full_clean()
