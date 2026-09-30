"""
Storage Provider Adapter — Abstract Interface ONLY (Infra §7)

📌 التنفيذ الحقيقي في s3_adapter.py وحده (R2 أو S3 — قرار 2026-09-30).
   هذا الملف عقد محايد، وبقية النطاق لا تستورد أي SDK.

الاختيار عبر settings.JOB_STORAGE_ADAPTER_CLASS (مسار نصي لكلاس) — نفس نمط
PAYMENT_PROVIDER_ADAPTER_CLASS و SMS_ADAPTER.

⚠️ أي تنفيذ مستقبلي يجب أن يدعم Signed URLs (Security Architecture §23):
   الملفات لا تُقدَّم عبر روابط دائمة مكشوفة.

📌 هذه هي واجهة التخزين الوحيدة. واجهة Phase 0 القديمة
   (adapters/storage/base.py) حُذفت: لم يكن لها مستورد، وتوقيعها مختلف.
"""

from abc import ABC, abstractmethod


class StorageUnavailableError(Exception):
    """المزوّد لم يقبل الرفع أو الحذف (شبكة، صلاحيات، bucket)."""


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

    def delete(self, storage_key: str) -> None:
        """
        يحذف ملفًا مخزَّنًا (صورة أزالها المقاول قبل إعلان الإنجاز).

        📌 ليس abstract عمدًا: الافتراضي لا يفعل شيئًا، فلا يُكسر أي adapter
           قائم. المزوّد الحقيقي يعيد تعريفه ليحذف الملف فعلًا.
        """
        return None
