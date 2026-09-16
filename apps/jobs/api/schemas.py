"""
API Schemas — Jobs Domain (Change Set §36.3؛ Infra §7)

مبنية يدويًا (لا ModelSchema) — نفس قرار بقية النطاقات.

🔒 storage_key لا يُكشف للعميل إطلاقًا: مرجع تخزين داخلي يُستبدل دائمًا
   بـsigned_url. يظهر للإدارة وحدها كتفصيل تشخيصي.
"""

import uuid
from datetime import datetime
from typing import Optional

from ninja import Schema


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
