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

from ..models import ACCESS_NOTES_MAX_LENGTH


class ServiceSelectionIn(Schema):
    """سطر خدمة عند الإنشاء."""

    service_type_id: uuid.UUID
    # صفر مسموح — يعني الرسم الأساسي وحده لتلك الخدمة
    room_count: int = Field(..., ge=0)


class BookingIn(Schema):
    """Create a booking from an optional frozen quote."""

    property_id: uuid.UUID
    service_selections: list[ServiceSelectionIn] = Field(default_factory=list)
    scheduled_at: datetime
    access_notes: str = Field("", max_length=ACCESS_NOTES_MAX_LENGTH)
    quote_id: Optional[uuid.UUID] = None
    payment_method_reference: str = Field("", max_length=255)


class QuoteIn(Schema):
   """Create a frozen maximum-price quote for one property."""

   property_id: uuid.UUID
   service_selections: list[ServiceSelectionIn]


class QuoteOut(Schema):
   id: uuid.UUID
   property_id: uuid.UUID
   maximum_total: Decimal
   services_total: Decimal
   currency: str
   pricing_version: int
   expires_at: datetime
   created_at: datetime


class BookingRescheduleIn(Schema):
    """
    إعادة جدولة حجز لم يجد مقاولًا.

    🔒 timezone للتوافق فقط: المنطقة مشتقة من عنوان العقار دائمًا، وأي
       قيمة مختلفة تُرفض (كانت تتيح تجاوز ساعات العمل أو 500).

    ⚠️ لا property_id ولا service_selections: إعادة الجدولة تغيّر الموعد
       وحده. تغيير العقار أو الخدمات حجزٌ آخر لا تعديل.
    """

    scheduled_at: datetime
    timezone: Optional[str] = None


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


class PaymentSummaryOut(Schema):
    """
    ملخص الدفع داخل الحجز — للعرض وحده.

    📌 سبب الوجود: شاشة الحجز تعرض حالة الدفع ومبلغه، وكانت تحتاج نداءً
       ثانيًا لكل حجز (/bookings/{id}/payment). في شاشة القائمة كان ذلك
       نداءً لكل صف.

    🔒 لا provider_reference ولا failure_reason هنا — والحذف هيكلي لا
       مشروط: الشكل الذي لا يُعرِّف الحقل لا يستطيع كشفه مهما فعل
       المُسلسِل. (نفس درس §43 الموثّق في payments/api/schemas.py.)

    ⚠️ ملخص لا بديل: GET /api/bookings/{id}/payment يبقى مسار التفاصيل
       بلا تغيير، وهو وحده ما يكشف provider_reference للإدارة.
    """

    # PENDING | SUCCEEDED | FAILED
    status: str
    amount: Decimal
    # CARD | APPLE_PAY | GOOGLE_PAY
    method: str


class BookingOut(Schema):
    """
    📌 توقيت كشف السعر (§36.1/§36.2):

       computed_price يبقى None ما دام الحجز PENDING، ويحمل اللقطة
       المجمَّدة فور أن يصبح CONFIRMED (أي فور قبول مقاول للعرض).
       الطبقة المُسلسِلة هي التي تقرر ذلك بناءً على الحالة — لا يكفي
       وجود قيمة في قاعدة البيانات لكشفها.
    """

    id: uuid.UUID
    # 📌 الرقم المرجعي المقروء (CLN-7F3K9Q) — يُعرض للعميل ويُستعمل في
    #    الدعم. ليس معرّفًا: لا يقبله أي مسار، والـid أعلاه يبقى المفتاح.
    public_reference: str
    customer_id: uuid.UUID
    property_id: uuid.UUID
    status: str
    # None قبل التأكيد، واللقطة بعده
    computed_price: Optional[Decimal] = None
    assigned_contractor_id: Optional[uuid.UUID] = None
    # 📌 موعد الزيارة بالـUTC كما هو مخزَّن.
    #    Optional لأن الصفوف السابقة للحقل بلا موعد — لا لأن الإنشاء
    #    الجديد يسمح بتركه (الـschema يفرضه إلزاميًا).
    scheduled_at: Optional[datetime] = None
    # نفس اللحظة محوَّلة لتوقيت العميل — الواجهة لا تحوّل بنفسها
    scheduled_at_local: Optional[datetime] = None
    # اسم IANA المستخدم في التحويل أعلاه (للعرض)
    customer_timezone: str = ""
    # 📌 تقدّم البحث عن مقاول — عرض فقط لا قرار:
    #    SEARCHING: عرض نشط أو تتابع جارٍ.
    #    NO_CONTRACTOR: استُنفد المرشَّحون في آخر محاولة.
    #    ASSIGNED: قُبل العرض.
    # ⚠️ لا يغني عن status ولا يناقضه: الحجز يبقى PENDING في الحالتين
    #    الأوليين. الواجهة تميّز "ما زلنا نبحث" عن "لا يوجد عامل" بهذا
    #    الحقل وحده (القرار المفتوح #16 لم يُحسم هنا).
    dispatch_status: str = ""
    # آخر محاولة ترشيح — مرجع "نبحث منذ ..." في الواجهة
    last_dispatch_attempt_at: Optional[datetime] = None
    # 🔒 ملاحظات وصول العميل — null لكل من ليس المقاول المُسنَد.
    #    الحجب في طبقة التسلسل لا هنا: الـschema يسمح بالقيمة، والمُسلسِل
    #    هو من يقرّر كشفها (نفس نمط computed_price أعلاه).
    # ⚠️ قد تحوي مكان مفتاح المنزل — لا تُكشف لمقاول لم يقبل بعد.
    access_notes: Optional[str] = None
    # 📌 حالة الدفع ومبلغه وطريقته — None ما دام الحجز PENDING: لا توجد
    #    دفعة قبل قبول المقاول أصلًا. نفس شرط computed_price أعلاه.
    # ⚠️ SUCCEEDED ليس شرطًا للظهور: الحجز قد يكون CONFIRMED ودفعته
    #    FAILED (الشحن يقع بعد التأكيد ولا يتراجع عنه)، والواجهة تحتاج
    #    أن ترى ذلك لا أن يُخفى عنها.
    payment: Optional[PaymentSummaryOut] = None
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
   """Dispatch offer data safe for the addressed contractor."""

   id: uuid.UUID
   booking_id: uuid.UUID
   contractor_id: uuid.UUID
   status: str
   distance_km: Optional[Decimal] = None
   offered_at: datetime
   responded_at: Optional[datetime] = None
   expires_at: datetime
   total_amount: Optional[Decimal] = None
   contractor_earnings: Optional[Decimal] = None
   currency: str = "AUD"
   eta_seconds: Optional[int] = None
   service_summary: list[str] = Field(default_factory=list)
   property_summary: Optional[str] = None


class OfferServiceOut(Schema):
    service_type_id: uuid.UUID
    service_name: str
    room_count: int


class OfferDetailOut(OfferOut):
    """
    العرض كما يراه المقاول قبل الرد — ما يكفي ليقرر.

    🔒 قبل القبول: الضاحية والولاية والرمز البريدي فقط، بلا عنوان الشارع
       ولا ملاحظات الوصول — تُكشف للمقاول المُسنَد بعد القبول وحده.
    📌 السعر والأرباح مجمَّدان على العرض منذ إنشائه (OfferOut).
    """

    scheduled_at: Optional[datetime] = None
    scheduled_at_local: Optional[datetime] = None
    customer_timezone: str = ""
    suburb: str = ""
    state: str = ""
    postcode: str = ""
    property_type: str = ""
    services: list[OfferServiceOut] = []


class OfferResponseOut(Schema):
    """
    نتيجة الرد على عرض.

    next_offer معرّف العرض التالي عند الرفض (أو None إن لم يوجد مرشَّح —
    القرار المفتوح #16). لا تفاصيل عن المقاول التالي: الرافض لا يحتاجها.
    """

    offer: OfferOut
    next_offer: Optional[str] = None
