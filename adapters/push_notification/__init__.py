"""Push Notification Adapter Layer — الاختيار عبر settings.PUSH_ADAPTER."""

from django.conf import settings
from django.utils.module_loading import import_string

from .base import BasePushNotificationAdapter, PushMessage, PushProviderError, PushResult

__all__ = [
    "BasePushNotificationAdapter",
    "PushMessage",
    "PushProviderError",
    "PushResult",
    "get_push_adapter",
]


def get_push_adapter() -> BasePushNotificationAdapter:
    return import_string(settings.PUSH_ADAPTER)()
