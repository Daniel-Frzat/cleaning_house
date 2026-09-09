"""
FakeSocialAuthAdapter — للاختبارات والتطوير المحلي فقط.

⚠️ لا يتحقق من أي توكن حقيقي. يعيد ملفًا شخصيًا قابلًا للتحكم، ليتمكن
   منطق الـDomain (ربط الحسابات) من الاختبار دون استدعاءات شبكة.

الاستخدام في الاختبارات:
    FakeSocialAuthAdapter.register_token("tok-1", ProviderProfile(...))
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import BaseSocialAuthAdapter, ProviderProfile, SocialAuthError


class FakeSocialAuthAdapter(BaseSocialAuthAdapter):
    """
    يحوّل التوكنات المسجَّلة مسبقًا إلى ProviderProfile.

    🔒 يرفض العمل عند DEBUG=False ما لم يُسمح صراحةً عبر
       SOCIAL_AUTH_ALLOW_FAKE=True — حتى لا يقبل نظام إنتاجي توكنات وهمية.
    """

    # {token: ProviderProfile}
    _tokens = {}

    def __init__(self):
        allow_fake = getattr(settings, "SOCIAL_AUTH_ALLOW_FAKE", False)
        if not settings.DEBUG and not allow_fake:
            raise ImproperlyConfigured(
                "FakeSocialAuthAdapter يقبل توكنات وهمية ولا يجوز استخدامه خارج "
                "بيئة التطوير/الاختبار. اضبط SOCIAL_AUTH_ADAPTER على تنفيذ حقيقي، "
                "أو فعّل SOCIAL_AUTH_ALLOW_FAKE=True صراحةً."
            )

    # -------- أدوات التحكم في الاختبارات --------
    @classmethod
    def register_token(cls, token: str, profile: ProviderProfile):
        cls._tokens[token] = profile
        return profile

    @classmethod
    def reset(cls):
        cls._tokens = {}

    # -------- العقد --------
    def verify_token(self, provider: str, token: str) -> ProviderProfile:
        profile = self._tokens.get(token)
        if profile is None:
            raise SocialAuthError(f"Invalid or unknown token for provider {provider}.")
        if profile.provider != provider:
            raise SocialAuthError(
                f"Token belongs to provider {profile.provider}, not {provider}."
            )
        return profile
