"""
FakePaymentAdapter — للتطوير والاختبار فقط.

⚠️ لا يتصل بأي مزوّد ولا يشحن أي مبلغ حقيقي. الغرض الوحيد تمكين تدفّق
   الدفع محليًا وفي الاختبارات.

🔒 أمان (نفس نمط DevConsoleSMSAdapter — Change Set §31): يرفض العمل عندما
   DEBUG=False ما لم يُفعَّل PAYMENTS_ALLOW_FAKE_ADAPTER=True صراحةً، حتى
   لا يظن نظام إنتاجي أن عمليات الدفع تمت فعلًا وهي وهمية.

📌 قيمة الفشل المتفق عليها (failure sentinel): مبلغ يساوي بالضبط
   Decimal("0.01") يُعيد فشلًا. اختير لأنه أصغر مبلغ ممكن ولا يقع عمليًا
   كسعر حجز حقيقي (أي حجز يحمل base_price أكبر بكثير). أي مبلغ آخر ينجح.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import BasePaymentProviderAdapter, ChargeOutcome, PaymentChargeResult

# 📌 المبلغ الذي يُحاكي فشل المزوّد في الاختبارات.
FAILURE_SENTINEL_AMOUNT = Decimal("0.01")

FAILURE_REASON = "Card declined by issuer (simulated)."

# 📌 مبلغ يُحاكي طلب المصادقة البنكية (3-D Secure) في الاختبارات.
REQUIRES_ACTION_SENTINEL_AMOUNT = Decimal("0.02")

# ملخّص آمن لطريقة الدفع — نفس شكل ما يعيده مزوّد حقيقي (§13).
_METHOD_SUMMARIES = {
    "CARD": {
        "type": "CARD",
        "display_name": "Visa •••• 4242",
        "card_brand": "VISA",
        "last4": "4242",
    },
    "APPLE_PAY": {
        "type": "APPLE_PAY",
        "display_name": "Apple Pay",
        "card_brand": None,
        "last4": None,
    },
    "GOOGLE_PAY": {
        "type": "GOOGLE_PAY",
        "display_name": "Google Pay",
        "card_brand": None,
        "last4": None,
    },
}


class FakePaymentAdapter(BasePaymentProviderAdapter):
    """
    ينجح دائمًا بمعرّف مُصطنع، إلا إذا كان المبلغ مساويًا للقيمة المتفق
    عليها لمحاكاة الفشل (FAILURE_SENTINEL_AMOUNT).
    """

    def __init__(self):
        allow_fake = getattr(settings, "PAYMENTS_ALLOW_FAKE_ADAPTER", False)
        if not settings.DEBUG and not allow_fake:
            raise ImproperlyConfigured(
                "FakePaymentAdapter لا يشحن أي مبلغ حقيقي ولا يجوز استخدامه خارج "
                "بيئة التطوير/الاختبار. اضبط PAYMENT_PROVIDER_ADAPTER_CLASS على "
                "تنفيذ حقيقي، أو فعّل PAYMENTS_ALLOW_FAKE_ADAPTER=True صراحةً."
            )

    def charge(
        self,
        amount,
        method,
        idempotency_key,
        payment_method_reference="",
        currency="AUD",
        customer_reference=None,
    ):
        """
        ⚠️ لا شبكة ولا مزوّد. النتيجة تُشتق من المبلغ وحده.

        📌 ينمذج المسارات الأربعة التي تطلبها §18: نجاح، فشل، طلب
           مصادقة، وإعادة محاولة (بمفتاح تكرار مختلف لكل محاولة).
        """
        summary = _METHOD_SUMMARIES.get(method, _METHOD_SUMMARIES["CARD"])

        if amount == FAILURE_SENTINEL_AMOUNT:
            return PaymentChargeResult(
                outcome=ChargeOutcome.FAILED,
                provider_reference=None,
                failure_reason=FAILURE_REASON,
                error_code="card_declined",
                method_summary=summary,
            )

        if amount == REQUIRES_ACTION_SENTINEL_AMOUNT:
            reference = f"fake_{uuid.uuid4()}"
            return PaymentChargeResult(
                outcome=ChargeOutcome.REQUIRES_ACTION,
                provider_reference=reference,
                action_payload={
                    "type": "3ds_redirect",
                    "client_secret": f"fake_secret_{reference}",
                },
                method_summary=summary,
            )

        return PaymentChargeResult(
            outcome=ChargeOutcome.SUCCEEDED,
            provider_reference=f"fake_{uuid.uuid4()}",
            method_summary=summary,
        )

    def confirm(self, provider_reference):
        """
        يحسم محاولة بعد مصادقة العميل.

        📌 الـfake يفترض نجاح المصادقة دائمًا: اختبار الفشل بعد المصادقة
           يمرّ عبر مبلغ الفشل المتفق عليه لا عبر هذا المسار.
        """
        return PaymentChargeResult(
            outcome=ChargeOutcome.SUCCEEDED,
            provider_reference=provider_reference,
            method_summary=_METHOD_SUMMARIES["CARD"],
        )
