"""
Payment Adapter — Abstract Interface ONLY

🔴 BLOCKING: Payment Provider غير محسوم بعد (راجع Open Decisions #4).
لا يوجد هنا أي تنفيذ فعلي، ولا حتى افتراض حول:
    - آلية الـHold/Release الفعلية
    - كيفية تنفيذ Contractor Payout
    - القيود القانونية على Escrow

هذا الملف فقط يحدد "الشكل العام" الذي يجب أن يلتزم به أي Adapter
مستقبلي، دون منطق أعمال.
"""

from abc import ABC, abstractmethod


class BasePaymentAdapter(ABC):
    """Contract مجرد — لا تنفيذ. يُطبَّق فعليًا فقط بعد حسم Payment Provider."""

    @abstractmethod
    def authorize_payment(self, *args, **kwargs):
        raise NotImplementedError("Payment Provider غير محسوم بعد — راجع Open Decision #4")

    @abstractmethod
    def hold_in_escrow(self, *args, **kwargs):
        raise NotImplementedError("آلية الـEscrow Hold تعتمد على Provider غير محسوم")

    @abstractmethod
    def release_escrow(self, *args, **kwargs):
        raise NotImplementedError("شروط الـEscrow Release غير محسومة — راجع Open Decision #3")

    @abstractmethod
    def payout_to_contractor(self, *args, **kwargs):
        raise NotImplementedError("آلية الـPayout تعتمد على Provider غير محسوم")