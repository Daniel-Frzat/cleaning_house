"""
ClickSendAdapter — تنفيذ فعلي لبوابة SMS عبر ClickSend (Change Set §21).

📌 هذا أول Provider حقيقي في طبقة الـSMS. يبقى خلف BaseSMSAdapter تمامًا:
   طبقة الـDomain تستدعي get_sms_adapter().send_otp(...) ولا تعرف — ولا
   يجوز أن تعرف — أن ClickSend هو من يرسل.

🔒 أمان الاعتماد (شرط صارم): CLICKSEND_USERNAME و CLICKSEND_API_KEY لا
   يظهران في أي سطر سجل، ولا في نص أي استثناء، ولا في أي تتبّع.
   لذلك:
     - الاعتماد يُمرَّر إلى requests عبر auth=(...) لا داخل رابط أو ترويسة
       نبنيها بأنفسنا، فلا يتسرّب في تمثيل نصي لكائن الطلب.
     - لا نُسجّل جسم الاستجابة الخام: ClickSend يُعيد صدى الرسالة المُرسَلة،
       فتسجيله يعني تسريب رمز OTP نفسه.
     - رسائل الاستثناءات تحمل رمز الحالة ووصفًا عامًا فقط.

🔒 أمان الرمز: رمز OTP الخام يمرّ من هنا. لا يُسجَّل إطلاقًا — لا في مسار
   النجاح ولا في مسار الفشل.

⚠️ لا إعادة محاولة هنا عمدًا. الفحص أثبت أن لا طبقة أعلى تملك سياسة إعادة
   محاولة لإرسال SMS: apps/accounts/services/otp.py يستدعي الـAdapter
   استدعاءً متزامنًا مباشرًا (لا shared_task، ولا celery في مسار OTP —
   celery مستخدم في apps/bookings/tasks.py وحده). وبما أن لا سياسة قائمة
   ولا إعداد backoff في settings، فاختراع عدد محاولات ومهلة تراجع هنا كان
   سيصنع سياسة ضمنية لا يراها أحد. البديل المتّبع: نصنّف الخطأ
   (SMSTransientError.retryable = True) ونرفعه، فيقرر المستدعي.
"""

import logging

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import (
    BaseSMSAdapter,
    SMSAuthError,
    SMSRejectedError,
    SMSTransientError,
)

logger = logging.getLogger(__name__)

SEND_URL = "https://rest.clicksend.com/v3/sms/send"

# مهلة صريحة: (مهلة الاتصال، مهلة القراءة) بالثواني. طلب بلا مهلة قد
# يُعلّق خيط الـWSGI إلى ما لا نهاية إذا تجمّد المزوّد.
DEFAULT_TIMEOUT = (5, 10)


class ClickSendAdapter(BaseSMSAdapter):
    """يرسل رمز OTP عبر ClickSend REST v3 باستخدام HTTP Basic Auth."""

    def __init__(self):
        # فشل مبكر وواضح: اعتماد ناقص يعني أن كل إرسال سيفشل لاحقًا برفض
        # 401 غامض. الأفضل أن يسقط الإنشاء برسالة تقول ما الناقص بالضبط.
        self._username = (getattr(settings, "CLICKSEND_USERNAME", "") or "").strip()
        self._api_key = (getattr(settings, "CLICKSEND_API_KEY", "") or "").strip()
        self._sender_id = (getattr(settings, "CLICKSEND_SENDER_ID", "") or "").strip()

        missing = [
            name
            for name, value in (
                ("CLICKSEND_USERNAME", self._username),
                ("CLICKSEND_API_KEY", self._api_key),
                ("CLICKSEND_SENDER_ID", self._sender_id),
            )
            if not value
        ]
        if missing:
            # 🔒 نذكر *أسماء* الإعدادات الناقصة لا قيم الموجود منها.
            raise ImproperlyConfigured(
                "ClickSendAdapter مُختار عبر SMS_ADAPTER لكن الإعدادات التالية "
                f"فارغة: {', '.join(missing)}. اضبطها عبر متغيرات البيئة — "
                "الإرسال باعتماد فارغ ممنوع."
            )

        self._timeout = getattr(settings, "SMS_HTTP_TIMEOUT", DEFAULT_TIMEOUT)

    def send_otp(self, phone_number: str, code: str, *args, **kwargs):
        """
        يرسل الرمز ويعيد إيصالًا محايد الشكل (نفس شكل DevConsoleSMSAdapter).

        يرفع عند الفشل واحدًا من عقد base.py:
          SMSAuthError      — 401/403، لا إعادة محاولة.
          SMSRejectedError  — 4xx أخرى (رقم غير صالح/طلب مُشوَّه)، لا إعادة.
          SMSTransientError — شبكة/مهلة/5xx، يستحق إعادة محاولة من المستدعي.
        """
        payload = {
            "messages": [
                {
                    "to": phone_number,
                    "body": self._build_body(code),
                    "from": self._sender_id,
                }
            ]
        }

        try:
            response = requests.post(
                SEND_URL,
                json=payload,
                # 🔒 الاعتماد يبقى داخل requests ولا يُبنى في ترويسة نُمسكها.
                auth=(self._username, self._api_key),
                timeout=self._timeout,
            )
        except requests.Timeout as exc:
            # 🔒 str(exc) من requests قد يحمل الرابط لكنه لا يحمل الاعتماد
            #    (الاعتماد ليس في الرابط). رغم ذلك لا نُمرّره: نكتب نصًا خاصًا بنا.
            raise SMSTransientError(
                "ClickSend request timed out."
            ) from None
        except requests.RequestException:
            # ⚠️ from None عمدًا: سلسلة الاستثناء الأصلية قد تحمل كائن الطلب
            #    في بعض المسارات، ونحن نضمن ألا يصل الاعتماد إلى أي تتبّع.
            raise SMSTransientError(
                "ClickSend request failed at the network layer."
            ) from None

        status = response.status_code

        if status in (401, 403):
            logger.error("ClickSend rejected credentials (HTTP %s).", status)
            raise SMSAuthError(
                f"ClickSend authentication failed (HTTP {status}). "
                "تحقّق من إعدادات الاعتماد — لا إعادة محاولة."
            )

        if 500 <= status < 600:
            logger.warning("ClickSend upstream error (HTTP %s).", status)
            raise SMSTransientError(
                f"ClickSend upstream error (HTTP {status})."
            )

        if 400 <= status < 500:
            # 🔒 لا نُسجّل ولا نرفع جسم الاستجابة: قد يُعيد صدى نص الرسالة
            #    (أي رمز OTP) إضافةً إلى بيانات الطلب.
            logger.warning("ClickSend rejected the request (HTTP %s).", status)
            raise SMSRejectedError(
                f"ClickSend rejected the message (HTTP {status}). "
                "رقم غير صالح أو طلب مُشوَّه — لا إعادة محاولة."
            )

        if status >= 300:
            raise SMSTransientError(
                f"Unexpected ClickSend response status (HTTP {status})."
            )

        # 🔒 لا نُسجّل الاستجابة ولا الرقم كاملًا ولا الرمز.
        logger.info("OTP dispatched via ClickSend (HTTP %s).", status)
        return {
            "provider": "clicksend",
            "phone_number": phone_number,
            "delivered": True,
        }

    def _build_body(self, code: str) -> str:
        """نص الرسالة. يبقى هنا لأنه شكل عرض لا قاعدة عمل."""
        return f"{code} is your Cleaning House verification code."
