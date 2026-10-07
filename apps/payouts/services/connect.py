"""Provider-neutral contractor payout-account onboarding and event sync."""

import logging

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.utils import timezone

from apps.contractors.models import (
    ContractorPayoutAccount,
    ContractorPayoutAccountStatus,
)
from apps.contractors.services import profile as profile_service

from ..adapters import (
    PayoutAccountProviderError,
    PayoutAccountWebhookError,
    get_payout_adapter,
)
from ..models import PayoutWebhookEvent

logger = logging.getLogger(__name__)


class PayoutAccountError(Exception):
    code = "payout_account_error"


class PayoutAccountPermissionError(PayoutAccountError):
    code = "contractor_profile_forbidden"


class PayoutAccountNotFoundError(PayoutAccountError):
    code = "contractor_profile_not_found"


class InvalidOnboardingURL(PayoutAccountError):
    code = "validation_error"


class ConnectWebhookNotConfiguredError(PayoutAccountError):
    code = "webhook_not_configured"


class ConnectWebhookRejectedError(PayoutAccountError):
    code = "webhook_signature_invalid"


def _require_https_url(value, field_name):
    try:
        URLValidator(schemes=["https"])(value)
    except ValidationError:
        raise InvalidOnboardingURL(f"{field_name} must be an absolute HTTPS URL.")


def _status_for(payouts_enabled, details_submitted):
    if payouts_enabled and details_submitted:
        return ContractorPayoutAccountStatus.ENABLED
    if not details_submitted:
        return ContractorPayoutAccountStatus.PENDING
    return ContractorPayoutAccountStatus.RESTRICTED


def get_payout_account(user):
    try:
        profile = profile_service.get_own_profile(user)
    except profile_service.InvalidContractorRoleError as exc:
        raise PayoutAccountPermissionError(str(exc)) from exc
    except profile_service.ContractorProfilePermissionError as exc:
        raise PayoutAccountPermissionError(str(exc)) from exc
    except profile_service.ContractorProfileNotFoundError as exc:
        raise PayoutAccountNotFoundError(str(exc)) from exc

    account = ContractorPayoutAccount.objects.filter(
        contractor_profile=profile
    ).first()
    if account is None:
        return {
            "status": ContractorPayoutAccountStatus.NOT_STARTED,
            "requirements_due": [],
        }
    return {
        "status": account.status,
        "requirements_due": account.requirements_due,
    }


def create_onboarding_link(user, return_url, refresh_url):
    _require_https_url(return_url, "return_url")
    _require_https_url(refresh_url, "refresh_url")
    try:
        profile = profile_service.get_own_profile(user)
    except profile_service.InvalidContractorRoleError as exc:
        raise PayoutAccountPermissionError(str(exc)) from exc
    except profile_service.ContractorProfilePermissionError as exc:
        raise PayoutAccountPermissionError(str(exc)) from exc
    except profile_service.ContractorProfileNotFoundError as exc:
        raise PayoutAccountNotFoundError(str(exc)) from exc

    adapter = get_payout_adapter()
    provider = getattr(adapter, "provider_name", type(adapter).__name__)
    with transaction.atomic():
        account, _ = ContractorPayoutAccount.objects.select_for_update().get_or_create(
            contractor_profile=profile,
            defaults={"provider": provider},
        )
        if account.provider != provider and account.external_account_id:
            raise PayoutAccountProviderError(
                "The contractor payout account belongs to a different provider."
            )
        if not account.external_account_id:
            provider_account = adapter.create_account(profile)
            external_account_id = provider_account.get("id")
            if not external_account_id:
                raise PayoutAccountProviderError(
                    "The provider did not return an account identifier."
                )
            account.provider = provider
            account.external_account_id = external_account_id
            account.payouts_enabled = bool(
                provider_account.get("payouts_enabled", False)
            )
            account.details_submitted = bool(
                provider_account.get("details_submitted", False)
            )
            account.requirements_due = provider_account.get(
                "requirements_due", []
            )
            account.status = _status_for(
                account.payouts_enabled, account.details_submitted
            )
            account.last_synced_at = timezone.now()
            account.save(
                update_fields=[
                    "provider",
                    "external_account_id",
                    "payouts_enabled",
                    "details_submitted",
                    "requirements_due",
                    "status",
                    "last_synced_at",
                    "updated_at",
                ]
            )
        link = adapter.create_onboarding_link(
            account.external_account_id, return_url, refresh_url
        )
        url = link.get("url")
        if not url:
            raise PayoutAccountProviderError(
                "The provider did not return an onboarding link."
            )
        return {"url": url}


def handle_connect_account_webhook(payload, headers):
    try:
        event = get_payout_adapter().parse_account_event(payload, headers)
    except PayoutAccountWebhookError as exc:
        raise ConnectWebhookRejectedError("Invalid connected-account webhook signature.") from exc
    except ImproperlyConfigured as exc:
        logger.error("Connect webhook received but not configured: %s", exc)
        raise ConnectWebhookNotConfiguredError(
            "Connected-account webhooks are not configured on the server."
        ) from exc

    if not event.event_id:
        raise ConnectWebhookRejectedError("Connected-account webhook has no event id.")

    return _persist_connect_event(event)


@transaction.atomic
def _persist_connect_event(event):
    _, created = PayoutWebhookEvent.objects.get_or_create(
        external_event_id=event.event_id,
        defaults={"event_type": event.kind},
    )
    if not created:
        return "duplicate"

    if event.kind != "account_updated":
        return "ignored"

    account = (
        ContractorPayoutAccount.objects.select_for_update()
        .filter(external_account_id=event.account_reference)
        .first()
    )
    if account is None:
        logger.warning(
            "Connected account update for unknown account (event_id=%s)",
            event.event_id,
        )
        return "unknown_account"

    account.payouts_enabled = event.payouts_enabled
    account.details_submitted = event.details_submitted
    account.requirements_due = event.requirements_due
    account.status = _status_for(
        account.payouts_enabled, account.details_submitted
    )
    account.last_synced_at = timezone.now()
    account.save(
        update_fields=[
            "payouts_enabled",
            "details_submitted",
            "requirements_due",
            "status",
            "last_synced_at",
            "updated_at",
        ]
    )
    return "account_updated"
