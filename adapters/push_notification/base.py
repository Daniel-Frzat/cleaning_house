"""
Push Notification Adapter — Abstract Interface ONLY

🟢 Provider غير محسوم (Firebase أو مشابه). لا تنفيذ فعلي هنا.
"""

from abc import ABC, abstractmethod


class BasePushNotificationAdapter(ABC):
    @abstractmethod
    def send(self, device_token: str, payload: dict, *args, **kwargs):
        raise NotImplementedError("Push Notification Provider غير محسوم بعد")