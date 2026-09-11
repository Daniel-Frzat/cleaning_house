"""
Payment Provider Adapters — Payment Domain.

الاختيار عبر settings.PAYMENT_PROVIDER_ADAPTER_CLASS (مسار نصي لكلاس)،
بحيث لا تعرف طبقة الـDomain أي مزوّد بعينه — نفس نمط adapters/sms
و adapters/social_auth من Phase 0.
"""

from django.conf import settings
from django.utils.module_loading import import_string

from .base import BasePaymentProviderAdapter, PaymentChargeResult

__all__ = [
    "BasePaymentProviderAdapter",
    "PaymentChargeResult",
    "get_payment_adapter",
]


def get_payment_adapter():
    """يُنشئ الـAdapter المُعرَّف في settings.PAYMENT_PROVIDER_ADAPTER_CLASS."""
    return import_string(settings.PAYMENT_PROVIDER_ADAPTER_CLASS)()
