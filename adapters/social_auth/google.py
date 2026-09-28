"""
GoogleSocialAuthAdapter — Sign in with Google (تحقق حقيقي من ID token).

التطبيق يحصل على ID token من Google SDK (google_sign_in مع serverClientId)
ويرسله كما هو في POST /api/auth/social/google {"token": "..."}.

🔒 التحقق كاملًا هنا، لا ثقة بأي حقل من التطبيق:
   - التوقيع بمفاتيح Google العامة (تُجلب من googleapis.com)
   - الصلاحية الزمنية (exp / iat)
   - المُصدِر iss = accounts.google.com
   - الجمهور aud = أحد GOOGLE_OAUTH_CLIENT_IDS — توكن صادر لتطبيق آخر
     يُرفض حتى لو كان توقيعه سليمًا.

📌 Apple غير مضبوط بعد: طلب APPLE يُرفض برمز provider_not_configured بدل
   أن ينهار بخطأ 500.

الإعداد:
    SOCIAL_AUTH_ADAPTER=adapters.social_auth.google.GoogleSocialAuthAdapter
    GOOGLE_OAUTH_CLIENT_IDS=<client id>[,<client id>...]
"""

import logging

from django.conf import settings

from .base import BaseSocialAuthAdapter, ProviderProfile, SocialAuthError

logger = logging.getLogger(__name__)

GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class ProviderNotConfiguredError(SocialAuthError):
    """This sign-in provider is not configured on the server yet."""

    code = "provider_not_configured"


def _truthy(value):
    # Google يعيد email_verified أحيانًا نصًا "true"
    return value is True or str(value).lower() == "true"


class GoogleSocialAuthAdapter(BaseSocialAuthAdapter):
    def __init__(self):
        self.client_ids = list(getattr(settings, "GOOGLE_OAUTH_CLIENT_IDS", []) or [])

    def verify_token(self, provider: str, token: str) -> ProviderProfile:
        if provider != "GOOGLE":
            raise ProviderNotConfiguredError(f"Sign in with {provider.title()} is not configured yet.")
        if not self.client_ids:
            logger.error("GOOGLE_OAUTH_CLIENT_IDS is empty — Google sign-in cannot verify tokens.")
            raise ProviderNotConfiguredError("Sign in with Google is not configured yet.")

        from google.auth import exceptions as google_exceptions
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        try:
            claims = id_token.verify_oauth2_token(token, Request(), audience=self.client_ids)
        except (ValueError, google_exceptions.GoogleAuthError) as exc:
            # توقيع/صلاحية/جمهور غير صالح — لا نكشف التفاصيل للعميل
            logger.info("Google token rejected: %s", exc)
            raise SocialAuthError("Invalid Google token.") from exc

        if claims.get("iss") not in GOOGLE_ISSUERS:
            raise SocialAuthError("Invalid Google token issuer.")
        subject = claims.get("sub")
        if not subject:
            raise SocialAuthError("Google token has no subject.")

        return ProviderProfile(
            provider="GOOGLE",
            provider_user_id=str(subject),
            email=claims.get("email"),
            email_verified=_truthy(claims.get("email_verified")),
            full_name=claims.get("name") or "",
            raw={k: claims.get(k) for k in ("sub", "email", "email_verified", "aud", "iss", "exp")},
        )
