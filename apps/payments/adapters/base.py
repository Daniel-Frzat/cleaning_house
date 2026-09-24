"""
Payment Provider Adapter — Abstract Interface ONLY (Change Set §8؛ Infra §2)

🟡 مزوّد الدفع (PSP) قرار مفتوح. لا تنفيذ فعلي هنا ولا في أي مكان في كود
   الإنتاج: لا Stripe SDK، ولا استدعاءات HTTP، ولا أي تكامل حقيقي.

الاختيار يتم عبر settings.PAYMENT_PROVIDER_ADAPTER_CLASS (مسار نصي لكلاس)،
بحيث لا تعرف طبقة الـDomain أي مزوّد بعينه — نفس نمط SMS_ADAPTER و
SOCIAL_AUTH_ADAPTER من Phase 0.

⚠️ العقد هنا متعمَّد البساطة: charge() تعيد نتيجة محايدة الشكل
   (PaymentChargeResult) لا تفترض حقول أي مزوّد. أي إثراء لاحق (3-D Secure،
   استرداد، تقسيط) يُضاف كميثود مستقل لا بتعديل هذا العقد.
"""

from abc import ABC, abstractmethod
from decimal import Decimal


class PaymentChargeResult:
    """
    نتيجة محاولة شحن — شكل محايد لا يخص مزوّدًا بعينه.

    success            : هل نجحت العملية؟
    provider_reference : معرّف مبهم من المزوّد (None عند الفشل عادةً).
                         لا يُفسَّر ولا يُحلَّل في منطق النطاق.
    failure_reason     : سبب الفشل بصيغة مقروءة (None عند النجاح).
    """

    __slots__ = ("success", "provider_reference", "failure_reason")

    def __init__(self, success, provider_reference=None, failure_reason=None):
        self.success = bool(success)
        self.provider_reference = provider_reference
        self.failure_reason = failure_reason

    def __repr__(self):
        return (
            f"PaymentChargeResult(success={self.success}, "
            f"provider_reference={self.provider_reference!r}, "
            f"failure_reason={self.failure_reason!r})"
        )


class BasePaymentProviderAdapter(ABC):
    """
    واجهة مزوّد الدفع — تجريدية بالكامل.

    ⚠️ لا تُنفَّذ هنا. أي تنفيذ حقيقي يُضاف في مرحلته الخاصة بعد حسم
       المزوّد، دون تعديل طبقة الـDomain.
    """

    @abstractmethod
    def charge(
        self,
        amount: Decimal,
        method: str,
        idempotency_key: str,
        currency: str = "AUD",
        customer_reference: str = None,
    ) -> PaymentChargeResult:
        """
        يشحن المبلغ مباشرة (direct charge — لا تفويض ولا حجز).

        Args:
            amount: المبلغ بالـDecimal.
            method: إحدى قيم PaymentMethod.
            idempotency_key: مفتاح ثابت مشتق من الحجز — إعادة الاستدعاء
                بالمفتاح نفسه يجب ألا تُنتج شحنًا مزدوجًا (Infra §14/§16).
            currency: رمز ISO 4217 — "AUD" دائمًا في هذا السوق.
            customer_reference: معرّف العميل الداخلي. التنفيذ الحقيقي يربطه
                بعميل المزوّد ووسيلة الدفع المحفوظة (قرار مفتوح مع المزوّد).

        ⚠️ استثناء يُرفع من هنا يعني "النتيجة مجهولة": الدفعة تبقى PENDING
           للمطابقة. الرفض المعروف (بطاقة مرفوضة) يُعاد success=False.

        Returns:
            PaymentChargeResult
        """
        raise NotImplementedError(
            "Payment Service Provider غير محسوم — لا تنفيذ فعلي في كود الإنتاج."
        )
