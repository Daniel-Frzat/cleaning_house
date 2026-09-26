"""
Back-office — الحجوزات (ADMIN فقط، قراءة).

    GET /api/admin/bookings
    GET /api/admin/bookings/{booking_id}

⚠️ لا مسار إلغاء ولا استرداد ولا إعادة إسناد: قرارات مفتوحة (#12، #16).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from ninja import Query, Router, Schema
from pydantic import Field

from apps.accounts.authentication import AdminJWTAuth
from apps.audit.api.common import CreatedRangeQuery, ErrorOut, error, forbidden, page
from apps.audit.services.backoffice import AdminRequiredError
from apps.properties.api.admin import AdminAddressOut, serialize_address

from ..models import BookingStatus, DispatchStatus
from ..services import admin as svc

router = Router(tags=["Admin — Bookings"], auth=AdminJWTAuth())


class BookingFilters(CreatedRangeQuery):
    status: Optional[BookingStatus] = None
    dispatch_status: Optional[DispatchStatus] = None
    customer_id: Optional[uuid.UUID] = None
    # معرّف ملف المقاول (ContractorProfile.id)
    contractor_id: Optional[uuid.UUID] = None
    scheduled_from: Optional[date] = None
    scheduled_to: Optional[date] = None
    q: Optional[str] = Field(None, max_length=32, description="public_reference contains")


class PersonSummaryOut(Schema):
    id: uuid.UUID
    phone: str
    email: Optional[str] = None
    full_name: str
    status: str


class ContractorSummaryOut(Schema):
    profile_id: uuid.UUID
    user_id: uuid.UUID
    business_name: str


class AdminBookingOut(Schema):
    id: uuid.UUID
    public_reference: str
    status: str
    dispatch_status: str
    customer_id: uuid.UUID
    customer_phone: str
    property_id: uuid.UUID
    suburb: Optional[str] = None
    state: Optional[str] = None
    assigned_contractor: Optional[ContractorSummaryOut] = None
    max_total: Optional[Decimal] = None
    computed_price: Optional[Decimal] = None
    currency: str
    scheduled_at: Optional[datetime] = None
    requested_at: datetime
    created_at: datetime
    updated_at: datetime


class AdminBookingListOut(Schema):
    count: int
    items: list[AdminBookingOut]


class PropertySummaryOut(Schema):
    id: uuid.UUID
    label: str
    property_type: str
    is_active: bool
    address: Optional[AdminAddressOut] = None


class ServiceLineOut(Schema):
    service_type_id: uuid.UUID
    service_name: str
    room_count: int


class QuoteSummaryOut(Schema):
    id: uuid.UUID
    services_total: Decimal
    maximum_total: Decimal
    currency: str
    pricing_version: int
    service_snapshot: Any = None
    expires_at: datetime
    created_at: datetime


class DispatchOfferOut(Schema):
    id: uuid.UUID
    dispatch_round: int
    contractor: ContractorSummaryOut
    status: str
    distance_km: Optional[Decimal] = None
    distance_source: str
    eta_seconds: Optional[int] = None
    total_amount: Optional[Decimal] = None
    contractor_earnings: Optional[Decimal] = None
    travel_fee: Optional[Decimal] = None
    services_total: Optional[Decimal] = None
    currency: str
    pricing_version: Optional[int] = None
    offered_at: datetime
    responded_at: Optional[datetime] = None
    expires_at: datetime


class PaymentSummaryOut(Schema):
    id: uuid.UUID
    status: str
    amount: Decimal
    method: str
    attempt_number: int
    provider_reference: Optional[str] = None
    provider_error_code: str
    failure_reason: Optional[str] = None
    paid_at: Optional[datetime] = None
    created_at: datetime


class JobSummaryOut(Schema):
    id: uuid.UUID
    status: str
    started_at: Optional[datetime] = None
    marked_done_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    created_at: datetime


class PayoutSummaryOut(Schema):
    id: uuid.UUID
    status: str
    amount: Decimal
    contractor_user_id: uuid.UUID
    provider_reference: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: datetime


class AdminBookingDetailOut(Schema):
    id: uuid.UUID
    public_reference: str
    status: str
    dispatch_status: str
    dispatch_round: int
    last_dispatch_attempt_at: Optional[datetime] = None
    customer: PersonSummaryOut
    property: PropertySummaryOut
    service_lines: list[ServiceLineOut]
    quote: Optional[QuoteSummaryOut] = None
    max_total: Optional[Decimal] = None
    computed_price: Optional[Decimal] = None
    pricing_version: Optional[int] = None
    currency: str
    access_notes: str
    scheduled_at: Optional[datetime] = None
    customer_timezone: str
    requested_at: datetime
    assigned_contractor: Optional[ContractorSummaryOut] = None
    dispatch_offers: list[DispatchOfferOut]
    payment: Optional[PaymentSummaryOut] = None
    job: Optional[JobSummaryOut] = None
    payout: Optional[PayoutSummaryOut] = None
    created_at: datetime
    updated_at: datetime


def serialize_contractor(profile):
    if profile is None:
        return None
    return {
        "profile_id": profile.id,
        "user_id": profile.user_id,
        "business_name": profile.business_name,
    }


def _related(obj, name):
    """علاقة OneToOne عكسية قد لا تكون موجودة."""
    return getattr(obj, name, None)


def _serialize(booking):
    address = _related(booking.property, "address")
    return {
        "id": booking.id,
        "public_reference": booking.public_reference,
        "status": booking.status,
        "dispatch_status": booking.dispatch_status,
        "customer_id": booking.customer_id,
        "customer_phone": booking.customer.phone,
        "property_id": booking.property_id,
        "suburb": address.suburb if address else None,
        "state": address.state if address else None,
        "assigned_contractor": serialize_contractor(booking.assigned_contractor),
        "max_total": booking.max_total,
        "computed_price": booking.computed_price,
        "currency": booking.currency,
        "scheduled_at": booking.scheduled_at,
        "requested_at": booking.requested_at,
        "created_at": booking.created_at,
        "updated_at": booking.updated_at,
    }


def _serialize_detail(booking):
    customer = booking.customer
    prop = booking.property
    quote = booking.quote
    payment = _related(booking, "payment")
    job = _related(booking, "job")
    payout = _related(booking, "payout")

    return {
        "id": booking.id,
        "public_reference": booking.public_reference,
        "status": booking.status,
        "dispatch_status": booking.dispatch_status,
        "dispatch_round": booking.dispatch_round,
        "last_dispatch_attempt_at": booking.last_dispatch_attempt_at,
        "customer": {
            "id": customer.id,
            "phone": customer.phone,
            "email": customer.email,
            "full_name": customer.full_name,
            "status": customer.status,
        },
        "property": {
            "id": prop.id,
            "label": prop.label,
            "property_type": prop.property_type,
            "is_active": prop.is_active,
            "address": serialize_address(_related(prop, "address")),
        },
        "service_lines": [
            {
                "service_type_id": line.service_type_id,
                "service_name": line.service_type.name,
                "room_count": line.room_count,
            }
            for line in booking.service_selections.all()
        ],
        "quote": None
        if quote is None
        else {
            "id": quote.id,
            "services_total": quote.services_total,
            "maximum_total": quote.maximum_total,
            "currency": quote.currency,
            "pricing_version": quote.pricing_version,
            "service_snapshot": quote.service_snapshot,
            "expires_at": quote.expires_at,
            "created_at": quote.created_at,
        },
        "max_total": booking.max_total,
        "computed_price": booking.computed_price,
        "pricing_version": booking.pricing_version,
        "currency": booking.currency,
        "access_notes": booking.access_notes,
        "scheduled_at": booking.scheduled_at,
        "customer_timezone": booking.customer_timezone,
        "requested_at": booking.requested_at,
        "assigned_contractor": serialize_contractor(booking.assigned_contractor),
        "dispatch_offers": [
            {
                "id": offer.id,
                "dispatch_round": offer.dispatch_round,
                "contractor": serialize_contractor(offer.contractor),
                "status": offer.status,
                "distance_km": offer.distance_km,
                "distance_source": offer.distance_source,
                "eta_seconds": offer.eta_seconds,
                "total_amount": offer.total_amount,
                "contractor_earnings": offer.contractor_earnings,
                "travel_fee": offer.travel_fee,
                "services_total": offer.services_total,
                "currency": offer.currency,
                "pricing_version": offer.pricing_version,
                "offered_at": offer.offered_at,
                "responded_at": offer.responded_at,
                "expires_at": offer.expires_at,
            }
            for offer in svc.dispatch_history(booking)
        ],
        "payment": None
        if payment is None
        else {
            "id": payment.id,
            "status": payment.status,
            "amount": payment.amount,
            "method": payment.method,
            "attempt_number": payment.attempt_number,
            "provider_reference": payment.provider_reference,
            "provider_error_code": payment.provider_error_code,
            "failure_reason": payment.failure_reason,
            "paid_at": payment.paid_at,
            "created_at": payment.created_at,
        },
        "job": None
        if job is None
        else {
            "id": job.id,
            "status": job.status,
            "started_at": job.started_at,
            "marked_done_at": job.marked_done_at,
            "confirmed_at": job.confirmed_at,
            "created_at": job.created_at,
        },
        "payout": None
        if payout is None
        else {
            "id": payout.id,
            "status": payout.status,
            "amount": payout.amount,
            "contractor_user_id": payout.contractor_id,
            "provider_reference": payout.provider_reference,
            "failure_reason": payout.failure_reason,
            "created_at": payout.created_at,
        },
        "created_at": booking.created_at,
        "updated_at": booking.updated_at,
    }


@router.get(
    "/bookings",
    response={200: AdminBookingListOut, 403: ErrorOut},
    summary="List bookings (admin only)",
)
def list_bookings(request, filters: BookingFilters = Query(...)):
    try:
        qs = svc.list_bookings(
            request.user,
            status=filters.status,
            dispatch_status=filters.dispatch_status,
            customer_id=filters.customer_id,
            contractor_id=filters.contractor_id,
            created_from=filters.created_from,
            created_to=filters.created_to,
            scheduled_from=filters.scheduled_from,
            scheduled_to=filters.scheduled_to,
            q=filters.q,
        )
    except AdminRequiredError as exc:
        return forbidden(exc)
    return 200, page(qs, filters, _serialize)


@router.get(
    "/bookings/{booking_id}",
    response={200: AdminBookingDetailOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve a booking with its full history (admin only)",
)
def retrieve_booking(request, booking_id: uuid.UUID):
    try:
        booking = svc.get_booking(request.user, booking_id)
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.BookingNotFoundError as exc:
        return error(404, exc.code, str(exc))
    return 200, _serialize_detail(booking)


class AdminCancelIn(Schema):
    reason: str = Field(..., min_length=1, max_length=255)


@router.post(
    "/bookings/{booking_id}/cancel",
    response={200: AdminBookingDetailOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    summary="Cancel an unpaid booking on the customer's behalf (admin only)",
    description=(
        "Same rule as the customer's own cancellation: only a `PENDING` booking "
        "with no payment, or only a `FAILED`/`NOT_CHARGED` one "
        "(`409 booking_not_cancellable` / `cancellation_requires_support` "
        "otherwise). `reason` is required and audited; the contractor holding a "
        "live or reserved offer is notified."
    ),
)
def cancel_booking(request, booking_id: uuid.UUID, payload: AdminCancelIn):
    from ..services import bookings as booking_svc

    try:
        booking = svc.get_booking(request.user, booking_id)
        booking_svc.cancel_booking(
            booking.customer, booking.id, reason=payload.reason,
            actor=request.user, request=request,
        )
        booking = svc.get_booking(request.user, booking_id)
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.BookingNotFoundError as exc:
        return error(404, exc.code, str(exc))
    except (
        booking_svc.BookingNotCancellableError,
        booking_svc.CancellationRequiresSupportError,
    ) as exc:
        return error(409, exc.code, str(exc))
    return 200, _serialize_detail(booking)
