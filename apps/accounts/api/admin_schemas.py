"""عقود مصادقة الإدارة وإدارة حسابات الأدمن."""

import uuid
from datetime import datetime
from typing import Optional

from ninja import Schema
from pydantic import Field

from .schemas import TokenPairOut


class AdminLoginIn(Schema):
    # البريد أو الهاتف
    identifier: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=256)
    # توكن جهاز موثوق من دخول سابق — يعفي من رمز SMS حتى انتهائه
    device_token: Optional[str] = Field(None, max_length=128)


class AdminLoginOut(Schema):
    """
    state = "authenticated" → tokens موجودة (جهاز موثوق).
    state = "otp_required"  → challenge_id + phone_hint؛ أكمل بـ /verify.
    """

    state: str
    tokens: Optional[TokenPairOut] = None
    challenge_id: Optional[uuid.UUID] = None
    phone_hint: str = ""
    expires_in_seconds: Optional[int] = None


class AdminVerifyIn(Schema):
    challenge_id: uuid.UUID
    code: str = Field(..., min_length=4, max_length=12)
    remember_device: bool = False
    device_label: str = Field("", max_length=255)


class AdminVerifyOut(Schema):
    tokens: TokenPairOut
    # يُعاد مرة واحدة إن طُلب تذكّر الجهاز — يخزّنه الموقع ويرسله مع الدخول التالي
    device_token: Optional[str] = None
    device_expires_in_days: Optional[int] = None
    must_change_password: bool = False


class AdminResendIn(Schema):
    challenge_id: uuid.UUID


class PasswordChangeIn(Schema):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=1, max_length=256)


class AdminMeOut(Schema):
    id: uuid.UUID
    email: Optional[str] = None
    phone: str
    full_name: str = ""
    status: str
    is_superuser: bool
    must_change_password: bool
    password_changed_at: Optional[datetime] = None
    last_login: Optional[datetime] = None


class TrustedDeviceOut(Schema):
    id: uuid.UUID
    label: str = ""
    ip_address: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None
    expires_at: datetime


class AdminAccountOut(AdminMeOut):
    failed_login_attempts: int = 0
    locked_until: Optional[datetime] = None
    date_joined: datetime


class AdminAccountCreateIn(Schema):
    email: str = Field(..., max_length=254)
    phone: str = Field(..., min_length=8, max_length=32)
    full_name: str = Field("", max_length=255)
    is_superuser: bool = False


class AdminAccountCreatedOut(Schema):
    admin: AdminAccountOut
    # 🔒 تُعرض مرة واحدة ولا تُخزَّن نصًا. يُجبَر الأدمن على تغييرها.
    temporary_password: str


class AdminAccountPatch(Schema):
    full_name: Optional[str] = Field(None, max_length=255)
    email: Optional[str] = Field(None, max_length=254)
    phone: Optional[str] = Field(None, max_length=32)
    status: Optional[str] = None
    is_superuser: Optional[bool] = None


class AdminAccountListOut(Schema):
    count: int
    items: list[AdminAccountOut]
