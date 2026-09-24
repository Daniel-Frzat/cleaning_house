"""
Back-office — العقارات (ADMIN فقط، قراءة).

    GET /api/admin/properties
    GET /api/admin/properties/{property_id}
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ninja import Query, Router, Schema
from pydantic import Field

from apps.accounts.authentication import AdminJWTAuth
from apps.audit.api.common import ErrorOut, PageQuery, error, forbidden, page
from apps.audit.services.backoffice import AdminRequiredError

from ..models import AustralianState
from ..services import admin as svc

router = Router(tags=["Admin — Properties"], auth=AdminJWTAuth())


class PropertyFilters(PageQuery):
    owner_id: Optional[uuid.UUID] = None
    state: Optional[AustralianState] = None
    is_active: Optional[bool] = None
    q: Optional[str] = Field(None, max_length=255)


class AdminAddressOut(Schema):
    id: uuid.UUID
    street_address: str
    suburb: str
    state: str
    postcode: str
    country: str
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None


class AdminPropertyOut(Schema):
    id: uuid.UUID
    owner_id: uuid.UUID
    owner_phone: str
    owner_name: str
    label: str
    property_type: str
    is_active: bool
    address: Optional[AdminAddressOut] = None
    created_at: datetime
    updated_at: datetime


class AdminPropertyListOut(Schema):
    count: int
    items: list[AdminPropertyOut]


class AdminPropertyDetailOut(AdminPropertyOut):
    bookings_count: int


def serialize_address(address):
    if address is None:
        return None
    return {
        "id": address.id,
        "street_address": address.street_address,
        "suburb": address.suburb,
        "state": address.state,
        "postcode": address.postcode,
        "country": address.country,
        "latitude": address.latitude,
        "longitude": address.longitude,
    }


def _serialize(prop):
    return {
        "id": prop.id,
        "owner_id": prop.owner_id,
        "owner_phone": prop.owner.phone,
        "owner_name": prop.owner.full_name,
        "label": prop.label,
        "property_type": prop.property_type,
        "is_active": prop.is_active,
        "address": serialize_address(getattr(prop, "address", None)),
        "created_at": prop.created_at,
        "updated_at": prop.updated_at,
    }


@router.get(
    "/properties",
    response={200: AdminPropertyListOut, 403: ErrorOut},
    summary="List properties (admin only)",
)
def list_properties(request, filters: PropertyFilters = Query(...)):
    try:
        qs = svc.list_properties(
            request.user,
            owner_id=filters.owner_id,
            state=filters.state,
            is_active=filters.is_active,
            q=filters.q,
        )
    except AdminRequiredError as exc:
        return forbidden(exc)
    return 200, page(qs, filters, _serialize)


@router.get(
    "/properties/{property_id}",
    response={200: AdminPropertyDetailOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve a property (admin only)",
)
def retrieve_property(request, property_id: uuid.UUID):
    try:
        prop = svc.get_property(request.user, property_id)
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.PropertyNotFoundError as exc:
        return error(404, exc.code, str(exc))
    return 200, {**_serialize(prop), "bookings_count": prop.bookings_count}
