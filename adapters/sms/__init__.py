"""
SMS Adapter Layer.

الاختيار يتم عبر settings.SMS_ADAPTER (مسار نصي لكلاس)، بحيث لا تعرف
طبقة الـDomain أي Provider مستخدم.
"""

from django.conf import settings
from django.utils.module_loading import import_string

from .base import BaseSMSAdapter

__all__ = ["BaseSMSAdapter", "get_sms_adapter"]


def get_sms_adapter() -> BaseSMSAdapter:
    """يُنشئ الـAdapter المُعرَّف في settings.SMS_ADAPTER."""
    return import_string(settings.SMS_ADAPTER)()
