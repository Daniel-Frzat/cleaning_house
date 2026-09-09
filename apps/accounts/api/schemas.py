"""
API Schemas — Identity Domain (Phase 1)

عقود الطلب/الاستجابة. مبنية يدويًا (لا ModelSchema) حتى لا تتسرّب حقول
داخلية عن طريق الخطأ عند تغيّر الـModels لاحقًا.

🔒 لا تحتوي أي استجابة هنا على:
    - رمز OTP أو code_hash أو attempts_count
    - raw_profile من مزوّد الدخول الاجتماعي
"""

import uuid

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
    id: uuid.UUID
    phone: str
    role: str
    status: str


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
