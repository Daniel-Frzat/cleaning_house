"""
Push Notification Adapter — العقد.

الاختيار عبر settings.PUSH_ADAPTER (مسار نصي)، فلا تعرف طبقة الـDomain
أي مزوّد مستخدم. المزوّد المعتمد: Firebase Cloud Messaging (fcm.py).

⚠️ النتيجة لكل توكن على حدة: التوكن الذي يرفضه المزوّد نهائيًا (تطبيق
   محذوف، توكن مُستبدل) يُحذف من قاعدة البيانات؛ الخطأ المؤقت يُعاد
   لاحقًا. الخلط بينهما إما يحذف أجهزة سليمة أو يكرر الإرسال لأجهزة ميتة.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class PushProviderError(Exception):
    """المزوّد غير مُهيّأ أو غير متاح — لا نتيجة لأي توكن."""


@dataclass
class PushMessage:
    title: str
    body: str
    # قيم نصية فقط (قيد FCM على حقل data)
    data: dict = field(default_factory=dict)
    # "high" للعروض (تصل فورًا حتى في وضع توفير الطاقة)، "normal" لغيرها
    priority: str = "normal"
    android_channel_id: str = ""


@dataclass
class PushResult:
    delivered: list = field(default_factory=list)   # توكنات وصلت
    invalid: list = field(default_factory=list)     # توكنات ميتة — تُحذف
    failed: dict = field(default_factory=dict)      # توكن → سبب خطأ مؤقت


class BasePushNotificationAdapter(ABC):
    @abstractmethod
    def send(self, tokens: list, message: PushMessage) -> PushResult:
        """يرسل الرسالة لكل توكن ويعيد النتيجة لكل واحد."""
        raise NotImplementedError
