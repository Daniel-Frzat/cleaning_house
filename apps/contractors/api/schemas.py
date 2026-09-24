"""
API Schemas — Contractor Profile Domain

مبنية يدويًا (لا ModelSchema) حتى لا تتسرّب حقول داخلية عند تغيّر الـModels
— نفس قرار Properties و Services.

🔒 لا تكشف الاستجابات أي بيانات حساب عدا معرّف المستخدم — لا هاتف،
   لا بريد، لا دور، لا حالة حساب.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from ninja import Schema
from pydantic import Field

from ..models import AustralianState, AvailabilityStatus, VerificationStatus


# حدود الإحداثيات — فحص شكلي لا جغرافي (نفس قاعدة apps/properties).
LatitudeField = Field(None, ge=-90, le=90, max_digits=9, decimal_places=6)
LongitudeField = Field(None, ge=-180, le=180, max_digits=9, decimal_places=6)


class ContractorProfileIn(Schema):
    """
    إنشاء ملف المقاول.

    ⚠️ availability_status غير موجودة عمدًا: الملف يبدأ UNAVAILABLE دائمًا،
       والإتاحة فعل صريح لاحق عبر مسارها المخصّص.
    ⚠️ country غير موجود — منتج بسوق واحد، والحقل غير قابل للتعديل.
    """

    business_name: str = Field("", max_length=255)

    street_address: str = Field("", max_length=255)
    suburb: str = Field("", max_length=120)
    state: Optional[AustralianState] = None
    postcode: str = Field("", pattern=r"^(\d{4})?$")

    # 📌 تصل من GPS الجهاز عند الالتقاط — لا geocoding على الخادم (§34/§4).
    # ⚠️ المقاول بلا إحداثيات **لا يصله أي عرض إطلاقًا**: الترشيح يقيس
    #    المسافة، ومن لا موقع له يسقط صامتًا بلا رسالة خطأ.
    latitude: Optional[Decimal] = LatitudeField
    longitude: Optional[Decimal] = LongitudeField


class ContractorProfilePatch(Schema):
    """
    تعديل جزئي للملف.

    ⚠️ availability_status غير موجودة هنا — لها مسارها المخصّص وحدها،
       لأنها تُستدعى بكثرة ويجب أن تبقى خفيفة ومنفصلة عن تعديل البيانات.
    """

    business_name: Optional[str] = Field(None, max_length=255)

    street_address: Optional[str] = Field(None, max_length=255)
    suburb: Optional[str] = Field(None, max_length=120)
    state: Optional[AustralianState] = None
    postcode: Optional[str] = Field(None, pattern=r"^(\d{4})?$")

    latitude: Optional[Decimal] = LatitudeField
    longitude: Optional[Decimal] = LongitudeField


class AvailabilityPatch(Schema):
    """جسم خفيف لمسار الجاهزية — حقل واحد لا غير."""

    availability_status: AvailabilityStatus


class ContractorProfileOut(Schema):
    id: uuid.UUID
    # معرّف المستخدم فقط — لا بيانات حساب أخرى
    user_id: uuid.UUID

    business_name: str

    street_address: str
    suburb: str
    state: str
    postcode: str
    country: str

    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None

    availability_status: str

    created_at: datetime
    updated_at: datetime


class ErrorOut(Schema):
    """نفس شكل الخطأ المستخدم في Identity و Properties و Services."""

    code: str
    detail: str


# ============================================================
# التحقق — تسجيل النشاط والتأمين (Change Set §6)
# ============================================================
# ⚠️ مراجعة يدوية بحتة: لا حقل هنا يشير إلى أي سجل حكومي أو خدمة تحقق.


class BusinessRegistrationIn(Schema):
    """
    تقديم تسجيل نشاط.

    ⚠️ status/reviewed_by/reviewed_at غير موجودة عمدًا: السجل يبدأ PENDING
       دائمًا، وحقول المراجعة تُملأ من الإدارة وحدها.
    """

    # 11 رقمًا — فحص شكلي فقط، ليس تحققًا من السجل الحكومي
    abn: str = Field(..., pattern=r"^\d{11}$")
    business_name: str = Field(..., min_length=1, max_length=255)


class InsuranceDocumentIn(Schema):
    """
    تقديم وثيقة تأمين.

    ⚠️ document_reference نص (رقم بوليصة) — لا رفع ملفات (Infra §7).
    """

    document_reference: str = Field(..., min_length=1, max_length=255)
    expiry_date: date


class ReviewPatch(Schema):
    """
    قرار المراجعة الإدارية.

    ⚠️ rejection_reason إلزامي عند REJECTED — يُفرض في طبقة الخدمة
       والـModel معًا، لا في الـschema (حتى تكون الرسالة مفهومة).
    """

    status: VerificationStatus
    rejection_reason: Optional[str] = None


class BusinessRegistrationOut(Schema):
    id: uuid.UUID
    contractor_id: uuid.UUID
    abn: str
    business_name: str
    status: str
    reviewed_by_id: Optional[uuid.UUID] = None
    reviewed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class InsuranceDocumentOut(Schema):
    id: uuid.UUID
    contractor_id: uuid.UUID
    document_reference: str
    expiry_date: date
    status: str
    reviewed_by_id: Optional[uuid.UUID] = None
    reviewed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class PendingVerificationsOut(Schema):
    """
    ما ينتظر المراجعة — النوعان منفصلان لا مدمجان في قائمة واحدة.
    """

    business_registrations: list[BusinessRegistrationOut]
    insurance_documents: list[InsuranceDocumentOut]


class ContractorVerificationsOut(Schema):
    """السجل الكامل لتحقق مقاول واحد — كل الحالات، الأحدث أولًا."""

    contractor_id: uuid.UUID
    # الأهلية المحسوبة لحظيًا (is_contractor_eligible) — للعرض فقط
    eligible: bool
    business_registrations: list[BusinessRegistrationOut]
    insurance_documents: list[InsuranceDocumentOut]
