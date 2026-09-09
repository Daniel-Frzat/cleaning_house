"""
Social Auth Adapter Layer.

الاختيار عبر settings.SOCIAL_AUTH_ADAPTER، بحيث لا تعرف طبقة الـDomain
أي مزوّد أو أي آلية تحقق مستخدمة.
"""

from django.conf import settings
from django.utils.module_loading import import_string

from .base import BaseSocialAuthAdapter, ProviderProfile, SocialAuthError

__all__ = [
    "BaseSocialAuthAdapter",
    "ProviderProfile",
    "SocialAuthError",
    "get_social_auth_adapter",
]


def get_social_auth_adapter() -> BaseSocialAuthAdapter:
    """يُنشئ الـAdapter المُعرَّف في settings.SOCIAL_AUTH_ADAPTER."""
    return import_string(settings.SOCIAL_AUTH_ADAPTER)()
