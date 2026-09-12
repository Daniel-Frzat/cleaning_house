"""
API Schemas — Payout Domain (Change Set §36.5)

مبنية يدويًا (لا ModelSchema) — نفس قرار بقية النطاقات.

🔒 provider_reference تفصيل تشخيصي داخلي: لا يُكشف للمقاول، ويظهر
   للإدارة وحدها (نفس قرار Payment في §43).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ninja import Schema


class PayoutOut(Schema):
    id: uuid.UUID
    booking_id: uuid.UUID
    contractor_id: uuid.UUID
    amount: Decimal
    status: str
    # None للمقاول دائمًا؛ القيمة الحقيقية للإدارة فقط
    provider_reference: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ErrorOut(Schema):
    """نفس شكل الخطأ المستخدم في بقية النطاقات."""

    code: str
    detail: str
