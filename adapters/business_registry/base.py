"""
Business Registration Lookup Adapter — Abstract Interface ONLY

🟡 Provider غير محسوم، ويعتمد كليًا على الدولة/السوق المستهدف
(غير محدد صراحة بعد في أي من الوثيقتين). لا تنفيذ فعلي هنا.
"""

from abc import ABC, abstractmethod


class BaseBusinessRegistryAdapter(ABC):
    @abstractmethod
    def verify_registration(self, registration_number: str, *args, **kwargs):
        raise NotImplementedError(
            "Business Registration Lookup Provider غير محسوم — يعتمد على الدولة المستهدفة"
        )