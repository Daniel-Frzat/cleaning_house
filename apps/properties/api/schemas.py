"""
API Schemas — Properties & Address Domain (Phase 1)

مبنية يدويًا (لا ModelSchema) حتى لا تتسرّب حقول داخلية عند تغيّر الـModels.

🔒 لا تكشف الاستجابات أي بيانات للمالك عدا معرّفه — لا هاتف، لا بريد،
   لا دور، لا حالة حساب.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ninja import Schema
from pydantic import Field

from apps.geo_fields import Latitude, Longitude

from ..models import AustralianState, PropertyType


# ------------------------------------------------------------
# العنوان
# ------------------------------------------------------------


class AddressIn(Schema):
    """
    حقول العنوان عند الإنشاء.

    country غير موجود عمدًا — منتج بسوق واحد، والحقل غير قابل للتعديل.

    📌 latitude/longitude اختياريان ويُرسَلان من GPS الجهاز عند الالتقاط.
       ⚠️ العقار بلا إحداثيات **لا يُسنَد إليه أي مقاول إطلاقًا**: محرّك
          الإسناد يقيس المسافة، ومن لا موقع له يسقط من الترشيح صامتًا.
          فاجعلهما إلزاميين في الواجهة ولو كانا اختياريين هنا — الاختيارية
          للتوافق مع العقارات القائمة لا لتشجيع تركهما.
    """

    street_address: str = Field(..., min_length=1, max_length=255)
    suburb: str = Field(..., min_length=1, max_length=120)
    state: AustralianState
    postcode: str = Field(..., pattern=r"^\d{4}$")
    latitude: Optional[Latitude] = None
    longitude: Optional[Longitude] = None
    raw_input: Optional[str] = None


class AddressPatch(Schema):
    """
    كل الحقول اختيارية — تعديل جزئي.

    📌 تحديث الإحداثيات متاح هنا: عقار أُنشئ بلا موقع (أو بموقع خاطئ)
       يُصحَّح بـPATCH، فيصير قابلًا للإسناد.
    """

    street_address: Optional[str] = Field(None, min_length=1, max_length=255)
    suburb: Optional[str] = Field(None, min_length=1, max_length=120)
    state: Optional[AustralianState] = None
    postcode: Optional[str] = Field(None, pattern=r"^\d{4}$")
    latitude: Optional[Latitude] = None
    longitude: Optional[Longitude] = None
    raw_input: Optional[str] = None


class AddressOut(Schema):
    id: uuid.UUID
    street_address: str
    suburb: str
    state: str
    postcode: str
    country: str
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    raw_input: Optional[str] = None


# ------------------------------------------------------------
# العقار
# ------------------------------------------------------------
class PropertyIn(Schema):
    """إنشاء عقار وعنوانه في طلب واحد (nested)."""

    label: str = Field("", max_length=100)
    property_type: PropertyType = PropertyType.HOUSE
    address: AddressIn


class PropertyPatch(Schema):
    """
    تعديل جزئي للعقار و/أو عنوانه.

    owner غير موجود عمدًا — الملكية لا تُنقل عبر هذا المسار.
    """

    label: Optional[str] = Field(None, max_length=100)
    property_type: Optional[PropertyType] = None
    is_active: Optional[bool] = None
    address: Optional[AddressPatch] = None


class ServiceabilityWarningOut(Schema):
    """سبب عدم قابلية العقار للإسناد — نص جاهز للعرض ورمز ثابت للتفريع."""

    code: str
    message: str


class PropertyOut(Schema):
    id: uuid.UUID
    # معرّف المالك فقط — لا بيانات حساب أخرى
    owner_id: uuid.UUID
    label: str
    property_type: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    address: Optional[AddressOut] = None
    # 📌 مشتقّ لا مخزَّن: null يعني العقار قابل للإسناد.
    # ⚠️ عقار بلا إحداثيات يُنشأ بنجاح لكنه لا يُسنَد إليه مقاول أبدًا،
    #    والفشل صامت في محرّك الترشيح. هذا الحقل يكشفه لحظة الإنشاء.
    serviceability_warning: Optional[ServiceabilityWarningOut] = None


class ErrorOut(Schema):
    """نفس شكل الخطأ المستخدم في Identity Domain."""

    code: str
    detail: str
