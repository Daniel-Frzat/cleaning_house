"""
File Storage Adapter — Abstract Interface ONLY

🟢 Provider غير محسوم (S3 أو مشابه). لا تنفيذ فعلي هنا.
يجب أن يدعم أي تنفيذ مستقبلي Signed URLs (Security Architecture §23).
"""

from abc import ABC, abstractmethod


class BaseStorageAdapter(ABC):
    @abstractmethod
    def upload(self, file, *args, **kwargs):
        raise NotImplementedError("File Storage Provider غير محسوم بعد")

    @abstractmethod
    def generate_signed_url(self, file_key: str, *args, **kwargs):
        raise NotImplementedError("File Storage Provider غير محسوم بعد")