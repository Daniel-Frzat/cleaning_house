"""
Identity Resolution — Identity Domain (Phase 1)

حل هوية المستخدم بعد نجاح التحقق (OTP)، بنفس قاعدة Social Login:
    المستخدم الجديد يُنشأ بدور CUSTOMER فقط.

⚠️ قرار محسوم: أدوار CONTRACTOR/ADMIN لا تُمنح عبر التسجيل الذاتي —
   تتطلب مسارات تحقق منفصلة لم تُبنَ بعد.
"""

import logging

from django.db import transaction

from ..models import User, UserStatus
from ..roles import ConfirmedRole

logger = logging.getLogger(__name__)


class InactiveUserError(Exception):
    """الحساب موجود لكنه معطّل/موقوف."""

    code = "inactive_user"


@transaction.atomic
def get_or_create_user_by_phone(phone):
    """
    يعيد (user, created) للرقم المُتحقَّق منه عبر OTP.

    يُنشئ الحساب عند أول تحقق ناجح فقط.
    """
    phone = phone.strip()
    user = User.objects.filter(phone=phone).first()

    if user is None:
        user = User.objects.create_user(phone=phone, role=ConfirmedRole.CUSTOMER)
        logger.info("User created after first OTP verification (user_id=%s)", user.id)
        return user, True

    assert_user_can_login(user)
    return user, False


def assert_user_can_login(user):
    """يمنع منح دخول لحساب معطّل أو موقوف."""
    if not user.is_active or user.status != UserStatus.ACTIVE:
        raise InactiveUserError("This account is not active.")
