"""
Storage Provider Adapters — Jobs Domain.

الاختيار عبر settings.JOB_STORAGE_ADAPTER_CLASS (مسار نصي لكلاس)، بحيث لا
تعرف طبقة الـDomain أي مزوّد بعينه — نفس نمط apps/payments/adapters.
"""

from django.conf import settings
from django.utils.module_loading import import_string

from .base import BaseStorageProviderAdapter, StorageUploadResult

__all__ = [
    "BaseStorageProviderAdapter",
    "StorageUploadResult",
    "get_storage_adapter",
]


def get_storage_adapter():
    """يُنشئ الـAdapter المُعرَّف في settings.JOB_STORAGE_ADAPTER_CLASS."""
    return import_string(settings.JOB_STORAGE_ADAPTER_CLASS)()
