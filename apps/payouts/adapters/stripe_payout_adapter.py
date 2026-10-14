"""Stripe Connect Express account onboarding and separate-charge transfers."""

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import (
    BasePayoutProviderAdapter,
    PayoutAccountProviderError,
    PayoutAccountWebhookError,
    PayoutAccountWebhookEvent,
    PayoutResult,
)

STRIPE_API_VERSION = "2026-08-26.dahlia"


def _get(obj, key, default=None):
    if obj is None:
        return default
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return getattr(obj, key, default)


def _minor_units(amount):
    return int((Decimal(amount) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _webhook_secrets(value):
    """سرّ واحد (نص) أو عدة أسرار (قائمة أو نص مفصول بفواصل)."""
    if isinstance(value, str):
        value = value.split(",")
    return [secret.strip() for secret in value or () if secret and secret.strip()]


class StripePayoutAdapter(BasePayoutProviderAdapter):
    provider_name = "stripe"

    def __init__(self):
        import stripe

        self.stripe = stripe
        self.api_key = getattr(settings, "STRIPE_SECRET_KEY", "") or ""
        self.webhook_secrets = _webhook_secrets(getattr(settings, "STRIPE_CONNECT_WEBHOOK_SECRET", ""))
        if not self.api_key:
            raise ImproperlyConfigured("STRIPE_SECRET_KEY is not set.")

    def create_account(self, contractor):
        try:
            account = self.stripe.Account.create(
                api_key=self.api_key,
                stripe_version=STRIPE_API_VERSION,
                type="express",
                country="AU",
                capabilities={"transfers": {"requested": True}},
                metadata={"contractor_profile_id": str(contractor.pk)},
                idempotency_key=f"contractor-connect-account-{contractor.pk}",
            )
        except self.stripe.StripeError as exc:
            raise PayoutAccountProviderError(
                f"Connected account could not be created ({type(exc).__name__})."
            ) from exc
        return {
            "id": _get(account, "id"),
            "payouts_enabled": bool(_get(account, "payouts_enabled", False)),
            "details_submitted": bool(_get(account, "details_submitted", False)),
            "requirements_due": list(
                _get(_get(account, "requirements") or {}, "currently_due") or []
            ),
        }

    def create_onboarding_link(self, account_id, return_url, refresh_url):
        try:
            link = self.stripe.AccountLink.create(
                api_key=self.api_key,
                stripe_version=STRIPE_API_VERSION,
                account=account_id,
                return_url=return_url,
                refresh_url=refresh_url,
                type="account_onboarding",
            )
        except self.stripe.StripeError as exc:
            raise PayoutAccountProviderError(
                f"Connected account onboarding link could not be created ({type(exc).__name__})."
            ) from exc
        return {"url": _get(link, "url")}

    def payout(
        self,
        amount,
        contractor_reference,
        idempotency_key,
        payout_id=None,
        booking_id=None,
    ):
        transfer = self.stripe.Transfer.create(
            api_key=self.api_key,
            stripe_version=STRIPE_API_VERSION,
            amount=_minor_units(amount),
            currency="aud",
            destination=contractor_reference,
            transfer_group=str(booking_id),
            metadata={
                "payout_id": str(payout_id),
                "booking_id": str(booking_id),
            },
            idempotency_key=idempotency_key,
        )
        return PayoutResult(
            success=True,
            provider_reference=_get(transfer, "id"),
        )

    def parse_account_event(self, payload, headers):
        if not self.webhook_secrets:
            raise ImproperlyConfigured("STRIPE_CONNECT_WEBHOOK_SECRET is not set.")
        event, error = None, None
        for secret in self.webhook_secrets:
            try:
                event = self.stripe.Webhook.construct_event(
                    payload,
                    headers.get("Stripe-Signature", ""),
                    secret,
                    api_key=self.api_key,
                )
                break
            except self.stripe.SignatureVerificationError as exc:
                error = exc
            except ValueError as exc:
                error = exc
                break
        if event is None:
            raise PayoutAccountWebhookError(
                "Invalid connected-account webhook signature."
            ) from error

        kind = _get(event, "type")
        event_id = _get(event, "id")
        if kind != "account.updated":
            return PayoutAccountWebhookEvent(kind="ignored", event_id=event_id)

        account = _get(_get(event, "data"), "object")
        requirements = _get(account, "requirements") or {}
        return PayoutAccountWebhookEvent(
            kind="account_updated",
            event_id=event_id,
            account_reference=_get(account, "id"),
            payouts_enabled=_get(account, "payouts_enabled", False),
            details_submitted=_get(account, "details_submitted", False),
            requirements_due=_get(requirements, "currently_due") or [],
        )
