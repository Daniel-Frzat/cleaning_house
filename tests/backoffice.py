"""
مصانع مشتركة لاختبارات مسارات الـBack-office (tests/test_backoffice_*.py).

📌 دوال عادية لا fixtures: كل ملف اختبار يبني ما يحتاجه صراحةً، فيبقى
   الاختبار مقروءًا وحده.
"""

import itertools
from datetime import timedelta
from decimal import Decimal

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
)
from apps.contractors.models import AvailabilityStatus, ContractorProfile
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.properties.models import Property, PropertyAddress, PropertyType
from apps.services.models import ServiceType

_seq = itertools.count(1)


def _phone():
    return f"+6149{next(_seq):07d}"


def make_user(role=ConfirmedRole.CUSTOMER, phone=None, **extra):
    return User.objects.create_user(phone=phone or _phone(), role=role, **extra)


def make_admin(**extra):
    return make_user(role=ConfirmedRole.ADMIN, **extra)


def make_superuser(**extra):
    user = make_admin(**extra)
    user.is_superuser = True
    user.save()
    return user


def make_contractor(available=True, **extra):
    user = make_user(role=ConfirmedRole.CONTRACTOR, **extra)
    profile = ContractorProfile.objects.create(
        user=user,
        business_name=f"Co {user.phone[-4:]}",
        availability_status=AvailabilityStatus.AVAILABLE
        if available
        else AvailabilityStatus.UNAVAILABLE,
    )
    return user, profile


def tokens(user):
    return issue_tokens_for_user(user)


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {tokens(user)['access']}"}


def make_service_type(name="General Cleaning"):
    return ServiceType.objects.create(
        name=name, room_price=Decimal("45.00"), base_price=Decimal("80.00")
    )


def make_property(owner, suburb="Parramatta", state="NSW", postcode="2150", street="1 Main St"):
    prop = Property.objects.create(owner=owner, label="Home", property_type=PropertyType.HOUSE)
    PropertyAddress.objects.create(
        property=prop,
        street_address=street,
        suburb=suburb,
        state=state,
        postcode=postcode,
        latitude=Decimal("-33.815000"),
        longitude=Decimal("151.001000"),
    )
    return prop


def make_booking(
    customer,
    prop=None,
    service=None,
    status=BookingStatus.PENDING,
    price=None,
    contractor=None,
    **extra,
):
    prop = prop or make_property(customer)
    booking = Booking.objects.create(
        customer=customer,
        property=prop,
        status=status,
        computed_price=price,
        assigned_contractor=contractor,
        **extra,
    )
    if service is not None:
        BookingServiceSelection.objects.create(booking=booking, service_type=service, room_count=3)
    return booking


def make_offer(booking, profile, status=DispatchOfferStatus.PENDING, price=Decimal("215.00"), rnd=1):
    return DispatchOffer.objects.create(
        booking=booking,
        contractor=profile,
        status=status,
        dispatch_round=rnd,
        distance_km=Decimal("1.112"),
        total_amount=price,
        contractor_earnings=price,
        services_total=price,
        travel_fee=Decimal("0.00"),
        expires_at=timezone.now() + timedelta(minutes=60),
    )


def make_unknown_outcome_payment(customer, profile, price=Decimal("215.00")):
    """
    حجز محجوز بعرض مقبول (ACCEPTED_PENDING_PAYMENT) ودفعة PROCESSING رمى
    مزوّدها استثناءً — الحالة التي تحتاج مطابقة.
    """
    booking = make_booking(customer)
    offer = make_offer(booking, profile, status=DispatchOfferStatus.ACCEPTED_PENDING_PAYMENT, price=price)
    payment = Payment.objects.create(
        booking=booking,
        amount=price,
        method=PaymentMethod.CARD,
        status=PaymentStatus.PROCESSING,
        failure_reason="provider_error: TimeoutError",
    )
    return booking, offer, payment
