"""
Payment Provider Adapter — Abstract Interface ONLY (Change Set §8؛ Infra §2)

📌 المزوّد المعتمد Stripe (قرار PO — 2026-09-30) في stripe_adapter.py وحده.
   هذا الملف عقد محايد لا يعرف أي مزوّد، وبقية النطاق لا تتعامل إلا معه.

الاختيار يتم عبر settings.PAYMENT_PROVIDER_ADAPTER_CLASS (مسار نصي لكلاس)،
بحيث لا تعرف طبقة الـDomain أي مزوّد بعينه — نفس نمط SMS_ADAPTER و
SOCIAL_AUTH_ADAPTER من Phase 0.

⚠️ العقد هنا متعمَّد البساطة: charge() تعيد نتيجة محايدة الشكل
   (PaymentChargeResult) لا تفترض حقول أي مزوّد. أي إثراء لاحق (3-D Secure،
   استرداد، تقسيط) يُضاف كميثود مستقل لا بتعديل هذا العقد.
"""

from abc import ABC, abstractmethod
from decimal import Decimal


class ChargeOutcome:
    """
    نتائج محاولة الشحن الممكنة (§13).

    SUCCEEDED       : تأكد الشحن.
    REQUIRES_ACTION : البنك يطلب مصادقة العميل (3-D Secure).
    FAILED          : رُفضت المحاولة.

    ⚠️ لا PENDING هنا: "جارٍ" حالة داخلية لدى النظام لا نتيجة يعيدها
       المزوّد. المزوّد إمّا حسم أو طلب مصادقة.
    """

    SUCCEEDED = "SUCCEEDED"
    REQUIRES_ACTION = "REQUIRES_ACTION"
    FAILED = "FAILED"


class PaymentProviderUnavailableError(Exception):
    """المزوّد رفض طلبًا إداريًا (مفتاح خاطئ، معامل غير صالح، انقطاع) — لا شحن هنا."""


class WebhookSignatureError(Exception):
    """توقيع حدث المزوّد غير صالح أو الحمولة تالفة."""


class ProviderWebhookEvent:
    """
    حدث مُتحقَّق من توقيعه من المزوّد، بشكل محايد.

    kind: "succeeded" | "failed" | "ignored"
    idempotency_key: مفتاح المحاولة المحفوظ عند الشحن — يربط الحدث بالدفعة
        حتى لو لم يصل provider_reference (استثناء بعد خصم فعلي).
    """

    def __init__(self, kind, event_id=None, provider_reference=None, idempotency_key=None,
                 failure_reason=None, error_code=""):
        self.kind = kind
        self.event_id = event_id
        self.provider_reference = provider_reference
        self.idempotency_key = idempotency_key
        self.failure_reason = failure_reason
        self.error_code = error_code


class PaymentChargeResult:
    """
    نتيجة محاولة شحن — شكل محايد لا يخص مزوّدًا بعينه.

    outcome            : إحدى قيم ChargeOutcome.
    provider_reference : معرّف مبهم من المزوّد (None عند الفشل عادةً).
                         لا يُفسَّر ولا يُحلَّل في منطق النطاق.
    failure_reason     : سبب الفشل بصيغة مقروءة (None عند النجاح).
    error_code         : 🔒 رمز المزوّد الخام — للتشخيص الإداري وحده،
                         لا يُكشف للعميل (§15).
    action_payload     : بيانات المصادقة المطلوبة (client secret أو ما
                         يعادله) — تُمرَّر للعميل ليكملها بالـSDK (§17).
    method_summary     : 🔒 ملخّص آمن لطريقة الدفع للعرض:
                         {"type","display_name","card_brand","last4"}.
                         لا PAN ولا CVC ولا رمز مزوّد (§13).

    📌 success يبقى خاصية مشتقة: كود قائم يقرؤها، وحذفها كسرٌ بلا داعٍ.
    """

    __slots__ = (
        "outcome",
        "provider_reference",
        "failure_reason",
        "error_code",
        "action_payload",
        "method_summary",
    )

    def __init__(
        self,
        outcome=None,
        provider_reference=None,
        failure_reason=None,
        error_code="",
        action_payload=None,
        method_summary=None,
        success=None,
    ):
        # التوافق مع النداءات القديمة التي تمرّر success=True/False.
        if outcome is None:
            outcome = (
                ChargeOutcome.SUCCEEDED if success else ChargeOutcome.FAILED
            )
        self.outcome = outcome
        self.provider_reference = provider_reference
        self.failure_reason = failure_reason
        self.error_code = error_code
        self.action_payload = action_payload
        self.method_summary = method_summary

    @property
    def success(self):
        return self.outcome == ChargeOutcome.SUCCEEDED

    @property
    def requires_action(self):
        return self.outcome == ChargeOutcome.REQUIRES_ACTION

    def __repr__(self):
        return (
            f"PaymentChargeResult(outcome={self.outcome!r}, "
            f"provider_reference={self.provider_reference!r}, "
            f"failure_reason={self.failure_reason!r})"
        )


class BasePaymentProviderAdapter(ABC):
    """
    واجهة مزوّد الدفع — تجريدية بالكامل.

    ⚠️ لا تُنفَّذ هنا. أي تنفيذ حقيقي يُضاف في مرحلته الخاصة بعد حسم
       المزوّد، دون تعديل طبقة الـDomain.
    """

    def confirm(self, provider_reference: str) -> PaymentChargeResult:
        """
        يستعلم عن حسم محاولة بعد مصادقة العميل (§17).

        ⚠️ غير مجرّدة عمدًا: مزوّد متزامن بلا مصادقة لا يحتاجها. الافتراضي
           يرفع NotImplementedError، فمن يحتاجها ينفّذها.
        """
        raise NotImplementedError(
            "This provider does not support deferred confirmation."
        )

    def refund(self, provider_reference: str, amount: Decimal, idempotency_key: str, currency: str = "AUD"):
        """
        يعيد مبلغًا (كله أو بعضه) إلى وسيلة الدفع. يعيد PaymentChargeResult:
        SUCCEEDED مع provider_reference للاسترداد، أو FAILED مع السبب.

        ⚠️ غير مجرّدة عمدًا: الافتراضي يرفع NotImplementedError حتى ينفّذه
           المزوّد الحقيقي.
        """
        raise NotImplementedError("This provider does not support refunds.")

    def create_customer(self, user_reference, email=None, name=""):
        """ينشئ عميلًا لدى المزوّد ويعيد معرّفه (لحفظ البطاقات والشحن اللاحق)."""
        raise NotImplementedError("This provider does not support saved customers.")

    def setup_payment_method(self, customer_reference: str, stripe_version=None):
        """
        جلسة حفظ بطاقة للشحن اللاحق بلا حضور العميل (§18). يعيد قاموسًا:
        client_secret، setup_intent_reference، customer_reference، ephemeral_key.
        """
        raise NotImplementedError("Payment method setup requires a real provider.")

    def parse_webhook(self, payload: bytes, headers) -> "ProviderWebhookEvent":
        """يتحقق من توقيع حدث المزوّد ويعيده محايدًا — يرفع عند توقيع خاطئ."""
        raise NotImplementedError("This provider does not send webhooks.")

    def list_payment_methods(self, customer_reference: str):
        """يسرد طرق الدفع المحفوظة (§18). 🔴 غير منفَّذة — تحتاج مزوّدًا."""
        raise NotImplementedError("Listing payment methods requires a real provider.")

    def detach_payment_method(self, method_reference: str):
        """يحذف طريقة دفع محفوظة (§18). 🔴 غير منفَّذة — تحتاج مزوّدًا."""
        raise NotImplementedError("Detaching a payment method requires a real provider.")

    @abstractmethod
    def charge(
        self,
        amount: Decimal,
        method: str,
        idempotency_key: str,
        payment_method_reference: str = "",
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
