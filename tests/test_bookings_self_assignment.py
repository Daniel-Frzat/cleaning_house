"""
Self-assignment tests — Booking Domain

🔒 حساب واحد قد يكون عميلًا ومقاولًا معًا، فلا يجوز أن يُسنَد إليه حجزه
   هو. طبقتان:
     1) محرّك الترشيح يستبعده من قائمة المرشحين أصلًا.
     2) قبول العرض يرفض صراحةً — لعرض قديم أُنشئ قبل القاعدة، أو صف
        أُدخل يدويًا.

📌 هذا ليس سيناريو مستقبليًا: الثغرة كانت قائمة فعلًا قبل هذا الإصلاح —
   ملف المستخدم كان يظهر ضمن مرشحي حجزه.
"""

import datetime
import json
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.bookings.models import (
    Booking,
    BookingServiceSelection,
    BookingStatus,
    DispatchOffer,
    DispatchOfferStatus,
    OFFER_TTL_MINUTES,
)
from apps.bookings.services import offers as offers_svc
from apps.bookings.services.dispatch import assign_next_contractor, find_candidates
from apps.contractors.models import (
    AvailabilityStatus,
    BusinessRegistration,
    ContractorProfile,
    InsuranceDocument,
    VerificationStatus,
)
from apps.properties.models import Property, PropertyAddress, PropertyType
from apps.services.models import ServiceType


@pytest.fixture
def client():
    return Client()


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


def make_eligible_contractor(user, lat="-33.868800", lng="151.209300"):
    """ملف مقاول مؤهّل بالكامل: متاح + ABN معتمد + تأمين ساري."""
    profile = ContractorProfile.objects.create(
        user=user,
        business_name="Co",
        latitude=Decimal(lat),
        longitude=Decimal(lng),
        availability_status=AvailabilityStatus.AVAILABLE,
    )
    BusinessRegistration.objects.create(
        contractor=profile, abn="51824753556", business_name="Co",
        status=VerificationStatus.VERIFIED,
    )
    InsuranceDocument.objects.create(
        contractor=profile, document_reference="P1",
        expiry_date=datetime.date.today() + datetime.timedelta(days=300),
        status=VerificationStatus.VERIFIED,
    )
    return profile


def make_booking(customer, service):
    prop = Property.objects.create(
        owner=customer, label="H", property_type=PropertyType.HOUSE
    )
    PropertyAddress.objects.create(
        property=prop, street_address="1 St", suburb="Sydney", state="NSW",
        postcode="2000", latitude=Decimal("-33.870000"),
        longitude=Decimal("151.210000"),
    )
    booking = Booking.objects.create(
        customer=customer, property=prop, status=BookingStatus.PENDING
    )
    BookingServiceSelection.objects.create(
        booking=booking, service_type=service, room_count=2
    )
    return booking


@pytest.fixture
def service(db):
    return ServiceType.objects.create(
        name="General Cleaning",
        room_price=Decimal("45.00"),
        base_price=Decimal("80.00"),
    )


@pytest.fixture
def dual_user(db):
    """
    الحساب محلّ الاختبار: عميل يملك حجزًا، وله ملف مقاول مؤهّل.

    ⚠️ الدور CONTRACTOR لأن النموذج الحالي يقبل دورًا واحدًا — الحجز
       يُنشأ مباشرةً على مستوى الـORM لا عبر الـAPI. الاختبار يقيس منع
       الإسناد الذاتي، لا بوابة الدور.
    """
    return User.objects.create_user(
        phone="+61400550001", role=ConfirmedRole.CONTRACTOR
    )


@pytest.fixture
def other_contractor(db):
    return User.objects.create_user(
        phone="+61400550002", role=ConfirmedRole.CONTRACTOR
    )


# ============================================================
# 1) محرّك الترشيح
# ============================================================
@pytest.mark.django_db
def test_own_profile_is_excluded_from_candidates(dual_user, service):
    """🔒 الثغرة المُصلَحة: الملف كان يظهر ضمن مرشحي حجز صاحبه."""
    make_eligible_contractor(dual_user)
    booking = make_booking(dual_user, service)

    candidates = find_candidates(booking)

    assert candidates == []


@pytest.mark.django_db
def test_other_contractors_are_still_candidates(dual_user, other_contractor, service):
    """⚠️ الاستبعاد دقيق: لا يُسقط المقاولين الآخرين."""
    make_eligible_contractor(dual_user)
    other = make_eligible_contractor(other_contractor)
    booking = make_booking(dual_user, service)

    candidates = find_candidates(booking)

    assert [p.id for p, _ in candidates] == [other.id]


@pytest.mark.django_db
def test_no_offer_is_created_for_the_owner(dual_user, service):
    make_eligible_contractor(dual_user)
    booking = make_booking(dual_user, service)

    offer = assign_next_contractor(booking)

    assert offer is None
    assert DispatchOffer.objects.filter(booking=booking).count() == 0


@pytest.mark.django_db
def test_booking_stays_pending_when_the_only_contractor_is_the_owner(
    dual_user, service
):
    """
    ⚠️ ليس خطأ: الحجز يبقى PENDING بلا عرض — نفس سلوك "لا مقاول مؤهّل"
       (القرار المفتوح #16).
    """
    make_eligible_contractor(dual_user)
    booking = make_booking(dual_user, service)

    assign_next_contractor(booking)

    booking.refresh_from_db()
    assert booking.status == BookingStatus.PENDING
    assert booking.assigned_contractor_id is None


@pytest.mark.django_db
def test_offer_goes_to_the_other_contractor_not_the_owner(
    dual_user, other_contractor, service
):
    make_eligible_contractor(dual_user)
    other = make_eligible_contractor(other_contractor)
    booking = make_booking(dual_user, service)

    offer = assign_next_contractor(booking)

    assert offer is not None
    assert offer.contractor_id == other.id


# ============================================================
# 2) القبول — الطبقة الثانية
# ============================================================
def _force_offer(booking, profile):
    """
    ينشئ عرضًا يدويًا يتجاوز محرّك الترشيح.

    يحاكي عرضًا أُنشئ قبل تطبيق القاعدة، أو صفًّا أُدخل مباشرةً — وهو
    بالضبط ما تحرسه الطبقة الثانية.
    """
    return DispatchOffer.objects.create(
        booking=booking,
        contractor=profile,
        status=DispatchOfferStatus.PENDING,
        distance_km=Decimal("1.000"),
        expires_at=timezone.now() + datetime.timedelta(minutes=OFFER_TTL_MINUTES),
    )


@pytest.mark.django_db
def test_accepting_own_booking_is_refused_at_the_service_layer(dual_user, service):
    profile = make_eligible_contractor(dual_user)
    booking = make_booking(dual_user, service)
    offer = _force_offer(booking, profile)

    with pytest.raises(offers_svc.SelfAssignmentError):
        offers_svc.accept_offer(dual_user, offer.id)


@pytest.mark.django_db
def test_refused_acceptance_leaves_everything_untouched(dual_user, service):
    """🔒 لا سعر يُثبَّت ولا حالة تتغيّر ولا عرض يُستهلك."""
    profile = make_eligible_contractor(dual_user)
    booking = make_booking(dual_user, service)
    offer = _force_offer(booking, profile)

    with pytest.raises(offers_svc.SelfAssignmentError):
        offers_svc.accept_offer(dual_user, offer.id)

    booking.refresh_from_db()
    offer.refresh_from_db()
    assert booking.status == BookingStatus.PENDING
    assert booking.computed_price is None
    assert booking.assigned_contractor_id is None
    assert offer.status == DispatchOfferStatus.PENDING
    assert offer.responded_at is None


@pytest.mark.django_db
def test_api_returns_403_with_a_named_code(client, dual_user, service):
    profile = make_eligible_contractor(dual_user)
    booking = make_booking(dual_user, service)
    offer = _force_offer(booking, profile)

    r = client.post(
        f"/api/contractor/offers/{offer.id}/accept", **auth(dual_user)
    )

    assert r.status_code == 403, r.content
    assert r.json()["code"] == "self_assignment_forbidden"


@pytest.mark.django_db
def test_another_contractor_can_still_accept_normally(
    client, dual_user, other_contractor, service
):
    """⚠️ الحارس لا يعطّل المسار السليم."""
    other = make_eligible_contractor(other_contractor)
    booking = make_booking(dual_user, service)
    offer = _force_offer(booking, other)

    r = client.post(
        f"/api/contractor/offers/{offer.id}/accept", **auth(other_contractor)
    )

    assert r.status_code == 200, r.content
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CONFIRMED
    assert booking.assigned_contractor_id == other.id


@pytest.mark.django_db
def test_declining_your_own_booking_is_also_refused(client, dual_user, service):
    """
    الرفض ممنوع كذلك: السماح به كان سيتيح لصاحب الحجز تحريك تتابع
    الإسناد من موقع لا يحق له أصلًا.
    """
    profile = make_eligible_contractor(dual_user)
    booking = make_booking(dual_user, service)
    offer = _force_offer(booking, profile)

    r = client.post(
        f"/api/contractor/offers/{offer.id}/decline", **auth(dual_user)
    )

    assert r.status_code == 403, r.content
    assert r.json()["code"] == "self_assignment_forbidden"
