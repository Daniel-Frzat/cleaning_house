"""
SMS Adapter — Abstract Interface ONLY

🟢 Provider غير محسوم (SMS Gateway). لا تنفيذ فعلي هنا.
"""

from abc import ABC, abstractmethod


class BaseSMSAdapter(ABC):
    @abstractmethod
    def send_otp(self, phone_number: str, code: str, *args, **kwargs):
        raise NotImplementedError("SMS Gateway Provider غير محسوم بعد")