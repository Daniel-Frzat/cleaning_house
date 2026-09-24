"""
Social Auth Adapter — Abstract Interface ONLY

Sign in with Apple / Google متطلبان مؤكدان (SOURCE-confirmed) في Phase 1،
لكن التحقق الفعلي من التوكن يتطلب إعدادات تشغيلية (Apple Developer keys،
Google OAuth client IDs) تُدار عبر متغيرات بيئة — خارج نطاق هذه الخطوة.

لذلك هنا العقد (contract) فقط. أي تنفيذ حقيقي لاحق يلتزم بنفس التوقيع
دون تعديل كود الـDomain.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderProfile:
    """
    نتيجة التحقق من توكن المزوّد — شكل محايد لا يفترض أي مزوّد بعينه.

    provider_user_id: المعرّف المستقر للمستخدم لدى المزوّد (sub في Apple/Google).
                      هذا هو مفتاح الهوية، وليس البريد (البريد قد يتغير أو يُخفى).
    email:            كما أبلغ عنه المزوّد. قد يكون None (Apple Private Relay
                      أو مستخدم رفض مشاركة البريد).
    email_verified:   هل أكّد المزوّد ملكية البريد (claim email_verified في
                      OIDC). 🔒 التنفيذ الحقيقي يجب أن ينقله كما هو — الربط
                      بحساب قائم عبر البريد يعتمد عليه.
    raw:              الحمولة الخام للتدقيق/التشخيص.
    """

    provider: str
    provider_user_id: str
    email: str = None
    email_verified: bool = False
    full_name: str = ""
    raw: dict = field(default_factory=dict)


class SocialAuthError(Exception):
    """فشل التحقق من توكن المزوّد."""

    code = "social_auth_failed"


class BaseSocialAuthAdapter(ABC):
    """Contract مجرد — لا تنفيذ فعلي لأي مزوّد هنا."""

    @abstractmethod
    def verify_token(self, provider: str, token: str) -> ProviderProfile:
        """
        يتحقق من توكن المزوّد ويعيد ProviderProfile.

        يجب أن يرفع SocialAuthError عند التوكن غير الصالح/المنتهي.
        """
        raise NotImplementedError(
            "التحقق الفعلي من Apple/Google يتطلب إعدادات تشغيلية غير مضبوطة بعد"
        )
