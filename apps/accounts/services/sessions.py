"""
إبطال الجلسات — المصدر الوحيد.

يُستعمل من: إيقاف حساب (الـAPI ولوحة Django)، تغيير كلمة السر وإعادة
تعيينها، تعطيل أدمن أو تغيير هاتفه، و"تسجيل الخروج من كل مكان".
"""

from django.utils import timezone

from ..models import TrustedDevice


def revoke_refresh_tokens(user):
    """
    يضع كل refresh token سارٍ للحساب في القائمة السوداء. يعيد العدد الجديد.

    📌 OutstandingToken يُسجَّل عند كل إصدار (ninja_jwt.token_blacklist)،
       فهذا يغطي كل الأجهزة. access token الحالي ينتهي خلال دقائق، والحساب
       الموقوف يُرفض فورًا في كل طلب (ActiveUserJWTAuth).
    """
    from ninja_jwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    revoked = 0
    for token in OutstandingToken.objects.filter(
        user=user, expires_at__gt=timezone.now(), blacklistedtoken__isnull=True
    ):
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        revoked += int(created)
    return revoked


def revoke_trusted_devices(user):
    """يُبطل كل جهاز موثوق سارٍ (revoked_at) — لا حذف، فيبقى الأثر."""
    return TrustedDevice.objects.filter(user=user, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )


def revoke_all_sessions(user):
    """توكنات refresh + الأجهزة الموثوقة معًا. يعيد (tokens, devices)."""
    return revoke_refresh_tokens(user), revoke_trusted_devices(user)
