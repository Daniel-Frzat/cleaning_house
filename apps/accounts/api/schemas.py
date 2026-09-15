"""
API Schemas — Identity Domain (Phase 1)

عقود الطلب/الاستجابة. مبنية يدويًا (لا ModelSchema) حتى لا تتسرّب حقول
داخلية عن طريق الخطأ عند تغيّر الـModels لاحقًا.

🔒 لا تحتوي أي استجابة هنا على:
    - رمز OTP أو code_hash أو attempts_count
    - raw_profile من مزوّد الدخول الاجتماعي
"""

import uuid
from typing import Optional

from ninja import Schema
from pydantic import Field


# ------------------------------------------------------------
# الطلبات
# ------------------------------------------------------------
class OTPRequestIn(Schema):
    phone: str = Field(..., min_length=4, max_length=32)


class OTPVerifyIn(Schema):
    phone: str = Field(..., min_length=4, max_length=32)
    code: str = Field(..., min_length=4, max_length=12)


class SocialLoginIn(Schema):
    token: str = Field(..., min_length=1)


# ------------------------------------------------------------
# الاستجابات
# ------------------------------------------------------------
class OTPRequestOut(Schema):
    """
    🔒 لا يحتوي الرمز ولا أي تفصيل عن سجل التحقق.
    expires_in_seconds مشتق من السياسة وليس من السجل نفسه.
    """

    detail: str
    expires_in_seconds: int


class TokenPairOut(Schema):
    access: str
    refresh: str


class UserOut(Schema):
    """
    بيانات المستخدم الحالي.

    📌 full_name و email مكشوفان للمالك نفسه فقط — هذا الشكل لا يُستخدم
       إلا في /auth/me وفي رد الدخول، وكلاهما يخص صاحب التوكن.

    🔒 لا حقول امتياز أخرى: is_staff و is_superuser وgroups لا تُكشف
       إطلاقًا، ولا password ولا last_login.
    """

    id: uuid.UUID
    # ⚠️ الدور الأساسي — يبقى للتوافق الرجعي. الفرونت يقرأ roles لا هذا:
    #    حساب بصفتين يعيد role="CUSTOMER" بينما roles تحوي الاثنين.
    role: str
    phone: str
    status: str
    # قد يكون "" (لم يُدخله المستخدم بعد)
    full_name: str = ""
    # قد يكون None (اختياري، ويُخزَّن NULL لا "")
    email: Optional[str] = None

    # ------------------------------------------------------------
    # الصلاحيات والأوضاع — للفرونت
    # ------------------------------------------------------------
    # 📌 كل صلاحيات الحساب الفعلية. ADMIN يُعاد وحده (حصري).
    roles: list[str] = []
    # حالة الانضمام كعامل — مشتقّة لحظيًا، غير مخزَّنة
    contractor_status: str = "NONE"
    # الأوضاع المتاحة للتبديل بينها في الواجهة
    # ⚠️ عرض فقط: لا تمنح صلاحية، ولا يُخزَّن الوضع المختار في الباكند.
    available_modes: list[str] = []


class UserProfilePatch(Schema):
    """
    تعديل ذاتي للملف الشخصي.

    ⚠️ حقلان لا ثالث لهما. phone و role و status غير موجودة عمدًا:
       الأول معرّف الدخول (يحتاج تحقق OTP)، والباقيان حقول امتياز تُدار
       من الإدارة — قبولهما هنا كان سيتيح تصعيد صلاحيات.

    📌 التعديل جزئي: الحقل غير المُرسَل لا يُمس. إرسال email فارغًا يمسح
       البريد (يصبح NULL)، وهو سلوك مقصود لا خطأ.
    """

    full_name: Optional[str] = Field(None, max_length=255)
    email: Optional[str] = Field(None, max_length=254)


class AuthOut(Schema):
    """نتيجة أي مسار دخول ناجح (OTP أو Social)."""

    tokens: TokenPairOut
    user: UserOut


class ErrorOut(Schema):
    """شكل موحّد للأخطاء عبر كل نقاط النهاية."""

    code: str
    detail: str
    # يُستخدم فقط مع أخطاء التهدئة/الحد
    retry_after_seconds: int = None
