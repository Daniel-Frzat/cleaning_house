"""
API Schemas — Booking Domain (Change Set §36.1، §20)

مبنية يدويًا (لا ModelSchema) حتى لا تتسرّب حقول داخلية عند تغيّر الـModels
— نفس قرار Properties و Services و Contractors.

🔒 قرار محوري: BookingOut لا يحتوي computed_price إطلاقًا.

   الحذف كامل وليس إخفاءً مشروطًا (None). لو كان الحقل موجودًا بقيمة null،
   لكان كشفُه لاحقًا سهوًا مسألةَ سطر واحد. §36.1 ينص أن السعر لا يُكشف
   إلا بعد قبول المقاول — ولا آلية قبول في هذه المرحلة أصلًا، فلا سبب
   لوجود الحقل في الرد.

🔒 لا يحتوي assigned_contractor كذلك — لا إسناد بعد.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ninja import Schema
from pydantic import Field


class ServiceSelectionIn(Schema):
    """سطر خدمة عند الإنشاء."""

    service_type_id: uuid.UUID
    # صفر مسموح — يعني الرسم الأساسي وحده لتلك الخدمة
    room_count: int = Field(..., ge=0)


class BookingIn(Schema):
    """
    إنشاء حجز.

    ⚠️ status غير موجود عمدًا: كل حجز جديد يبدأ PENDING.
    ⚠️ computed_price و assigned_contractor غير موجودين — لا يُقبلان من
       العميل ولا يُضبطان في هذه المرحلة.
    """

    property_id: uuid.UUID
    # القائمة الفارغة تُرفض في طبقة الخدمة برسالة مفهومة (400)
    service_selections: list[ServiceSelectionIn]


class ServiceSelectionOut(Schema):
    """
    سطر خدمة في الرد.

    ⚠️ بلا أي حقل سعر (§36.2): لا تفصيل بالبنود للعميل، ولا سعر مخزَّن
       لكل سطر.
    """

    id: uuid.UUID
    service_type_id: uuid.UUID
    service_type_name: str
    room_count: int


class BookingOut(Schema):
    """
    📌 توقيت كشف السعر (§36.1/§36.2):

       computed_price يبقى None ما دام الحجز PENDING، ويحمل اللقطة
       المجمَّدة فور أن يصبح CONFIRMED (أي فور قبول مقاول للعرض).
       الطبقة المُسلسِلة هي التي تقرر ذلك بناءً على الحالة — لا يكفي
       وجود قيمة في قاعدة البيانات لكشفها.
    """

    id: uuid.UUID
    customer_id: uuid.UUID
    property_id: uuid.UUID
    status: str
    # None قبل التأكيد، واللقطة بعده
    computed_price: Optional[Decimal] = None
    assigned_contractor_id: Optional[uuid.UUID] = None
    service_selections: list[ServiceSelectionOut]
    created_at: datetime
    updated_at: datetime


class ErrorOut(Schema):
    """نفس شكل الخطأ المستخدم في بقية النطاقات."""

    code: str
    detail: str


# ============================================================
# عروض الإسناد (Change Set §36.1، §36.5، §36.6)
# ============================================================


class OfferOut(Schema):
    """
    عرض إسناد.

    ⚠️ بلا أي حقل سعر: العرض لا يحمل سعرًا، والسعر يعيش على الحجز بعد
       القبول وحده (§36.1). distance_km ليست سعرًا — هي الأساس المخزَّن
       الذي سيُحسب عليه السعر إن قُبل العرض.
    """

    id: uuid.UUID
    booking_id: uuid.UUID
    contractor_id: uuid.UUID
    status: str
    distance_km: Optional[Decimal] = None
    offered_at: datetime
    responded_at: Optional[datetime] = None
    expires_at: datetime


class OfferResponseOut(Schema):
    """
    نتيجة الرد على عرض.

    next_offer معرّف العرض التالي عند الرفض (أو None إن لم يوجد مرشَّح —
    القرار المفتوح #16). لا تفاصيل عن المقاول التالي: الرافض لا يحتاجها.
    """

    offer: OfferOut
    next_offer: Optional[str] = None
