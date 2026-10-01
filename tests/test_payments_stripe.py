"""
Stripe (قرار PO — 2026-09-30): المحوّل، حفظ البطاقة، action_payload،
والـwebhook بتوقيع حقيقي.

نداءات Stripe مستبدلة بدوال محلية — لا شبكة ولا مفاتيح. التوقيع في اختبار
الـwebhook يُحسب بخوارزمية Stripe نفسها (HMAC-SHA256)، فالتحقق حقيقي.
"""

import hashlib
import hmac
import json
import time
from decimal import Decimal

import pytest
import stripe

from apps.payments.adapters.base import ChargeOutcome
from apps.payments.adapters.stripe_adapter import StripePaymentAdapter, method_summary_from, to_minor_units
from apps.payments.models import Payment, PaymentCustomer, PaymentStatus
from tests.test_jobs_completion import auth, client, customer  # noqa: F401

CARD = {"id": "pm_card", "customer": "cus_1", "card": {"brand": "visa", "last4": "4242", "wallet": None}}


@pytest.fixture
def stripe_settings(settings):
    settings.PAYMENT_PROVIDER_ADAPTER_CLASS = "apps.payments.adapters.stripe_adapter.StripePaymentAdapter"
    settings.STRIPE_SECRET_KEY = "sk_test_dummy"
    settings.STRIPE_WEBHOOK_SECRET = "whsec_test_secret"
    return settings


@pytest.fixture
def calls(monkeypatch):
    """يسجّل نداءات Stripe ويعيد ردودًا قابلة للتحكم."""
    log = {}

    def record(name, response):
        def fn(*args, **kwargs):
            log.setdefault(name, []).append((args, kwargs))
            return response(*args, **kwargs) if callable(response) else response
        return fn

    def install(name, response):
        obj, attr = name.split(".")
        monkeypatch.setattr(getattr(stripe, obj), attr, record(name, response))

    log["install"] = install
    return log


# ------------------------------------------------------------ وحدات صغيرة
def test_amounts_are_sent_in_cents():
    assert to_minor_units(Decimal("125.00")) == 12500
    assert to_minor_units(Decimal("0.015")) == 2


@pytest.mark.parametrize("wallet, expected, display", [
    (None, "CARD", "Visa •••• 4242"),
    ({"type": "google_pay"}, "GOOGLE_PAY", "Google Pay"),
    ({"type": "apple_pay"}, "APPLE_PAY", "Apple Pay"),
])
def test_wallet_decides_the_method(wallet, expected, display):
    summary = method_summary_from({"card": {"brand": "visa", "last4": "4242", "wallet": wallet}})
    assert summary["type"] == expected and summary["display_name"] == display


# ------------------------------------------------------------ الشحن
def test_off_session_charge_succeeds(stripe_settings, calls):
    calls["install"]("PaymentMethod.retrieve", CARD)
    calls["install"]("PaymentIntent.create", {"id": "pi_1", "status": "succeeded", "client_secret": "s"})
    result = StripePaymentAdapter().charge(
        Decimal("125.00"), "CARD", "booking-x-payment-attempt-1", "pm_card", customer_reference="cus_1"
    )
    assert result.outcome == ChargeOutcome.SUCCEEDED and result.provider_reference == "pi_1"
    kwargs = calls["PaymentIntent.create"][0][1]
    assert kwargs["amount"] == 12500 and kwargs["currency"] == "aud"
    assert kwargs["off_session"] is True and kwargs["confirm"] is True
    assert kwargs["customer"] == "cus_1" and kwargs["idempotency_key"] == "booking-x-payment-attempt-1"
    assert result.method_summary["last4"] == "4242"


def test_unattached_card_is_attached_first(stripe_settings, calls):
    calls["install"]("PaymentMethod.retrieve", {**CARD, "customer": None})
    calls["install"]("PaymentMethod.attach", CARD)
    calls["install"]("PaymentIntent.create", {"id": "pi_1", "status": "succeeded"})
    StripePaymentAdapter().charge(Decimal("10"), "CARD", "k", "pm_card", customer_reference="cus_1")
    assert calls["PaymentMethod.attach"][0][1]["customer"] == "cus_1"


def test_card_of_another_customer_is_refused(stripe_settings, calls):
    calls["install"]("PaymentMethod.retrieve", {**CARD, "customer": "cus_other"})
    result = StripePaymentAdapter().charge(Decimal("10"), "CARD", "k", "pm_card", customer_reference="cus_1")
    assert result.outcome == ChargeOutcome.FAILED and result.error_code == "payment_method_mismatch"
    assert "PaymentIntent.create" not in calls


def test_saved_card_is_used_when_no_reference(stripe_settings, calls):
    calls["install"]("PaymentMethod.list", {"data": [CARD]})
    calls["install"]("PaymentMethod.retrieve", CARD)
    calls["install"]("PaymentIntent.create", {"id": "pi_1", "status": "succeeded"})
    StripePaymentAdapter().charge(Decimal("10"), "CARD", "k", "", customer_reference="cus_1")
    assert calls["PaymentIntent.create"][0][1]["payment_method"] == "pm_card"


def test_decline_is_a_known_failure(stripe_settings, calls):
    calls["install"]("PaymentMethod.retrieve", CARD)

    def decline(*a, **k):
        raise stripe.CardError(
            "Your card was declined.", None, "card_declined",
            json_body={"error": {"code": "card_declined", "decline_code": "insufficient_funds",
                                 "message": "Your card has insufficient funds.",
                                 "payment_intent": {"id": "pi_2", "status": "requires_payment_method"}}},
        )

    calls["install"]("PaymentIntent.create", decline)
    result = StripePaymentAdapter().charge(Decimal("10"), "CARD", "k", "pm_card", customer_reference="cus_1")
    assert result.outcome == ChargeOutcome.FAILED
    assert result.error_code == "insufficient_funds" and result.provider_reference == "pi_2"


def test_off_session_authentication_asks_the_customer(stripe_settings, calls):
    calls["install"]("PaymentMethod.retrieve", CARD)

    def needs_auth(*a, **k):
        raise stripe.CardError(
            "Authentication required.", None, "authentication_required",
            json_body={"error": {"code": "authentication_required",
                                 "payment_intent": {"id": "pi_3", "client_secret": "pi_3_secret_x",
                                                    "status": "requires_payment_method"}}},
        )

    calls["install"]("PaymentIntent.create", needs_auth)
    result = StripePaymentAdapter().charge(Decimal("10"), "CARD", "k", "pm_card", customer_reference="cus_1")
    assert result.outcome == ChargeOutcome.REQUIRES_ACTION
    assert result.action_payload["client_secret"] == "pi_3_secret_x"
    assert result.action_payload["payment_method"] == "pm_card"


def test_network_errors_propagate_as_unknown_outcome(stripe_settings, calls):
    calls["install"]("PaymentMethod.retrieve", CARD)

    def down(*a, **k):
        raise stripe.APIConnectionError("Network is down")

    calls["install"]("PaymentIntent.create", down)
    with pytest.raises(stripe.APIConnectionError):
        StripePaymentAdapter().charge(Decimal("10"), "CARD", "k", "pm_card", customer_reference="cus_1")


def test_confirm_after_3ds(stripe_settings, calls):
    calls["install"]("PaymentIntent.retrieve", {"id": "pi_3", "status": "succeeded", "payment_method": CARD})
    result = StripePaymentAdapter().confirm("pi_3")
    assert result.outcome == ChargeOutcome.SUCCEEDED and result.method_summary["last4"] == "4242"


def test_refund_goes_through_stripe(stripe_settings, calls):
    calls["install"]("Refund.create", {"id": "re_1", "status": "succeeded"})
    result = StripePaymentAdapter().refund("pi_1", Decimal("40.00"), "refund-key")
    assert result.success and calls["Refund.create"][0][1]["amount"] == 4000


# ------------------------------------------------------------ حفظ البطاقة (API)
@pytest.mark.django_db
def test_setup_intent_creates_the_customer_once(client, customer, stripe_settings, calls):
    calls["install"]("Customer.create", {"id": "cus_new"})
    calls["install"]("SetupIntent.create", {"id": "seti_1", "client_secret": "seti_1_secret"})
    calls["install"]("EphemeralKey.create", {"secret": "ek_secret"})

    r = client.post("/api/payments/setup-intent", data=json.dumps({"stripe_version": "2024-06-20"}),
                    content_type="application/json", **auth(customer))
    assert r.status_code == 200, r.content
    assert r.json() == {"client_secret": "seti_1_secret", "setup_intent_reference": "seti_1",
                        "customer_reference": "cus_new", "ephemeral_key": "ek_secret"}
    assert calls["SetupIntent.create"][0][1]["usage"] == "off_session"

    client.post("/api/payments/setup-intent", data="{}", content_type="application/json", **auth(customer))
    assert len(calls["Customer.create"]) == 1
    assert PaymentCustomer.objects.get(user=customer).customer_reference == "cus_new"


# ------------------------------------------------------------ action_payload + webhook
@pytest.fixture
def pending_payment(customer):
    from apps.bookings.models import Booking, BookingStatus
    from apps.properties.models import Property, PropertyType

    prop = Property.objects.create(owner=customer, property_type=PropertyType.HOUSE)
    booking = Booking.objects.create(customer=customer, property=prop, status=BookingStatus.PENDING)
    return Payment.objects.create(
        booking=booking, amount=Decimal("125.00"), method="CARD", status=PaymentStatus.REQUIRES_ACTION,
        provider_reference="pi_9", attempt_number=1,
        action_payload={"type": "stripe_payment_intent", "client_secret": "pi_9_secret", "payment_method": "pm_card"},
    )


@pytest.mark.django_db
def test_owner_sees_the_client_secret_only_while_action_is_required(client, customer, pending_payment):
    url = f"/api/bookings/{pending_payment.booking_id}/payment"
    assert client.get(url, **auth(customer)).json()["action_payload"]["client_secret"] == "pi_9_secret"
    pending_payment.status = PaymentStatus.FAILED
    pending_payment.save(update_fields=["status"])
    assert client.get(url, **auth(customer)).json()["action_payload"] is None


def _signed(payload, secret):
    timestamp = int(time.time())
    signature = hmac.new(secret.encode(), f"{timestamp}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


def _event(kind, intent):
    return json.dumps({"id": "evt_1", "object": "event", "type": kind, "data": {"object": intent}})


@pytest.mark.django_db
def test_webhook_settles_a_payment_with_a_valid_signature(client, stripe_settings, pending_payment, monkeypatch):
    from apps.payments.services import payments as svc

    confirmed = []
    monkeypatch.setattr(svc, "confirm_payment", lambda pid: confirmed.append(pid))
    body = _event("payment_intent.succeeded", {"id": "pi_9", "object": "payment_intent", "metadata": {}})
    r = client.post("/api/payments/webhooks/stripe", data=body, content_type="application/json",
                    HTTP_STRIPE_SIGNATURE=_signed(body, "whsec_test_secret"))
    assert r.status_code == 200, r.content
    assert r.json()["outcome"] == "succeeded"
    pending_payment.refresh_from_db()
    assert pending_payment.status == PaymentStatus.SUCCEEDED and pending_payment.action_payload is None

    again = client.post("/api/payments/webhooks/stripe", data=body, content_type="application/json",
                        HTTP_STRIPE_SIGNATURE=_signed(body, "whsec_test_secret"))
    assert again.json()["outcome"] == "already_settled"


@pytest.mark.django_db
def test_webhook_rejects_a_forged_signature(client, stripe_settings, pending_payment):
    body = _event("payment_intent.succeeded", {"id": "pi_9", "object": "payment_intent"})
    r = client.post("/api/payments/webhooks/stripe", data=body, content_type="application/json",
                    HTTP_STRIPE_SIGNATURE=_signed(body, "whsec_attacker"))
    assert r.status_code == 400 and r.json()["code"] == "webhook_signature_invalid"
    pending_payment.refresh_from_db()
    assert pending_payment.status == PaymentStatus.REQUIRES_ACTION


@pytest.mark.django_db
def test_webhook_failure_marks_failed(client, stripe_settings, pending_payment):
    body = _event("payment_intent.payment_failed", {
        "id": "pi_9", "object": "payment_intent",
        "last_payment_error": {"message": "Authentication failed.", "code": "payment_intent_authentication_failure"},
    })
    r = client.post("/api/payments/webhooks/stripe", data=body, content_type="application/json",
                    HTTP_STRIPE_SIGNATURE=_signed(body, "whsec_test_secret"))
    assert r.json()["outcome"] == "failed"
    pending_payment.refresh_from_db()
    assert pending_payment.status == PaymentStatus.FAILED
    assert pending_payment.failure_reason == "Authentication failed."


@pytest.mark.django_db
def test_webhook_finds_a_payment_by_attempt_key_when_the_reference_was_lost(client, stripe_settings, pending_payment):
    pending_payment.status = PaymentStatus.PROCESSING
    pending_payment.provider_reference = None
    pending_payment.save(update_fields=["status", "provider_reference"])
    key = f"booking-{pending_payment.booking_id}-payment-attempt-1"
    body = _event("payment_intent.succeeded", {"id": "pi_new", "object": "payment_intent",
                                               "metadata": {"idempotency_key": key}})
    r = client.post("/api/payments/webhooks/stripe", data=body, content_type="application/json",
                    HTTP_STRIPE_SIGNATURE=_signed(body, "whsec_test_secret"))
    assert r.json()["outcome"] == "succeeded"
    pending_payment.refresh_from_db()
    assert pending_payment.provider_reference == "pi_new"


@pytest.mark.django_db
def test_other_events_are_acknowledged(client, stripe_settings):
    body = _event("customer.created", {"id": "cus_1", "object": "customer"})
    r = client.post("/api/payments/webhooks/stripe", data=body, content_type="application/json",
                    HTTP_STRIPE_SIGNATURE=_signed(body, "whsec_test_secret"))
    assert r.status_code == 200 and r.json()["outcome"] == "ignored"


def test_decline_at_attach_is_a_known_failure(stripe_settings, calls):
    """اكتُشف في تجربة Stripe الحقيقية: الرفض قد يقع عند ربط البطاقة لا عند الشحن."""
    calls["install"]("PaymentMethod.retrieve", {**CARD, "customer": None})

    def decline(*a, **k):
        raise stripe.CardError(
            "Your card has insufficient funds.", None, "card_declined",
            json_body={"error": {"code": "card_declined", "decline_code": "insufficient_funds",
                                 "message": "Your card has insufficient funds."}},
        )

    calls["install"]("PaymentMethod.attach", decline)
    result = StripePaymentAdapter().charge(Decimal("10"), "CARD", "k", "pm_card", customer_reference="cus_1")
    assert result.outcome == ChargeOutcome.FAILED and result.error_code == "insufficient_funds"
    assert "PaymentIntent.create" not in calls


# ------------------------------------------------------------ أعطال الإعداد → 503 واضح لا 500
@pytest.mark.django_db
def test_setup_with_the_fake_adapter_is_a_clear_503(client, customer, settings):
    """كانت تعيد fake_seti_secret_… فيرفضه Stripe SDK في التطبيق برسالة مربكة."""
    settings.PAYMENT_PROVIDER_ADAPTER_CLASS = "apps.payments.adapters.fake_adapter.FakePaymentAdapter"
    r = client.post("/api/payments/setup-intent", data="{}", content_type="application/json", **auth(customer))
    assert r.status_code == 503 and r.json()["code"] == "payment_setup_unavailable"
    assert "not using a real payment provider" in r.json()["detail"]


@pytest.mark.django_db
def test_missing_stripe_key_is_a_clear_503(client, customer, stripe_settings):
    stripe_settings.STRIPE_SECRET_KEY = ""
    r = client.post("/api/payments/setup-intent", data="{}", content_type="application/json", **auth(customer))
    assert r.status_code == 503 and "STRIPE_SECRET_KEY" in r.json()["detail"]


@pytest.mark.django_db
def test_stripe_rejection_is_a_clear_503(client, customer, stripe_settings, calls):
    def reject(*a, **k):
        raise stripe.AuthenticationError("Invalid API Key provided: sk_test_*rong")

    calls["install"]("Customer.create", reject)
    r = client.post("/api/payments/setup-intent", data="{}", content_type="application/json", **auth(customer))
    assert r.status_code == 503
    assert "AuthenticationError" in r.json()["detail"] and "Invalid API Key" in r.json()["detail"]


@pytest.mark.django_db
def test_webhook_without_signing_secret_is_503_not_500(client, stripe_settings):
    """على الإنتاج قبل ضبط STRIPE_WEBHOOK_SECRET كان الطلب يعيد 500."""
    stripe_settings.STRIPE_WEBHOOK_SECRET = ""
    r = client.post("/api/payments/webhooks/stripe", data="{}", content_type="application/json")
    assert r.status_code == 503 and r.json()["code"] == "webhook_not_configured"
    assert "STRIPE_WEBHOOK_SECRET" in r.json()["detail"]


@pytest.mark.django_db
def test_webhook_without_stripe_key_is_503_not_500(client, stripe_settings):
    stripe_settings.STRIPE_SECRET_KEY = ""
    r = client.post("/api/payments/webhooks/stripe", data="{}", content_type="application/json")
    assert r.status_code == 503 and "STRIPE_SECRET_KEY" in r.json()["detail"]


def test_confirm_keeps_the_card_summary_with_real_stripe_objects(stripe_settings, calls):
    """Stripe ≥15: الكائنات ليست dict — كان confirm يُسقط ملخص البطاقة."""
    intent = stripe.PaymentIntent.construct_from(
        {"id": "pi_5", "status": "succeeded", "payment_method": CARD}, "sk_test_dummy"
    )
    calls["install"]("PaymentIntent.retrieve", intent)
    result = StripePaymentAdapter().confirm("pi_5")
    assert result.success and result.method_summary["display_name"] == "Visa •••• 4242"
