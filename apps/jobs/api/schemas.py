"""
API Schemas — Jobs Domain (Change Set §36.3؛ Infra §7)

مبنية يدويًا (لا ModelSchema) — نفس قرار بقية النطاقات.

🔒 storage_key لا يُكشف للعميل إطلاقًا: مرجع تخزين داخلي يُستبدل دائمًا
   بـsigned_url. يظهر للإدارة وحدها كتفصيل تشخيصي.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ninja import Schema
from pydantic import Field


# حدود الإحداثيات — فحص شكلي لا جغرافي (نفس قاعدة properties/contractors).
LatitudeField = Field(..., ge=-90, le=90, max_digits=9, decimal_places=6)
LongitudeField = Field(..., ge=-180, le=180, max_digits=9, decimal_places=6)


class JobPhotoOut(Schema):
    """
    صورة مهمة.

    🔒 storage_key يبقى None لغير الإدارة — الطبقة المُسلسِلة تقرّر بناءً
       على دور الطالب، لا على وجود القيمة.
    """

    id: uuid.UUID
    photo_type: str
    signed_url: str
    storage_key: Optional[str] = None
    uploaded_at: datetime


class JobOut(Schema):
    id: uuid.UUID
    booking_id: uuid.UUID
    status: str
    # يُملأ حين يعلن المقاول بدء العمل (ASSIGNED → IN_PROGRESS)
    started_at: Optional[datetime] = None
    # 🔒 ملاحظات وصول العميل — null لكل من ليس المقاول المُسنَد.
    #    قد تحوي مكان مفتاح المنزل، فالحجب افتراضي في المُسلسِل.
    access_notes: Optional[str] = None
    marked_done_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    photos: list[JobPhotoOut]
    created_at: datetime


class ErrorOut(Schema):
    """نفس شكل الخطأ المستخدم في بقية النطاقات."""

    code: str
    detail: str


class JobLocationIn(Schema):
    """
    تحديث موقع المقاول المُسنَد.

    ⚠️ الإحداثيتان إلزاميتان: تحديث موقع بإحداثية واحدة لا معنى له.

    📌 recorded_at إلزامي ويصل من الجهاز — لحظة التقاط النقطة لا لحظة
       وصولها. الفارق مهم حين تتأخر الشبكة: نقطة عمرها دقيقتان يجب أن
       تُعرض كذلك لا كأنها الآن.
    ⚠️ لا يُوثق به وحده: الخادم يختم وقت الاستلام بنفسه، وحساب التقادم
       يقوم على ختمه لا على ساعة الجهاز.
    """

    latitude: Decimal = LatitudeField
    longitude: Decimal = LongitudeField
    # 📌 دقة بالأمتار إن توفّرت — تُرسم كدائرة عدم يقين حول النقطة.
    # ⚠️ اختيارية: أجهزة لا تبلّغها، ورفض التحديث لغيابها يُسقط تتبعًا صالحًا.
    accuracy: Optional[Decimal] = Field(None, ge=0, max_digits=7, decimal_places=2)
    recorded_at: datetime


class ContractorLocationOut(Schema):
    """آخر موقع معلوم للمقاول، وعمره."""

    latitude: Decimal
    longitude: Decimal
    accuracy: Optional[Decimal] = None
    # لحظة التقاط الجهاز كما بلّغها (غير موثوقة — للعرض)
    recorded_at: datetime
    # 🔒 ختم الخادم — مرجع حساب العمر والتقادم
    received_at: datetime
    age_seconds: int
    # ⚠️ true يعني "توقّف الإرسال" لا "الموقع خاطئ": اعرض آخر تحديث
    #    منذ متى بدل نقطة توحي بأنها حيّة.
    is_stale: bool


class PropertyLocationOut(Schema):
    """
    وجهة الرحلة — إحداثيات العقار.

    📌 تُعاد مع التتبع حتى ترسم الواجهة الطرفين من نداء واحد.
    🔒 الإحداثيات وحدها: لا عنوان ولا ملاحظات وصول — تلك لها مسارها
       وقواعد حجبها.
    """

    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None


class JobTrackingOut(Schema):
    """
    لقطة التتبع الكاملة لنداء واحد.

    📌 يجمع المهمة والعقار والموقع معًا: الواجهة ترسم الخريطة بلا نداء
       ثانٍ لجلب إحداثيات العقار.

    ⚠️ tracking_active=false تعني انتهاء النافذة (بدأ العمل أو تجاوزه).
       عندها يكون contractor_location = null دائمًا، حتى لو كانت نقطة
       مخزَّنة — لا يُعرض موقع العامل بعد انتهاء الخدمة.

    ⚠️ contractor_location = null مع tracking_active=true حالة عادية:
       المقاول لم يرسل بعد. اعرض "في الطريق" لا رسالة خطأ.

    ⚠️ لا ETA ولا مسار شوارع: كلاهما يحتاج Directions API (Google/Mapbox)
       — قرار مزوّد مفتوح. النقطتان هنا لا ترسمان طريقًا.
    """

    booking_id: uuid.UUID
    job_id: uuid.UUID
    job_status: str
    tracking_active: bool
    contractor_location: Optional[ContractorLocationOut] = None
    property_location: PropertyLocationOut
