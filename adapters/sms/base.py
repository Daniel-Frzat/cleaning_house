"""
SMS Adapter — Abstract Interface ONLY

🟢 Provider غير محسوم (SMS Gateway). لا تنفيذ فعلي هنا.

📌 عقد الأخطاء أُضيف هنا لا في ملف الـProvider: طبقة الـDomain تلتقط
   SMSDeliveryError دون أن تعرف أي مزوّد رفع الاستثناء. لو عاش العقد في
   clicksend.py لاضطر أي مستدعٍ إلى استيراد المزوّد مباشرةً — وهو بالضبط
   ما تمنعه قاعدة §21.

⚠️ التمييز بين retryable و non-retryable هو *تصنيف* لا *سياسة*: الـAdapter
   يقول إن كان الخطأ يستحق إعادة المحاولة، ومن يقرر الإعادة فعليًا هو
   المستدعي. لا إعادة محاولة داخل أي Adapter.
"""

from abc import ABC, abstractmethod


class SMSDeliveryError(Exception):
    """أصل كل أخطاء إرسال SMS — يرفعه أي Adapter مهما كان المزوّد.

    🔒 أمان: نص هذا الاستثناء يظهر في السجلات وتتبّع الأخطاء. لا تضع فيه
       أبدًا اسم مستخدم المزوّد ولا مفتاح الـAPI ولا جسم الاستجابة الخام.
    """

    #: هل يستحق الخطأ إعادة محاولة لاحقة؟ يدهسها الأبناء.
    retryable = False


class SMSAuthError(SMSDeliveryError):
    """اعتماد المزوّد مرفوض (401/403).

    ⚠️ غير قابل لإعادة المحاولة عمدًا: مفتاح خاطئ سيبقى خاطئًا مهما
       أُعيدت المحاولة، وإعادتها تُهدر الرسائل وقد تُقفل الحساب.
    """

    retryable = False


class SMSTransientError(SMSDeliveryError):
    """عطل عابر: شبكة، مهلة، أو 5xx من المزوّد.

    📌 قابل لإعادة المحاولة — لكن القرار للمستدعي لا للـAdapter.
    """

    retryable = True


class SMSRejectedError(SMSDeliveryError):
    """المزوّد رفض الطلب نفسه: رقم غير صالح أو جسم مُشوَّه (4xx عدا 401/403).

    ⚠️ غير قابل لإعادة المحاولة: الطلب ذاته معيب، وتكراره يُنتج الرفض نفسه.
    """

    retryable = False


class BaseSMSAdapter(ABC):
    @abstractmethod
    def send_otp(self, phone_number: str, code: str, *args, **kwargs):
        raise NotImplementedError("SMS Gateway Provider غير محسوم بعد")