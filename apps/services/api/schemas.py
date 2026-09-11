"""
API Schemas — Services Catalog & Pricing Domain

مبنية يدويًا (لا ModelSchema) حتى لا تتسرّب حقول داخلية عند تغيّر الـModels
— نفس قرار Properties Domain.

🔒 هذه المخططات تخدم نقاط نهاية ADMIN فقط. لا يوجد في هذه المرحلة أي
   مخطط موجّه للعميل — الأسعار الخام غير مكشوفة لـCUSTOMER/CONTRACTOR.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ninja import Schema
from pydantic import Field

# الأسعار غير سالبة على مستوى الـschema أيضًا (فحص مبكر قبل الـModel).
PriceField = Field(..., ge=0, max_digits=10, decimal_places=2)
OptionalPriceField = Field(None, ge=0, max_digits=10, decimal_places=2)


class ServiceTypeIn(Schema):
    """إنشاء نوع خدمة."""

    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""
    # سعر الغرفة وسعر الأساس لهذه الخدمة تحديدًا — ليسا قيمًا عامة.
    room_price: Decimal = PriceField
    base_price: Decimal = PriceField
    is_active: bool = True


class ServiceTypePatch(Schema):
    """
    تعديل جزئي.

    ⚠️ حقول السعر اختيارية وقابلة للتعديل في أي وقت (§36.2) — بلا أي
       منطق إعادة حساب رجعي.
    """

    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = None
    room_price: Optional[Decimal] = OptionalPriceField
    base_price: Optional[Decimal] = OptionalPriceField
    is_active: Optional[bool] = None


class ServiceTypeOut(Schema):
    id: uuid.UUID
    name: str
    description: str
    room_price: Decimal
    base_price: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PricingConfigOut(Schema):
    """سعر الكيلومتر العام — قيمة واحدة للنظام كله."""

    price_per_km: Decimal
    updated_at: datetime


class PricingConfigPatch(Schema):
    price_per_km: Decimal = PriceField


class ErrorOut(Schema):
    """نفس شكل الخطأ المستخدم في Identity و Properties Domains."""

    code: str
    detail: str
