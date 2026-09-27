import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from ninja import Schema
from pydantic import Field

from ..models import RecleanArea


class ReviewIn(Schema):
    stars: int = Field(..., ge=1, le=5)
    comment: str = Field("", max_length=1000)


class ReviewOut(Schema):
    id: uuid.UUID
    booking_id: uuid.UUID
    stars: int
    comment: str
    created_at: datetime


class RecleanRequestIn(Schema):
    areas: list[RecleanArea] = Field(..., min_length=1)
    details: str = Field("", max_length=2000)


class RecleanRequestOut(Schema):
    id: uuid.UUID
    booking_id: uuid.UUID
    areas: list[str]
    details: str
    status: str
    decision_note: str
    decided_at: Optional[datetime] = None
    created_at: datetime


class AdminRecleanRequestOut(RecleanRequestOut):
    customer_id: uuid.UUID
    public_reference: str


class AdminRecleanListOut(Schema):
    count: int
    items: list[AdminRecleanRequestOut]


class RecleanDecisionIn(Schema):
    status: str = Field(..., pattern="^(APPROVED|REJECTED)$")
    note: str = Field("", max_length=1000)


class InvoiceLineOut(Schema):
    description: str
    quantity: int
    amount: Decimal


class InvoicePaymentOut(Schema):
    method: str
    display_name: str
    status: str
    paid_at: Optional[datetime] = None
    refunded_amount: Decimal


class InvoiceOut(Schema):
    number: str = Field(..., description="INV-YYYY-NNNNNN, sequential per year; never changes once issued.")
    issued_at: datetime
    booking_id: uuid.UUID
    public_reference: str
    service_date: Optional[date] = None
    service_address: str
    lines: list[InvoiceLineOut]
    total: Decimal
    currency: str
    gst_included: bool = Field(False, description="Always false: no GST line (PO decision).")
    payment: InvoicePaymentOut
