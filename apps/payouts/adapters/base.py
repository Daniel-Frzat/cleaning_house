"""
Payout Provider Adapter — Abstract Interface ONLY (Change Set §36.5)

🟡 مزوّد الدفع للمقاولين قرار مفتوح. لا تنفيذ فعلي هنا ولا في أي مكان
   في كود الإنتاج: لا Stripe Connect، ولا Wise، ولا Airwallex، ولا أي
   استدعاء شبكي.

الاختيار عبر settings.PAYOUT_PROVIDER_ADAPTER_CLASS (مسار نصي لكلاس) —
نفس نمط PAYMENT_PROVIDER_ADAPTER_CLASS و JOB_STORAGE_ADAPTER_CLASS.

⚠️ العقد بسيط عمدًا: payout() تعيد نتيجة محايدة الشكل لا تفترض حقول أي
   مزوّد. الدفع فوري لكل حجز، فلا يوجد في العقد أي مفهوم دفعة مجمَّعة
   ولا جدولة — وهذا مقصود لا نقص.
"""

from abc import ABC, abstractmethod
from decimal import Decimal


class PayoutResult:
    """
    نتيجة محاولة دفع — شكل محايد لا يخص مزوّدًا بعينه.

    success            : هل نجحت العملية؟
    provider_reference : معرّف مبهم من المزوّد (None عند الفشل عادةً).
    failure_reason     : سبب الفشل بصيغة مقروءة (None عند النجاح).
    """

    __slots__ = ("success", "provider_reference", "failure_reason")

    def __init__(self, success, provider_reference=None, failure_reason=None):
        self.success = bool(success)
        self.provider_reference = provider_reference
        self.failure_reason = failure_reason

    def __repr__(self):
        return (
            f"PayoutResult(success={self.success}, "
            f"provider_reference={self.provider_reference!r}, "
            f"failure_reason={self.failure_reason!r})"
        )


class BasePayoutProviderAdapter(ABC):
    """
    واجهة مزوّد الدفع للمقاولين — تجريدية بالكامل.

    ⚠️ لا تُنفَّذ هنا. أي تنفيذ حقيقي يُضاف في مرحلته الخاصة بعد حسم
       المزوّد، دون تعديل طبقة الـDomain.
    """

    @abstractmethod
    def payout(
        self, amount: Decimal, contractor_reference: str, idempotency_key: str
    ) -> PayoutResult:
        """
        يدفع المبلغ للمقاول فورًا (لا تجميع ولا جدولة).

        Args:
            amount: المبلغ الكامل بالـDecimal (بلا عمولة — §36.5).
            contractor_reference: معرّف المقاول عند المزوّد. في هذه
                المرحلة معرّف المستخدم الداخلي؛ حسابات المزوّد تُربط
                عند حسمه.
            idempotency_key: مفتاح ثابت مشتق من الحجز — إعادة الاستدعاء
                بالمفتاح نفسه يجب ألا تُنتج دفعًا مزدوجًا (Infra §14/§16).

        Returns:
            PayoutResult
        """
        raise NotImplementedError(
            "Payout Provider غير محسوم — لا تنفيذ فعلي في كود الإنتاج."
        )
