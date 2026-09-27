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

from ..models import RoundingRule

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
    """
    إعداد التسعير العام — قيمة واحدة للنظام كله.

    رسم المسافة = max(0, المسافة − included_distance_km) × price_per_km،
    مسقوفًا بـmaximum_travel_fee. السقف المعروض للعميل قبل البحث =
    مجموع الخدمات + maximum_travel_fee.
    """

    price_per_km: Decimal
    included_distance_km: Decimal
    maximum_travel_fee: Decimal
    rounding_rule: str
    dispatch_offer_ttl_seconds: int
    currency: str
    pricing_version: int
    active_from: datetime
    updated_at: datetime


class PricingConfigPatch(Schema):
    """تحديث جزئي: أرسل ما تريد تغييره فقط (حقل واحد على الأقل)."""

    price_per_km: Optional[Decimal] = Field(
        None, ge=0, max_digits=10, decimal_places=2, description="Travel rate per kilometre."
    )
    included_distance_km: Optional[Decimal] = Field(
        None, ge=0, max_digits=6, decimal_places=3,
        description="Free distance; only the excess is charged.",
    )
    maximum_travel_fee: Optional[Decimal] = Field(
        None, ge=0, max_digits=10, decimal_places=2,
        description=(
            "Cap on the travel component. Also sets the customer's pre-request "
            "maximum: services total + this cap."
        ),
    )
    rounding_rule: Optional[RoundingRule] = Field(None, description="Applied once, to the final total.")
    dispatch_offer_ttl_seconds: Optional[int] = Field(
        None, ge=1, description="How long a contractor can answer an offer. Not a pricing change."
    )


class ErrorOut(Schema):
    """نفس شكل الخطأ المستخدم في Identity و Properties Domains."""

    code: str
    detail: str
