"""
FakePushAdapter — للتطوير والاختبارات فقط.

يسجّل الرسائل في الذاكرة (وفي السجل) بدل إرسالها. التوكنات التي تبدأ بـ
"invalid-" تُعامل كميتة، و"flaky-" كخطأ مؤقت — لاختبار المسارين.

🔒 يرفض العمل عند DEBUG=False ما لم يُفعَّل PUSH_ALLOW_FAKE_ADAPTER صراحةً
   (نفس نمط بقية المحوّلات الوهمية): إنتاج بلا Firebase يجب أن يفشل
   بصوت عالٍ لا أن "ينجح" دون أن يصل شيء.
"""

import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import BasePushNotificationAdapter, PushResult

logger = logging.getLogger(__name__)


class FakePushAdapter(BasePushNotificationAdapter):
    sent = []

    def __init__(self):
        if not settings.DEBUG and not getattr(settings, "PUSH_ALLOW_FAKE_ADAPTER", False):
            raise ImproperlyConfigured(
                "FakePushAdapter لا يرسل شيئًا ولا يجوز استخدامه خارج التطوير/الاختبار. "
                "اضبط PUSH_ADAPTER على adapters.push_notification.fcm.FCMPushAdapter."
            )

    @classmethod
    def reset(cls):
        cls.sent = []

    def send(self, tokens, message):
        result = PushResult()
        for token in tokens:
            if token.startswith("invalid-"):
                result.invalid.append(token)
            elif token.startswith("flaky-"):
                result.failed[token] = "503 UNAVAILABLE"
            else:
                result.delivered.append(token)
                type(self).sent.append({"token": token, "message": message})
        logger.info("[DEV PUSH] %s → %d device(s)", message.title, len(result.delivered))
        return result
