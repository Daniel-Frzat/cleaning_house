"""
Storage Provider Adapter — Abstract Interface ONLY (Infra §7)

🟢 مزوّد التخزين (S3 أو مشابه) قرار مفتوح. لا تنفيذ فعلي هنا ولا في أي
   مكان في كود الإنتاج: لا boto3، ولا S3 SDK، ولا كتابة ملفات على القرص
   كمسار إنتاجي.

الاختيار عبر settings.JOB_STORAGE_ADAPTER_CLASS (مسار نصي لكلاس) — نفس نمط
PAYMENT_PROVIDER_ADAPTER_CLASS و SMS_ADAPTER.

⚠️ أي تنفيذ مستقبلي يجب أن يدعم Signed URLs (Security Architecture §23):
   الملفات لا تُقدَّم عبر روابط دائمة مكشوفة.

⚠️ هذه الواجهة مستقلة عن adapters/storage/base.py من Phase 0: تلك أقدم
   وبتوقيع مختلف (upload(file)، generate_signed_url) ولا مستورد لها.
   المواصفة هنا تحدّد توقيعًا مختلفًا صراحةً، فلم تُعدَّل تلك حتى لا
   يتغيّر ملف Phase 0 دون طلب.
"""

from abc import ABC, abstractmethod


class StorageUploadResult:
    """
    نتيجة رفع ملف — شكل محايد لا يخص مزوّدًا بعينه.

    storage_key : مرجع مبهم يُخزَّن في قاعدة البيانات ولا يُفسَّر.
    signed_url  : رابط مؤقّت اختياري إن أعاده المزوّد عند الرفع.
    """

    __slots__ = ("storage_key", "signed_url")

    def __init__(self, storage_key, signed_url=None):
        self.storage_key = storage_key
        self.signed_url = signed_url

    def __repr__(self):
        return (
            f"StorageUploadResult(storage_key={self.storage_key!r}, "
            f"signed_url={self.signed_url!r})"
        )


class BaseStorageProviderAdapter(ABC):
    """
    واجهة مزوّد التخزين — تجريدية بالكامل.

    ⚠️ لا تُنفَّذ هنا. أي تنفيذ حقيقي يُضاف في مرحلته الخاصة بعد حسم
       المزوّد، دون تعديل طبقة الـDomain.
    """

    @abstractmethod
    def upload(
        self, file_bytes: bytes, content_type: str, path_hint: str
    ) -> StorageUploadResult:
        """يرفع محتوى الملف ويعيد مرجعًا مبهمًا."""
        raise NotImplementedError("Storage Provider غير محسوم بعد.")

    @abstractmethod
    def get_signed_url(self, storage_key: str) -> str:
        """يعيد رابطًا مؤقّتًا للوصول إلى ملف مخزَّن."""
        raise NotImplementedError("Storage Provider غير محسوم بعد.")
