"""
DevConsoleSMSAdapter — تنفيذ محلي للتطوير/الاختبار فقط.

⚠️ هذا ليس Provider فعليًا. SMS Gateway Provider ما زال قرارًا مفتوحًا
   (🟢 غير معطِّل) — راجع Change Set / Open Decisions.

الغرض الوحيد: تمكين تدفق OTP محليًا دون إرسال SMS حقيقي، عبر طباعة/تسجيل
الرمز في الـconsole. يُختار عبر settings.SMS_ADAPTER فقط، بحيث يمكن استبداله
بـProvider حقيقي لاحقًا دون تعديل أي كود في طبقة الـDomain.

🔒 أمان: هذا الـAdapter يطبع الرمز الخام. لذلك يرفض العمل عندما
   DEBUG=False ما لم يُسمح بذلك صراحةً عبر SMS_DEV_ALLOW_INSECURE=True،
   حتى لا يُسرَّب رمز حقيقي في سجلات بيئة إنتاجية بالخطأ.
"""

import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import BaseSMSAdapter

logger = logging.getLogger(__name__)


class DevConsoleSMSAdapter(BaseSMSAdapter):
    """يسجّل الرمز في الـconsole بدلًا من إرساله عبر شبكة SMS حقيقية."""

    def __init__(self):
        allow_insecure = getattr(settings, "SMS_DEV_ALLOW_INSECURE", False)
        if not settings.DEBUG and not allow_insecure:
            raise ImproperlyConfigured(
                "DevConsoleSMSAdapter يطبع رمز OTP الخام ولا يجوز استخدامه خارج "
                "بيئة التطوير. اضبط SMS_ADAPTER على Provider حقيقي، أو فعّل "
                "SMS_DEV_ALLOW_INSECURE=True صراحةً إذا كانت هذه بيئة اختبار مقصودة."
            )

    def send_otp(self, phone_number: str, code: str, *args, **kwargs):
        """
        يطبع الرمز محليًا. يعيد dict بسيط كإيصال إرسال (شكل محايد لا يفترض
        أي Provider معيّن).
        """
        logger.info("[DEV SMS] OTP for %s -> %s", phone_number, code)
        print(f"[DEV SMS] OTP for {phone_number} -> {code}")
        return {"provider": "dev-console", "phone_number": phone_number, "delivered": True}
