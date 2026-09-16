"""
API Schemas — Support Domain (MVP)

مبنية يدويًا (لا ModelSchema) حتى لا تتسرّب حقول داخلية عند تغيّر
الـModels — نفس قرار بقية النطاقات.
"""

import uuid
from datetime import datetime
from typing import Optional

from ninja import Schema
from pydantic import Field

from ..models import MESSAGE_MAX_LENGTH, SupportCategory


class SupportRequestIn(Schema):
    """
    إنشاء طلب دعم.

    ⚠️ status غير موجود عمدًا: كل طلب يبدأ SUBMITTED، ولا يضبطه العميل.
    ⚠️ user غير موجود: يُشتق من التوكن، فلا يمكن فتح طلب باسم غيرك.

    📌 category مُقيَّد بـSupportCategory: قيمة خارجها ترفضها طبقة
       التحقق بـ422 قبل أن تصل إلى الخدمة.
    """

    category: SupportCategory
    message: str = Field(..., min_length=1, max_length=MESSAGE_MAX_LENGTH)
    # 📌 اختياري: الطلب قد لا يخصّ حجزًا (مشكلة دخول، عطل في التطبيق).
    booking_id: Optional[uuid.UUID] = None


class SupportRequestOut(Schema):
    """
    طلب دعم في الرد.

    📌 booking_id يُعاد كما هو (None إن لم يُربط) — الواجهة تستعمله
       لفتح الحجز من شاشة الطلب.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    booking_id: Optional[uuid.UUID] = None
    category: str
    message: str
    status: str
    created_at: datetime
    updated_at: datetime


class ErrorOut(Schema):
    """نفس شكل الخطأ المستخدم في بقية النطاقات."""

    code: str
    detail: str
