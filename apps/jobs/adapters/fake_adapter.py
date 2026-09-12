"""
FakeStorageAdapter — للتطوير والاختبار فقط.

⚠️ لا يخزّن شيئًا إطلاقًا: محتوى الملف يُهمَل (discard) ولا يُكتب على قرص
   ولا يُرسل لأي مزوّد. الغرض الوحيد إثبات أن تدفّق النطاق يعمل — لا
   معالجة ملفات حقيقية (Infra §7).

⚠️ get_signed_url يعيد رابطًا وهميًا واضح الزيف (نطاق .local) حتى لا
   يُخلط برابط موقَّع حقيقي في أي بيئة.

🔒 أمان (نفس نمط FakePaymentAdapter و DevConsoleSMSAdapter — §31):
   يرفض العمل عند DEBUG=False ما لم يُفعَّل JOBS_ALLOW_FAKE_STORAGE_ADAPTER
   صراحةً، حتى لا يظن نظام إنتاجي أن الصور محفوظة وهي مُهمَلة.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import BaseStorageProviderAdapter, StorageUploadResult

# تخمين امتداد بسيط — تجميلي فقط، لا يُعتمد عليه في أي منطق.
_EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
}

FAKE_URL_PREFIX = "https://fake-storage.local/signed/"


class FakeStorageAdapter(BaseStorageProviderAdapter):
    """يولّد مراجع وروابط مُصطنعة دون تخزين أي بايت."""

    def __init__(self):
        allow_fake = getattr(settings, "JOBS_ALLOW_FAKE_STORAGE_ADAPTER", False)
        if not settings.DEBUG and not allow_fake:
            raise ImproperlyConfigured(
                "FakeStorageAdapter لا يخزّن الملفات فعليًا ولا يجوز استخدامه خارج "
                "بيئة التطوير/الاختبار. اضبط JOB_STORAGE_ADAPTER_CLASS على تنفيذ "
                "حقيقي، أو فعّل JOBS_ALLOW_FAKE_STORAGE_ADAPTER=True صراحةً."
            )

    def upload(self, file_bytes, content_type, path_hint):
        """
        ⚠️ file_bytes يُهمَل عمدًا — لا يُكتب ولا يُرسل. هذه المرحلة تثبت
           تدفّق النطاق لا معالجة الملفات.
        """
        extension = _EXTENSION_BY_CONTENT_TYPE.get(
            (content_type or "").lower(), ".bin"
        )
        storage_key = f"fake/{path_hint}/{uuid.uuid4()}{extension}".replace("//", "/")

        return StorageUploadResult(
            storage_key=storage_key,
            signed_url=self.get_signed_url(storage_key),
        )

    def get_signed_url(self, storage_key):
        """رابط وهمي واضح الزيف — ليس رابطًا موقَّعًا حقيقيًا."""
        return f"{FAKE_URL_PREFIX}{storage_key}"
