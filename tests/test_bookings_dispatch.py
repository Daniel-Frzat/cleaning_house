"""
Dispatch & Offers Tests — Booking Domain (Change Set §36.1، §36.5، §36.6)

يغطي: المسار السعيد كاملًا، التتابع عند الرفض، التتابع المطابق عند انتهاء
المهلة عبر مهمة Celery، إنفاذ الملكية على العروض، رفض العروض غير القابلة
للرد، غياب المرشَّحين، استبعاد غير المؤهَّل، واستبعاد مجهول الإحداثيات.

⚠️ ملاحظة تقنية: create_booking يُطلق الإسناد عبر transaction.on_commit،
   وهو لا يعمل داخل معاملة الاختبار الافتراضية. لذلك نستخدم
   django_capture_on_commit_callbacks في كل اختبار يُنشئ حجزًا عبر الخدمة.
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
    BookingStatus,
    DispatchOffer,
    DispatchOfferStatus,
    OFFER_TTL_MINUTES,
)
from apps.bookings.services import bookings as booking_svc
from apps.bookings.services import offers as offers_svc
from apps.bookings.services.dispatch import assign_next_contractor, find_candidates
from apps.bookings.services.distance import distance_between, haversine_km
from apps.bookings.tasks import expire_pending_offers
from apps.contractors.models import (
    AvailabilityStatus,
    BusinessRegistration,
    ContractorProfile,
    InsuranceDocument,
    VerificationStatus,
)
from apps.properties.models import Property, PropertyAddress, PropertyType
from apps.services.models import PricingConfig, ServiceType

# إحداثيات مرجعية (سيدني ومحيطها)
SYDNEY = (Decimal("-33.868800"), Decimal("151.209300"))
NEAR = (Decimal("-33.878800"), Decimal("151.209300"))    # ~1.1 km
MID = (Decimal("-33.918800"), Decimal("151.209300"))     # ~5.6 km
FAR = (Decimal("-34.068800"), Decimal("151.209300"))     # ~22 km


# ------------------------------------------------------------
# أدوات
# ------------------------------------------------------------
@pytest.fixture
def client():
    return Client()


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
        latitude=coords[0] if coords else None,
        longitude=coords[1] if coords else None,
    )
    return prop


def make_contractor(phone, coords=NEAR, available=True, eligible=True):
    """ينشئ مستخدم مقاول + ملفًا + (اختياريًا) مستندات تحقق معتمدة."""
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
    if eligible:
        admin = User.objects.filter(role=ConfirmedRole.ADMIN).first() or make_user(
            "+61499999999", role=ConfirmedRole.ADMIN
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


@pytest.fixture
def customer(db):
    return make_user("+61400007001")


@pytest.fixture
def prop(customer):
    return make_property(customer)


@pytest.fixture
def general(db):
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


def create_booking(customer, prop, general, rooms=3, capture=None):
    """
    ينشئ حجزًا عبر طبقة الخدمة مع تشغيل on_commit callbacks.

    capture: fixture django_capture_on_commit_callbacks
    """
    if capture is None:
        return booking_svc.create_booking(
            customer,
            property_id=prop.id,
            service_selections=[{"service_type_id": general.id, "room_count": rooms}],
        )

    with capture(execute=True):
        booking = booking_svc.create_booking(
            customer,
            property_id=prop.id,
            service_selections=[{"service_type_id": general.id, "room_count": rooms}],
        )
    return booking


# ============================================================
# 1) المسار السعيد الكامل
# ============================================================
@pytest.mark.django_db
def test_booking_creation_auto_creates_offer_to_nearest(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    _, near = make_contractor("+61400007101", coords=NEAR)
    _, far = make_contractor("+61400007102", coords=FAR)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    offers = DispatchOffer.objects.filter(booking=booking)
    assert offers.count() == 1
    offer = offers.first()
    assert offer.contractor_id == near.id, "nearest contractor must be offered first"
    assert offer.status == DispatchOfferStatus.PENDING
    assert offer.distance_km is not None


@pytest.mark.django_db
def test_offer_expires_in_60_minutes(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    make_contractor("+61400007103", coords=NEAR)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)

    window = offer.expires_at - offer.offered_at
    assert abs(window - timedelta(minutes=OFFER_TTL_MINUTES)) < timedelta(seconds=5)


@pytest.mark.django_db
def test_accept_computes_price_matching_formula(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """
    📌 المسار السعيد: القبول يحسب السعر ويثبّته ويؤكّد الحجز.
    """
    contractor_user, profile = make_contractor("+61400007104", coords=NEAR)

    booking = create_booking(
        customer, prop, general, rooms=3, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)

    r = post(
        client, f"/api/contractor/offers/{offer.id}/accept", **auth(contractor_user)
    )
    assert r.status_code == 200, r.content

    booking.refresh_from_db()
    offer.refresh_from_db()

    # الصيغة: (45 × 3) + 80 + (distance × 2.00)
    expected = (Decimal("45") * 3 + Decimal("80")) + offer.distance_km * Decimal("2.00")
    expected = expected.quantize(Decimal("0.01"))

    assert booking.computed_price == expected
    assert booking.status == BookingStatus.CONFIRMED
    assert booking.assigned_contractor_id == profile.id
    assert offer.status == DispatchOfferStatus.ACCEPTED
    assert offer.responded_at is not None


@pytest.mark.django_db
def test_price_uses_offer_stored_distance_not_recomputed(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """
    ⚠️ لو غيّر المقاول إحداثياته بعد العرض، يبقى التسعير على مسافة العرض.
    """
    contractor_user, profile = make_contractor("+61400007105", coords=NEAR)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)
    original_distance = offer.distance_km

    # المقاول ينتقل بعيدًا جدًا قبل أن يقبل
    profile.latitude, profile.longitude = FAR
    profile.save()

    post(client, f"/api/contractor/offers/{offer.id}/accept", **auth(contractor_user))

    booking.refresh_from_db()
    expected = (Decimal("45") * 3 + Decimal("80")) + original_distance * Decimal("2.00")
    assert booking.computed_price == expected.quantize(Decimal("0.01"))


# ============================================================
# 2) توقيت كشف السعر للعميل (§36.1)
# ============================================================
@pytest.mark.django_db
def test_price_hidden_while_pending_revealed_after_confirm(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """🔒 الفحص الشامل من طرف إلى طرف لقاعدة توقيت الكشف."""
    contractor_user, _ = make_contractor("+61400007106", coords=NEAR)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    # قبل القبول: PENDING وبلا سعر
    before = client.get(f"/api/bookings/{booking.id}", **auth(customer)).json()
    assert before["status"] == BookingStatus.PENDING
    assert before["computed_price"] is None
    assert before["assigned_contractor_id"] is None

    offer = DispatchOffer.objects.get(booking=booking)
    post(client, f"/api/contractor/offers/{offer.id}/accept", **auth(contractor_user))

    # بعد القبول: CONFIRMED ومعه السعر
    after = client.get(f"/api/bookings/{booking.id}", **auth(customer)).json()
    assert after["status"] == BookingStatus.CONFIRMED
    assert after["computed_price"] is not None
    assert Decimal(after["computed_price"]) > 0
    assert after["assigned_contractor_id"] is not None


@pytest.mark.django_db
def test_price_stays_hidden_if_written_without_confirm(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """
    🔒 الشرط على الحالة لا على وجود قيمة: لقطة مكتوبة دون CONFIRMED تبقى محجوبة.
    """
    make_contractor("+61400007107", coords=NEAR)
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    booking.computed_price = Decimal("999.99")
    booking.save(update_fields=["computed_price"])

    body = client.get(f"/api/bookings/{booking.id}", **auth(customer)).json()

    assert body["status"] == BookingStatus.PENDING
    assert body["computed_price"] is None
    assert "999.99" not in json.dumps(body)


# ============================================================
# 3) الرفض يُطلق التتابع إلى التالي
# ============================================================
@pytest.mark.django_db
def test_decline_cascades_to_next_nearest(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    near_user, near = make_contractor("+61400007201", coords=NEAR)
    mid_user, mid = make_contractor("+61400007202", coords=MID)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    first = DispatchOffer.objects.get(booking=booking)
    assert first.contractor_id == near.id

    r = post(client, f"/api/contractor/offers/{first.id}/decline", **auth(near_user))
    assert r.status_code == 200, r.content

    first.refresh_from_db()
    assert first.status == DispatchOfferStatus.DECLINED

    # عرض جديد للمقاول التالي — وليس نفس الأول
    offers = DispatchOffer.objects.filter(booking=booking).order_by("offered_at")
    assert offers.count() == 2
    second = offers.last()
    assert second.contractor_id == mid.id
    assert second.status == DispatchOfferStatus.PENDING


@pytest.mark.django_db
def test_declined_contractor_is_never_re_offered(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """§36.5: لا يُعاد عرض الحجز نفسه على من رفضه."""
    near_user, near = make_contractor("+61400007203", coords=NEAR)
    make_contractor("+61400007204", coords=MID)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    first = DispatchOffer.objects.get(booking=booking)
    post(client, f"/api/contractor/offers/{first.id}/decline", **auth(near_user))

    contractor_ids = list(
        DispatchOffer.objects.filter(booking=booking).values_list(
            "contractor_id", flat=True
        )
    )
    assert len(contractor_ids) == len(set(contractor_ids)), "a contractor was re-offered"
    assert contractor_ids.count(near.id) == 1


@pytest.mark.django_db
def test_all_declined_leaves_booking_pending_with_no_active_offer(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """
    ⚠️ القرار المفتوح #16: لا سلوك بديل — الحجز يبقى PENDING بلا عرض نشط.
    """
    u1, _ = make_contractor("+61400007205", coords=NEAR)
    u2, _ = make_contractor("+61400007206", coords=MID)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    for user in (u1, u2):
        pending = DispatchOffer.objects.filter(
            booking=booking, status=DispatchOfferStatus.PENDING
        ).first()
        assert pending is not None
        post(client, f"/api/contractor/offers/{pending.id}/decline", **auth(user))

    booking.refresh_from_db()
    assert booking.status == BookingStatus.PENDING
    assert booking.computed_price is None
    assert booking.assigned_contractor_id is None
    assert not DispatchOffer.objects.filter(
        booking=booking, status=DispatchOfferStatus.PENDING
    ).exists()


# ============================================================
# 4) انتهاء المهلة عبر مهمة Celery — سلوك مطابق للرفض
# ============================================================
@pytest.mark.django_db
def test_expiry_task_cascades_like_decline(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    _, near = make_contractor("+61400007301", coords=NEAR)
    _, mid = make_contractor("+61400007302", coords=MID)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    first = DispatchOffer.objects.get(booking=booking)

    # محاكاة انقضاء المهلة
    first.expires_at = timezone.now() - timedelta(minutes=1)
    first.save(update_fields=["expires_at"])

    result = expire_pending_offers()

    first.refresh_from_db()
    assert first.status == DispatchOfferStatus.EXPIRED
    assert result["expired"] == 1
    assert result["created"] == 1

    offers = DispatchOffer.objects.filter(booking=booking).order_by("offered_at")
    assert offers.count() == 2
    assert offers.last().contractor_id == mid.id
    assert offers.last().status == DispatchOfferStatus.PENDING


@pytest.mark.django_db
def test_expiry_task_is_idempotent(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """
    🔒 Infra §15: تشغيلها مرتين لا يُنتج تتابعًا مكرَّرًا.
    """
    make_contractor("+61400007303", coords=NEAR)
    make_contractor("+61400007304", coords=MID)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    first = DispatchOffer.objects.get(booking=booking)
    first.expires_at = timezone.now() - timedelta(minutes=1)
    first.save(update_fields=["expires_at"])

    run1 = expire_pending_offers()
    count_after_first = DispatchOffer.objects.filter(booking=booking).count()

    run2 = expire_pending_offers()
    count_after_second = DispatchOffer.objects.filter(booking=booking).count()

    assert run1 == {"expired": 1, "created": 1}
    assert run2 == {"expired": 0, "created": 0}, "second run must be a no-op"
    assert count_after_first == count_after_second == 2


@pytest.mark.django_db
def test_expiry_task_ignores_unexpired_offers(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    make_contractor("+61400007305", coords=NEAR)
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    result = expire_pending_offers()

    assert result == {"expired": 0, "created": 0}
    assert DispatchOffer.objects.get(booking=booking).status == DispatchOfferStatus.PENDING


@pytest.mark.django_db
def test_expiry_task_does_not_touch_answered_offers(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """العرض المقبول لا يصبح EXPIRED حتى لو تجاوز وقته."""
    contractor_user, _ = make_contractor("+61400007306", coords=NEAR)
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)
    post(client, f"/api/contractor/offers/{offer.id}/accept", **auth(contractor_user))

    DispatchOffer.objects.filter(pk=offer.pk).update(
        expires_at=timezone.now() - timedelta(hours=2)
    )
    expire_pending_offers()

    offer.refresh_from_db()
    assert offer.status == DispatchOfferStatus.ACCEPTED


# ============================================================
# 5) الملكية على العروض — 403
# ============================================================
@pytest.mark.django_db
def test_other_contractor_cannot_accept_or_decline(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    make_contractor("+61400007401", coords=NEAR)
    intruder_user, _ = make_contractor("+61400007402", coords=MID)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)

    accept = post(
        client, f"/api/contractor/offers/{offer.id}/accept", **auth(intruder_user)
    )
    decline = post(
        client, f"/api/contractor/offers/{offer.id}/decline", **auth(intruder_user)
    )

    assert accept.status_code == 403, accept.content
    assert decline.status_code == 403, decline.content

    offer.refresh_from_db()
    booking.refresh_from_db()
    assert offer.status == DispatchOfferStatus.PENDING
    assert booking.status == BookingStatus.PENDING


@pytest.mark.django_db
@pytest.mark.parametrize("role", [ConfirmedRole.CUSTOMER, ConfirmedRole.ADMIN])
def test_non_contractor_cannot_respond(
    client, customer, prop, general, pricing, role, django_capture_on_commit_callbacks
):
    make_contractor("+61400007403", coords=NEAR)
    other = make_user("+61400007404", role=role)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)

    r = post(client, f"/api/contractor/offers/{offer.id}/accept", **auth(other))

    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_unauthenticated_offer_response_returns_401(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    make_contractor("+61400007405", coords=NEAR)
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)

    assert post(client, f"/api/contractor/offers/{offer.id}/accept").status_code == 401
    assert post(client, f"/api/contractor/offers/{offer.id}/decline").status_code == 401


# ============================================================
# 6) العروض غير القابلة للرد — 409
# ============================================================
@pytest.mark.django_db
def test_accepting_expired_offer_is_rejected(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    contractor_user, _ = make_contractor("+61400007501", coords=NEAR)
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)

    DispatchOffer.objects.filter(pk=offer.pk).update(
        status=DispatchOfferStatus.EXPIRED, expires_at=timezone.now() - timedelta(minutes=1)
    )

    r = post(
        client, f"/api/contractor/offers/{offer.id}/accept", **auth(contractor_user)
    )

    assert r.status_code == 409, r.content
    assert r.json()["code"] == "offer_not_actionable"
    booking.refresh_from_db()
    assert booking.status == BookingStatus.PENDING
    assert booking.computed_price is None


@pytest.mark.django_db
def test_accepting_time_expired_offer_rejected_before_task_runs(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """
    🔒 الوقت هو الحَكَم لا آخر تشغيل للمهمة: عرض تجاوز وقته يُرفض حتى لو
       بقيت حالته PENDING لأن المهمة لم تعمل بعد.
    """
    contractor_user, _ = make_contractor("+61400007502", coords=NEAR)
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)

    DispatchOffer.objects.filter(pk=offer.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    r = post(
        client, f"/api/contractor/offers/{offer.id}/accept", **auth(contractor_user)
    )

    assert r.status_code == 409, r.content
    offer.refresh_from_db()
    assert offer.status == DispatchOfferStatus.PENDING  # المهمة وحدها تغيّر الحالة


@pytest.mark.django_db
def test_double_accept_is_rejected(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    contractor_user, _ = make_contractor("+61400007503", coords=NEAR)
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)

    first = post(
        client, f"/api/contractor/offers/{offer.id}/accept", **auth(contractor_user)
    )
    second = post(
        client, f"/api/contractor/offers/{offer.id}/accept", **auth(contractor_user)
    )

    assert first.status_code == 200
    assert second.status_code == 409, second.content


@pytest.mark.django_db
def test_declining_after_accepting_is_rejected(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    contractor_user, _ = make_contractor("+61400007504", coords=NEAR)
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)

    post(client, f"/api/contractor/offers/{offer.id}/accept", **auth(contractor_user))
    r = post(
        client, f"/api/contractor/offers/{offer.id}/decline", **auth(contractor_user)
    )

    assert r.status_code == 409, r.content
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CONFIRMED


# ============================================================
# 7) لا مرشَّح — القرار المفتوح #16
# ============================================================
@pytest.mark.django_db
def test_no_contractor_at_all_leaves_booking_pending(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    assert DispatchOffer.objects.filter(booking=booking).count() == 0
    booking.refresh_from_db()
    assert booking.status == BookingStatus.PENDING
    assert booking.computed_price is None


@pytest.mark.django_db
def test_assign_next_contractor_returns_none_when_no_candidates(db, customer, prop, general):
    booking = Booking.objects.create(customer=customer, property=prop)

    assert assign_next_contractor(booking) is None
    assert DispatchOffer.objects.count() == 0


@pytest.mark.django_db
def test_unavailable_contractor_never_selected(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    make_contractor("+61400007601", coords=NEAR, available=False)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    assert DispatchOffer.objects.filter(booking=booking).count() == 0


# ============================================================
# 8) استبعاد غير المؤهَّل ومجهول الإحداثيات
# ============================================================
@pytest.mark.django_db
def test_ineligible_contractor_never_selected_even_if_nearest(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """⚠️ الأقرب لكنه غير مؤهَّل (بلا مستندات) — يُتجاوَز إلى المؤهَّل الأبعد."""
    _, nearest_ineligible = make_contractor("+61400007701", coords=NEAR, eligible=False)
    _, farther_eligible = make_contractor("+61400007702", coords=MID, eligible=True)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    offer = DispatchOffer.objects.get(booking=booking)
    assert offer.contractor_id == farther_eligible.id
    assert offer.contractor_id != nearest_ineligible.id


@pytest.mark.django_db
def test_expired_insurance_makes_contractor_ineligible_for_dispatch(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    _, profile = make_contractor("+61400007703", coords=NEAR, eligible=True)
    InsuranceDocument.objects.filter(contractor=profile).update(
        expiry_date=timezone.localdate() - timedelta(days=1)
    )

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    assert DispatchOffer.objects.filter(booking=booking).count() == 0


@pytest.mark.django_db
def test_contractor_with_null_coordinates_never_selected(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """⚠️ مسافة مجهولة = استبعاد، لا افتراض صفر ولا قيمة افتراضية."""
    make_contractor("+61400007801", coords=None, eligible=True)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    assert DispatchOffer.objects.filter(booking=booking).count() == 0


@pytest.mark.django_db
def test_property_without_coordinates_yields_no_offer(
    customer, general, pricing, django_capture_on_commit_callbacks
):
    prop_no_coords = make_property(customer, coords=None)
    make_contractor("+61400007802", coords=NEAR)

    booking = create_booking(
        customer, prop_no_coords, general, capture=django_capture_on_commit_callbacks
    )

    assert DispatchOffer.objects.filter(booking=booking).count() == 0


@pytest.mark.django_db
def test_null_coordinate_contractor_skipped_but_others_still_offered(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """المجهول يُتجاوَز دون أن يُعطّل الإسناد للبقية."""
    make_contractor("+61400007803", coords=None)
    _, with_coords = make_contractor("+61400007804", coords=MID)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    offer = DispatchOffer.objects.get(booking=booking)
    assert offer.contractor_id == with_coords.id


# ============================================================
# 9) المسافة — رياضيات محلية لا adapter
# ============================================================
@pytest.mark.django_db
def test_distance_unknown_returns_none_not_zero(db, customer):
    prop_no_coords = make_property(customer, coords=None)
    _, profile = make_contractor("+61400007901", coords=NEAR, eligible=False)

    assert distance_between(prop_no_coords, profile) is None


def test_haversine_known_distance():
    """سيدني → ملبورن ≈ 713 km (مرجع معروف)."""
    sydney = (Decimal("-33.8688"), Decimal("151.2093"))
    melbourne = (Decimal("-37.8136"), Decimal("144.9631"))

    km = haversine_km(sydney[0], sydney[1], melbourne[0], melbourne[1])

    assert Decimal("700") < km < Decimal("725"), km


def test_haversine_same_point_is_zero():
    assert haversine_km(Decimal("-33.8688"), Decimal("151.2093"),
                        Decimal("-33.8688"), Decimal("151.2093")) == Decimal("0.000")


def test_distance_module_calls_no_adapter():
    """
    ⚠️ رياضيات محلية فقط — لا gps_distance adapter ولا Routing Engine
       (Infra §8). الفحص على الاستيرادات الفعلية (AST) لا على النص.
    """
    import ast
    import inspect

    from apps.bookings.services import distance, dispatch

    for mod in (distance, dispatch):
        tree = ast.parse(inspect.getsource(mod))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)

        for name in imported:
            low = name.lower()
            assert "adapter" not in low, f"{mod.__name__} imports adapter: {name}"
            assert "requests" not in low, f"{mod.__name__} imports HTTP client: {name}"
            assert "httpx" not in low, f"{mod.__name__} imports HTTP client: {name}"


# ============================================================
# 10) الطبقات
# ============================================================
def test_offer_api_layer_does_not_touch_orm():
    import inspect

    from apps.bookings.api import offers as api_mod

    src = inspect.getsource(api_mod)
    assert ".objects." not in src
    assert "DispatchOffer(" not in src


def test_find_candidates_excludes_already_offered(
    db, customer, prop, general, pricing
):
    """الدالة نفسها تستبعد من عُرض عليه — لا يعتمد الأمر على الـAPI."""
    _, near = make_contractor("+61400008001", coords=NEAR)
    _, mid = make_contractor("+61400008002", coords=MID)

    booking = Booking.objects.create(customer=customer, property=prop)

    first = assign_next_contractor(booking)
    assert first.contractor_id == near.id

    remaining = [p.id for p, _ in find_candidates(booking)]
    assert near.id not in remaining
    assert mid.id in remaining
