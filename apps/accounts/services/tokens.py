"""
JWT Token Service — Identity Domain (Phase 1)

مكان واحد لإصدار التوكنات، حتى تكون الـclaims موحّدة عبر كل مسارات الدخول
(OTP / Apple / Google).

⚠️ لا يوجد هنا تسجيل دخول بكلمة مرور — نموذج المصادقة المعتمد هو
   OTP + Apple + Google + JWT فقط (Change Set — قسم 21).
"""

import logging

from django.contrib.auth import get_user_model
from ninja_jwt.exceptions import TokenError
from ninja_jwt.settings import api_settings
from ninja_jwt.tokens import RefreshToken

from ..models import UserStatus

logger = logging.getLogger(__name__)


class TokenRefreshError(Exception):
    """توكن refresh غير صالح أو منتهٍ أو مُبطَل، أو صاحبه لم يعد نشطًا."""

    code = "token_not_valid"


def issue_tokens_for_user(user):
    """
    يصدر زوج access/refresh للمستخدم.

    يضيف claims إضافية (role, status, phone) لأن الـDomains اللاحقة
    (Properties, Bookings) تحتاج الدور للتحقق من الصلاحيات دون استعلام
    قاعدة بيانات في كل طلب.

    ملاحظة: الـclaims الموضوعة على refresh token تُنسخ تلقائيًا إلى
    access token (راجع RefreshToken.access_token).

    🔒 لا تُوضع في الـclaims أي بيانات حسّاسة (لا رموز OTP، ولا
       raw_profile من المزوّد).
    """
    refresh = RefreshToken.for_user(user)

    refresh["role"] = user.role
    refresh["status"] = user.status
    refresh["phone"] = user.phone

    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


def refresh_tokens(raw_refresh):
    """
    يستبدل refresh صالحًا بزوج جديد (تدوير).

    🔒 القديم يُضاف للقائمة السوداء فورًا: إعادة استخدامه ترفض. والحساب
       يُقرأ من قاعدة البيانات، فالحساب الموقوف لا يجدد توكنه، والـclaims
       (role/status) تُبنى من الحالة الحالية لا من التوكن القديم.
    """
    try:
        old = RefreshToken(raw_refresh)
    except TokenError as exc:
        raise TokenRefreshError("Refresh token is invalid, expired or revoked.") from exc

    user_id = old.get(api_settings.USER_ID_CLAIM)
    user = get_user_model().objects.filter(
        **{api_settings.USER_ID_FIELD: user_id}
    ).first()
    if user is None or not user.is_active or user.status != UserStatus.ACTIVE:
        raise TokenRefreshError("This account is not active.")

    # created=False يعني أن طلبًا متزامنًا أبطله قبلنا — يُرفض هذا الطلب
    _, created = old.blacklist()
    if not created:
        raise TokenRefreshError("Refresh token is invalid, expired or revoked.")

    return issue_tokens_for_user(user)


def revoke_refresh_token(raw_refresh):
    """
    تسجيل خروج: يُبطل refresh token فلا يمكن تجديده بعد الآن.

    📌 access token الحالي يبقى صالحًا حتى انتهائه القصير
       (JWT_ACCESS_TOKEN_LIFETIME_MIN) — طبيعة JWT عديم الحالة. الواجهة
       تحذفه محليًا عند الخروج.

    Idempotent: إبطال توكن مُبطل مسبقًا لا يُعد خطأ.

    يعيد صاحب التوكن (لإلغاء تسجيل جهازه)، أو None إن كان التوكن غير صالح.
    """
    try:
        token = RefreshToken(raw_refresh)
    except TokenError:
        # منتهٍ أو مُبطل مسبقًا أو غير صالح: لا شيء يمكن تجديده به — الخروج تم
        return None
    token.blacklist()
    return get_user_model().objects.filter(
        **{api_settings.USER_ID_FIELD: token.get(api_settings.USER_ID_CLAIM)}
    ).first()
