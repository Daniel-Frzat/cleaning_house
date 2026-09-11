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

from .base import BasePaymentProviderAdapter, PaymentChargeResult

# 📌 المبلغ الذي يُحاكي فشل المزوّد في الاختبارات.
FAILURE_SENTINEL_AMOUNT = Decimal("0.01")

FAILURE_REASON = "Card declined by issuer (simulated)."


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

    def charge(self, amount, method, idempotency_key):
        """
        ⚠️ لا شبكة ولا مزوّد. النتيجة تُشتق من المبلغ وحده.
        """
        if amount == FAILURE_SENTINEL_AMOUNT:
            return PaymentChargeResult(
                success=False,
                provider_reference=None,
                failure_reason=FAILURE_REASON,
            )

        return PaymentChargeResult(
            success=True,
            provider_reference=f"fake_{uuid.uuid4()}",
            failure_reason=None,
        )
