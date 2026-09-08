"""
Address Validation Adapter — Abstract Interface ONLY

🟢 Provider غير محسوم. لا تنفيذ فعلي هنا.
"""

from abc import ABC, abstractmethod


class BaseAddressValidationAdapter(ABC):
    @abstractmethod
    def autocomplete(self, query: str, *args, **kwargs):
        raise NotImplementedError("Address Validation Provider غير محسوم بعد")

    @abstractmethod
    def validate(self, address_payload: dict, *args, **kwargs):
        raise NotImplementedError("Address Validation Provider غير محسوم بعد")