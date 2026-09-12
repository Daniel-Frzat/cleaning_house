"""
Payout Provider Adapters — Payout Domain.

الاختيار عبر settings.PAYOUT_PROVIDER_ADAPTER_CLASS (مسار نصي لكلاس)،
بحيث لا تعرف طبقة الـDomain أي مزوّد بعينه — نفس نمط apps/payments/adapters
و apps/jobs/adapters.
"""

from django.conf import settings
from django.utils.module_loading import import_string

from .base import BasePayoutProviderAdapter, PayoutResult

__all__ = [
    "BasePayoutProviderAdapter",
    "PayoutResult",
    "get_payout_adapter",
]


def get_payout_adapter():
    """يُنشئ الـAdapter المُعرَّف في settings.PAYOUT_PROVIDER_ADAPTER_CLASS."""
    return import_string(settings.PAYOUT_PROVIDER_ADAPTER_CLASS)()
