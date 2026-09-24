"""
Directions adapter selection (§9).

الاختيار عبر settings.DIRECTIONS_PROVIDER_ADAPTER_CLASS، فلا تعرف طبقة
الـDomain أي مزوّد بعينه.
"""

from django.conf import settings
from django.utils.module_loading import import_string

from .base import BaseDirectionsProviderAdapter, DirectionsUnavailable, RouteResult


def get_directions_adapter() -> BaseDirectionsProviderAdapter:
    """يُنشأ في كل استدعاء ليعمل صمّام الأمان في __init__ كل مرة."""
    return import_string(settings.DIRECTIONS_PROVIDER_ADAPTER_CLASS)()


__all__ = [
    "BaseDirectionsProviderAdapter",
    "DirectionsUnavailable",
    "RouteResult",
    "get_directions_adapter",
]
