"""
StripePaymentAdapter — مزوّد الدفع الحقيقي (Stripe).

📌 التدفق (دفع بعد قبول العامل، والعميل قد يكون خارج التطبيق):
   1) قبل الطلب: setup_payment_method → SetupIntent على عميل Stripe. التطبيق
      يحفظ البطاقة عبر PaymentSheet، ويُجري 3-D Secure الآن والعميل حاضر.
   2) عند قبول العامل: charge → PaymentIntent off_session على البطاقة
      المحفوظة، بلا تدخل العميل.
   3) إن طلب البنك مصادقة جديدة (نادر): REQUIRES_ACTION مع client_secret،
      يفتح التطبيق شاشة البنك ثم confirm-action → confirm.
   4) الـwebhook يحسم ما بقي معلّقًا (نتيجة مجهولة أو مصادقة تمت خارج التطبيق).

🔒 أخطاء البطاقة (رفض، رصيد، مصادقة) نتيجة معروفة → FAILED/REQUIRES_ACTION.
   أي خطأ آخر (شبكة، Stripe متوقف) يُرفع كما هو: طبقة الخدمة تعامله "نتيجة
   مجهولة" وتترك الدفعة للمطابقة — قد يكون الخصم تم.

الإعداد:
    PAYMENT_PROVIDER_ADAPTER_CLASS=apps.payments.adapters.stripe_adapter.StripePaymentAdapter
    STRIPE_SECRET_KEY=sk_test_… / sk_live_…
    STRIPE_WEBHOOK_SECRET=whsec_…
"""

import logging
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import (
    BasePaymentProviderAdapter,
    ChargeOutcome,
    PaymentChargeResult,
    PaymentProviderUnavailableError,
    ProviderWebhookEvent,
    WebhookSignatureError,
)

logger = logging.getLogger(__name__)

_WALLET_METHODS = {"google_pay": "GOOGLE_PAY", "apple_pay": "APPLE_PAY"}
_WALLET_NAMES = {"GOOGLE_PAY": "Google Pay", "APPLE_PAY": "Apple Pay"}


def to_minor_units(amount):
    """Decimal بالدولار → سنتات صحيحة (Stripe لا يقبل كسورًا)."""
    return int((Decimal(amount) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _get(obj, key, default=None):
    """Stripe يعيد كائنات تشبه القواميس؛ الاختبارات تمرّر قواميس عادية."""
    if obj is None:
        return default
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return getattr(obj, key, default)


def method_summary_from(payment_method):
    """{type, display_name, card_brand, last4} من PaymentMethod لدى Stripe."""
    card = _get(payment_method, "card") or {}
    wallet = _get(card, "wallet") or {}
    method = _WALLET_METHODS.get(_get(wallet, "type"), "CARD")
    brand = (_get(card, "brand") or "").upper() or None
    last4 = _get(card, "last4")
    if method in _WALLET_NAMES:
        display = _WALLET_NAMES[method]
    elif brand and last4:
        display = f"{brand.title()} •••• {last4}"
    else:
        display = "Card"
    return {"type": method, "display_name": display, "card_brand": brand, "last4": last4}


class StripePaymentAdapter(BasePaymentProviderAdapter):
    provider_name = "stripe"

    def __init__(self):
        import stripe

        self.stripe = stripe
        self.api_key = getattr(settings, "STRIPE_SECRET_KEY", "") or ""
        if not self.api_key:
            raise ImproperlyConfigured("STRIPE_SECRET_KEY is not set.")
        self.webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "") or ""

    # ------------------------------------------------------------ العملاء والبطاقات
    def _unavailable(self, exc):
        """خطأ Stripe → رسالة تشخيص آمنة (Stripe يحجب المفتاح في رسائله)."""
        code = getattr(exc, "code", None) or ""
        message = getattr(exc, "user_message", None) or str(exc)
        logger.error("Stripe request failed: %s %s", type(exc).__name__, message)
        return PaymentProviderUnavailableError(f"{type(exc).__name__}{f' ({code})' if code else ''}: {message}")

    def create_customer(self, user_reference, email=None, name=""):
        try:
            return self._create_customer(user_reference, email, name)
        except self.stripe.StripeError as exc:
            raise self._unavailable(exc) from exc

    def _create_customer(self, user_reference, email=None, name=""):
        customer = self.stripe.Customer.create(
            api_key=self.api_key,
            email=email or None,
            name=name or None,
            metadata={"user_id": str(user_reference)},
            idempotency_key=f"customer-{user_reference}",
        )
        return customer["id"]

    def setup_payment_method(self, customer_reference, stripe_version=None):
        """
        SetupIntent لحفظ بطاقة للاستعمال اللاحق بلا حضور العميل. مع
        stripe_version (من SDK التطبيق) يُنشأ ephemeral key لـPaymentSheet.
        """
        try:
            return self._setup_payment_method(customer_reference, stripe_version)
        except self.stripe.StripeError as exc:
            raise self._unavailable(exc) from exc

    def _setup_payment_method(self, customer_reference, stripe_version=None):
        setup = self.stripe.SetupIntent.create(
            api_key=self.api_key,
            customer=customer_reference,
            usage="off_session",
            automatic_payment_methods={"enabled": True},
        )
        ephemeral_key = None
        if stripe_version:
            key = self.stripe.EphemeralKey.create(
                api_key=self.api_key, customer=customer_reference, stripe_version=stripe_version
            )
            ephemeral_key = key["secret"]
        return {
            "client_secret": setup["client_secret"],
            "setup_intent_reference": setup["id"],
            "customer_reference": customer_reference,
            "ephemeral_key": ephemeral_key,
        }

    def _default_payment_method(self, customer_reference):
        methods = self.stripe.PaymentMethod.list(
            api_key=self.api_key, customer=customer_reference, type="card", limit=1
        )
        data = _get(methods, "data") or []
        return data[0]["id"] if data else ""

    def _attach(self, payment_method_reference, customer_reference):
        """يربط البطاقة بالعميل إن لم تكن مربوطة — شرط الشحن off_session."""
        payment_method = self.stripe.PaymentMethod.retrieve(payment_method_reference, api_key=self.api_key)
        owner = _get(payment_method, "customer")
        if owner and owner != customer_reference:
            return None  # بطاقة عميل آخر — لا تُستعمل
        if not owner:
            payment_method = self.stripe.PaymentMethod.attach(
                payment_method_reference, customer=customer_reference, api_key=self.api_key
            )
        return payment_method

    # ------------------------------------------------------------ الشحن
    def _result_from_intent(self, intent, payment_method=None):
        status = _get(intent, "status")
        summary = method_summary_from(payment_method) if payment_method is not None else None
        reference = _get(intent, "id")
        if status == "succeeded":
            return PaymentChargeResult(outcome=ChargeOutcome.SUCCEEDED, provider_reference=reference,
                                       method_summary=summary)
        if status in ("requires_action", "requires_confirmation", "processing"):
            return PaymentChargeResult(
                outcome=ChargeOutcome.REQUIRES_ACTION,
                provider_reference=reference,
                action_payload={
                    "type": "stripe_payment_intent",
                    "client_secret": _get(intent, "client_secret"),
                    "payment_method": _get(intent, "payment_method"),
                },
                method_summary=summary,
            )
        error = _get(intent, "last_payment_error") or {}
        return PaymentChargeResult(
            outcome=ChargeOutcome.FAILED,
            provider_reference=reference,
            failure_reason=_get(error, "message") or "The payment was not completed.",
            error_code=_get(error, "decline_code") or _get(error, "code") or status or "",
            method_summary=summary,
        )

    def charge(self, amount, method, idempotency_key, payment_method_reference="", currency="AUD",
               customer_reference=None):
        if not customer_reference:
            return PaymentChargeResult(outcome=ChargeOutcome.FAILED, failure_reason="No saved payment method.",
                                       error_code="no_customer")
        reference = payment_method_reference or self._default_payment_method(customer_reference)
        if not reference:
            return PaymentChargeResult(outcome=ChargeOutcome.FAILED, failure_reason="No saved payment method.",
                                       error_code="no_payment_method")
        # قد يرفض Stripe البطاقة عند الربط نفسه (قبل أي شحن) — تُعامل كرفض عادي
        payment_method = None
        try:
            payment_method = self._attach(reference, customer_reference)
            if payment_method is None:
                return PaymentChargeResult(outcome=ChargeOutcome.FAILED,
                                           failure_reason="This payment method belongs to another account.",
                                           error_code="payment_method_mismatch")
            # بعد الربط قد يعيد Stripe معرّفًا جديدًا للبطاقة — نشحن ما رُبط فعلًا
            reference = _get(payment_method, "id") or reference
            intent = self.stripe.PaymentIntent.create(
                api_key=self.api_key,
                amount=to_minor_units(amount),
                currency=currency.lower(),
                customer=customer_reference,
                payment_method=reference,
                confirm=True,
                off_session=True,
                idempotency_key=idempotency_key,
                metadata={"idempotency_key": idempotency_key},
            )
        except self.stripe.CardError as exc:
            # رفض البطاقة أو حاجة لمصادقة — نتيجة معروفة
            error = exc.error or {}
            intent = _get(error, "payment_intent")
            if _get(error, "code") == "authentication_required" and intent is not None:
                return PaymentChargeResult(
                    outcome=ChargeOutcome.REQUIRES_ACTION,
                    provider_reference=_get(intent, "id"),
                    action_payload={
                        "type": "stripe_payment_intent",
                        "client_secret": _get(intent, "client_secret"),
                        "payment_method": reference,
                    },
                    error_code="authentication_required",
                    method_summary=method_summary_from(payment_method),
                )
            return PaymentChargeResult(
                outcome=ChargeOutcome.FAILED,
                provider_reference=_get(intent, "id") if intent is not None else None,
                failure_reason=exc.user_message or "Your card was declined.",
                error_code=_get(error, "decline_code") or _get(error, "code") or "card_error",
                method_summary=method_summary_from(payment_method),
            )
        return self._result_from_intent(intent, payment_method)

    def confirm(self, provider_reference):
        intent = self.stripe.PaymentIntent.retrieve(
            provider_reference, api_key=self.api_key, expand=["payment_method"]
        )
        payment_method = _get(intent, "payment_method")
        # 📌 Stripe ≥15 يعيد كائنات ليست dict؛ المعرّف النصي وحده يعني "غير موسَّع"
        expanded = payment_method if payment_method is not None and not isinstance(payment_method, str) else None
        return self._result_from_intent(intent, expanded)

    def refund(self, provider_reference, amount, idempotency_key, currency="AUD"):
        try:
            refund = self.stripe.Refund.create(
                api_key=self.api_key,
                payment_intent=provider_reference,
                amount=to_minor_units(amount),
                idempotency_key=idempotency_key,
            )
        except self.stripe.InvalidRequestError as exc:
            return PaymentChargeResult(outcome=ChargeOutcome.FAILED, failure_reason=exc.user_message or str(exc))
        if _get(refund, "status") in ("failed", "canceled"):
            return PaymentChargeResult(outcome=ChargeOutcome.FAILED, provider_reference=_get(refund, "id"),
                                       failure_reason=_get(refund, "failure_reason") or "Refund failed.")
        return PaymentChargeResult(outcome=ChargeOutcome.SUCCEEDED, provider_reference=_get(refund, "id"))

    # ------------------------------------------------------------ webhook
    def parse_webhook(self, payload, headers):
        if not self.webhook_secret:
            raise ImproperlyConfigured("STRIPE_WEBHOOK_SECRET is not set.")
        try:
            event = self.stripe.Webhook.construct_event(
                payload, headers.get("Stripe-Signature", ""), self.webhook_secret
            )
        except (ValueError, self.stripe.SignatureVerificationError) as exc:
            raise WebhookSignatureError(str(exc)) from exc
        kind = _get(event, "type")
        intent = _get(_get(event, "data"), "object")
        if kind not in ("payment_intent.succeeded", "payment_intent.payment_failed"):
            return ProviderWebhookEvent(kind="ignored", event_id=_get(event, "id"))
        error = _get(intent, "last_payment_error") or {}
        return ProviderWebhookEvent(
            kind="succeeded" if kind == "payment_intent.succeeded" else "failed",
            event_id=_get(event, "id"),
            provider_reference=_get(intent, "id"),
            idempotency_key=_get(_get(intent, "metadata") or {}, "idempotency_key"),
            failure_reason=_get(error, "message"),
            error_code=_get(error, "decline_code") or _get(error, "code") or "",
        )
