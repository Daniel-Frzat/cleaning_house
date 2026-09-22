"""
API Schemas — Payout Domain (Change Set §36.5)

مبنية يدويًا (لا ModelSchema) — نفس قرار بقية النطاقات.

🔒 provider_reference تفصيل تشخيصي داخلي — والحجب هنا **هيكلي** لا قيمي:

   PayoutOut (عام)      : لا يُعرِّف الحقل إطلاقًا، فلا يظهر مفتاحه في JSON.
   PayoutAdminOut       : يُعرِّف الحقل، فيظهر مفتاحه دائمًا — وقيمته قد
                          تكون None بشكل مشروع (دفعة فاشلة بلا مرجع).

   الفرق مقصود: غياب المفتاح يعني "لا يحق لك رؤيته"، أما المفتاح بقيمة
   None فيعني "يحق لك، ولا يوجد مرجع بعد". الخلط بينهما كان عيب §43
   المكتشَف رجعيًا: كان المفتاح يظهر لغير الإدارة بقيمة null، فيوحي
   بوجود حقل محجوب بدل ألا يوجد.

⚠️ لا تُوحَّد الاثنتان بـexclude_none: ذلك يحجب بالقيمة لا بالدور، فيُخفي
   المرجع عن الإدارة أيضًا حين يكون None — وهي حالة مشروعة تحتاج ظهورًا.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from ninja import Schema


class PayoutOut(Schema):
    """
    الشكل العام (المقاول المستحِق).

    🔒 provider_reference غير معرَّف هنا عمدًا — غائب من JSON كليًا.
    """

    id: uuid.UUID
    booking_id: uuid.UUID
    contractor_id: uuid.UUID
    amount: Decimal
    status: str
    failure_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class PayoutAdminOut(PayoutOut):
    """
    شكل الإدارة — يضيف المرجع التشخيصي وحده.

    الوراثة من PayoutOut تضمن ألا ينحرف الشكلان: أي حقل عام يُضاف لاحقًا
    يظهر في الاثنين تلقائيًا، والفرق يبقى محصورًا في هذا الحقل.
    """

    # قد تكون None بشكل مشروع (دفعة فاشلة) — والمفتاح يظهر رغم ذلك
    provider_reference: Optional[str] = None


class ErrorOut(Schema):
    """نفس شكل الخطأ المستخدم في بقية النطاقات."""

    code: str
    detail: str


class EarningsItemOut(Schema):
    payout_id: uuid.UUID
    booking_id: uuid.UUID
    public_reference: str
    service_summary: list[str]
    completed_at: Optional[datetime] = None
    amount: Decimal
    currency: str = "AUD"
    status: str


class EarningsOut(Schema):
    from_date: date
    to_date: date
    total: Decimal
    paid_total: Decimal
    processing_total: Decimal
    completed_jobs: int
    items: list[EarningsItemOut]
