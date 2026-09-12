"""
API Schemas — Payment Domain (Change Set §36.4)

مبنية يدويًا (لا ModelSchema) حتى لا تتسرّب حقول داخلية — نفس قرار بقية
النطاقات.

🔒 provider_reference تفصيل تشخيصي داخلي — والحجب هنا **هيكلي** لا قيمي:

   PaymentOut (عام)     : لا يُعرِّف الحقل إطلاقًا، فلا يظهر مفتاحه في JSON.
   PaymentAdminOut      : يُعرِّف الحقل، فيظهر مفتاحه دائمًا — وقيمته قد
                          تكون None بشكل مشروع (دفعة فاشلة بلا مرجع).

   ⚠️ تصحيح رجعي: كان الحقل معرَّفًا في الشكل العام وتملؤه الطبقة
      المُسلسِلة بـNone لغير الإدارة. ذلك حجب بالقيمة لا بالبنية — المفتاح
      كان يظهر في JSON ويوحي بوجود حقل محجوب. الآن الشكل العام لا يعرفه.

⚠️ لا تُوحَّد الاثنتان بـexclude_none: ذلك يحجب بالقيمة لا بالدور، فيُخفي
   المرجع عن الإدارة أيضًا حين يكون None — وهي حالة مشروعة تحتاج ظهورًا.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ninja import Schema


class PaymentOut(Schema):
    """
    الشكل العام (العميل مالك الحجز).

    🔒 provider_reference غير معرَّف هنا عمدًا — غائب من JSON كليًا.
    """

    id: uuid.UUID
    booking_id: uuid.UUID
    amount: Decimal
    method: str
    status: str
    failure_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class PaymentAdminOut(PaymentOut):
    """
    شكل الإدارة — يضيف المرجع التشخيصي وحده.

    الوراثة من PaymentOut تضمن ألا ينحرف الشكلان.
    """

    # قد تكون None بشكل مشروع (دفعة فاشلة) — والمفتاح يظهر رغم ذلك
    provider_reference: Optional[str] = None


class ErrorOut(Schema):
    """نفس شكل الخطأ المستخدم في بقية النطاقات."""

    code: str
    detail: str
