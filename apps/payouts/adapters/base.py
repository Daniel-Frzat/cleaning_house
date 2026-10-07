"""
Payout Provider Adapter — Abstract Interface ONLY (Change Set §36.5)

🟡 مزوّد الدفع للمقاولين قرار مفتوح. لا تنفيذ فعلي هنا ولا في أي مكان
   في كود الإنتاج: لا Stripe Connect، ولا Wise، ولا Airwallex، ولا أي
   استدعاء شبكي.

الاختيار عبر settings.PAYOUT_PROVIDER_ADAPTER_CLASS (مسار نصي لكلاس) —
نفس نمط PAYMENT_PROVIDER_ADAPTER_CLASS و JOB_STORAGE_ADAPTER_CLASS.

⚠️ العقد محايد للمزوّد ويغطي تحويل المبلغ وإعداد حساب الصرف ومزامنة
   أحداث الحساب. الدفع فوري لكل حجز، بلا تجميع أو جدولة.
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


class PayoutAccountWebhookError(Exception):
    """A connected-account webhook cannot be verified or parsed."""


class PayoutAccountProviderError(Exception):
    """The provider could not create an account or onboarding link."""


class PayoutAccountWebhookEvent:
    """Provider-neutral connected-account event."""

    def __init__(
        self,
        kind,
        event_id=None,
        account_reference=None,
        payouts_enabled=False,
        details_submitted=False,
        requirements_due=None,
    ):
        self.kind = kind
        self.event_id = event_id
        self.account_reference = account_reference
        self.payouts_enabled = bool(payouts_enabled)
        self.details_submitted = bool(details_submitted)
        self.requirements_due = requirements_due or []


class BasePayoutProviderAdapter(ABC):
    """
    واجهة مزوّد الدفع للمقاولين — لا تحتوي تفاصيل مزوّد بعينه.
    """

    @abstractmethod
    def payout(
        self,
        amount: Decimal,
        contractor_reference: str,
        idempotency_key: str,
        payout_id=None,
        booking_id=None,
    ) -> PayoutResult:
        """
        يدفع المبلغ للمقاول فورًا (لا تجميع ولا جدولة).

        Args:
            amount: المبلغ الكامل بالـDecimal (بلا عمولة — §36.5).
            contractor_reference: معرّف المقاول عند المزوّد. في هذه
                المرحلة معرف الحساب لدى مزود الدفع.
            idempotency_key: مفتاح ثابت مشتق من سجل الدفعة — إعادة الاستدعاء
                بالمفتاح نفسه يجب ألا تُنتج دفعًا مزدوجًا (Infra §14/§16).
            payout_id: سجل الدفعة الداخلي، لبيانات وصفية لدى المزود.
            booking_id: الحجز المرتبط، لبيانات وصفية ومجموعة التحويل.

        Returns:
            PayoutResult
        """
        raise NotImplementedError(
            "Payout Provider غير محسوم — لا تنفيذ فعلي في كود الإنتاج."
        )

    @abstractmethod
    def create_account(self, contractor):
        """Create the provider-side payout account for a contractor profile."""

    @abstractmethod
    def create_onboarding_link(self, account_id, return_url, refresh_url):
        """Create a fresh, single-use onboarding link."""

    @abstractmethod
    def parse_account_event(self, payload, headers) -> PayoutAccountWebhookEvent:
        """Verify and parse a connected-account webhook event."""
