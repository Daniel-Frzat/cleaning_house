"""
API Schemas — Payment Domain (Change Set §36.4)

مبنية يدويًا (لا ModelSchema) حتى لا تتسرّب حقول داخلية — نفس قرار بقية
النطاقات.

🔒 provider_reference تفصيل تشخيصي داخلي: لا يُكشف للعميل إطلاقًا، ويظهر
   للإدارة وحدها. لذلك الحقل اختياري هنا وتملؤه الطبقة المُسلسِلة بناءً
   على دور الطالب — لا على وجود القيمة.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ninja import Schema


class PaymentOut(Schema):
    id: uuid.UUID
    booking_id: uuid.UUID
    amount: Decimal
    method: str
    status: str
    # None للعميل دائمًا؛ القيمة الحقيقية للإدارة فقط
    provider_reference: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ErrorOut(Schema):
    """نفس شكل الخطأ المستخدم في بقية النطاقات."""

    code: str
    detail: str
