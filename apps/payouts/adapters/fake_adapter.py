"""
FakePayoutAdapter — للتطوير والاختبار فقط.

⚠️ لا يتصل بأي مزوّد ولا يحوّل أي مبلغ حقيقي. الغرض الوحيد تمكين تدفّق
   الدفع محليًا وفي الاختبارات.

🔒 أمان (نفس نمط FakePaymentAdapter — §43، و DevConsoleSMSAdapter — §31):
   يرفض العمل عند DEBUG=False ما لم يُفعَّل PAYOUTS_ALLOW_FAKE_ADAPTER
   صراحةً، حتى لا يظن نظام إنتاجي أن المقاولين قد استلموا أموالهم.

📌 قيمة الفشل المتفق عليها (failure sentinel): مبلغ يساوي بالضبط
   Decimal("0.02") يُعيد فشلًا. اختير مغايرًا لقيمة Payment (0.01) حتى
   لا يتشابك سلوك النطاقين في اختبار واحد.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import BasePayoutProviderAdapter, PayoutResult

# 📌 المبلغ الذي يُحاكي فشل المزوّد في الاختبارات.
FAILURE_SENTINEL_AMOUNT = Decimal("0.02")

FAILURE_REASON = "Contractor payout rejected by provider (simulated)."


class FakePayoutAdapter(BasePayoutProviderAdapter):
    """
    ينجح دائمًا بمعرّف مُصطنع، إلا إذا كان المبلغ مساويًا للقيمة المتفق
    عليها لمحاكاة الفشل (FAILURE_SENTINEL_AMOUNT).
    """

    def __init__(self):
        allow_fake = getattr(settings, "PAYOUTS_ALLOW_FAKE_ADAPTER", False)
        if not settings.DEBUG and not allow_fake:
            raise ImproperlyConfigured(
                "FakePayoutAdapter لا يحوّل أي مبلغ حقيقي ولا يجوز استخدامه خارج "
                "بيئة التطوير/الاختبار. اضبط PAYOUT_PROVIDER_ADAPTER_CLASS على "
                "تنفيذ حقيقي، أو فعّل PAYOUTS_ALLOW_FAKE_ADAPTER=True صراحةً."
            )

    def payout(self, amount, contractor_reference, idempotency_key):
        """
        ⚠️ لا شبكة ولا مزوّد. النتيجة تُشتق من المبلغ وحده.
        """
        if amount == FAILURE_SENTINEL_AMOUNT:
            return PayoutResult(
                success=False,
                provider_reference=None,
                failure_reason=FAILURE_REASON,
            )

        return PayoutResult(
            success=True,
            provider_reference=f"fake_payout_{uuid.uuid4()}",
            failure_reason=None,
        )
