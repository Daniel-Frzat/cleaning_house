import hashlib
import hmac
import json
import time
import ast
from decimal import Decimal
from pathlib import Path

import pytest
import stripe
from django.test import Client

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.contractors.models import (
    ContractorPayoutAccount,
    ContractorPayoutAccountStatus,
    ContractorProfile,
)
from apps.payouts.adapters.stripe_payout_adapter import (
    STRIPE_API_VERSION,
    StripePayoutAdapter,
)
from apps.payouts.models import Payout, PayoutStatus, PayoutWebhookEvent
from apps.payouts.services import payouts as payouts_service
from apps.properties.models import Property, PropertyType
from apps.bookings.models import Booking, BookingStatus
from apps.services.models import ServiceType

WEBHOOK_SECRET = "whsec_connect_test"
RETURN_URL = "https://app.example.test/connect/return"
REFRESH_URL = "https://app.example.test/connect/refresh"


@pytest.fixture
def api_client():
    return Client()


@pytest.fixture
def stripe_connect_settings(settings):
    settings.PAYOUT_PROVIDER_ADAPTER_CLASS = (
        "apps.payouts.adapters.stripe_payout_adapter.StripePayoutAdapter"
    )
    settings.STRIPE_SECRET_KEY = "sk_test_connect_dummy"
    settings.STRIPE_CONNECT_WEBHOOK_SECRET = WEBHOOK_SECRET
    return settings


def make_user(phone, role=ConfirmedRole.CUSTOMER):
    return User.objects.create_user(phone=phone, role=role)


def auth(user):
    tokens = issue_tokens_for_user(user)
    return {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}


@pytest.fixture
def contractor(db):
    user = make_user("+61400013901", ConfirmedRole.CONTRACTOR)
    profile = ContractorProfile.objects.create(user=user, business_name="Connect test")
    return user, profile


@pytest.fixture
def customer(db):
    return make_user("+61400013902")


def connect_event(event_id, event_type, obj):
    return json.dumps(
        {
            "id": event_id,
            "object": "event",
            "api_version": STRIPE_API_VERSION,
            "created": int(time.time()),
            "data": {"object": obj},
            "livemode": False,
            "pending_webhooks": 1,
            "request": {"id": None, "idempotency_key": None},
            "type": event_type,
        },
        separators=(",", ":"),
    ).encode()


def signed_header(payload, secret=WEBHOOK_SECRET):
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.".encode() + payload
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


def test_onboarding_creates_one_express_account_and_fresh_links(
    api_client, contractor, stripe_connect_settings, monkeypatch
):
    calls = {"accounts": [], "links": []}

    def create_account(**kwargs):
        calls["accounts"].append(kwargs)
        return {"id": "acct_connect_1", "details_submitted": False}

    def create_link(**kwargs):
        calls["links"].append(kwargs)
        return {"url": f"https://connect.example.test/{len(calls['links'])}"}

    monkeypatch.setattr(stripe.Account, "create", create_account)
    monkeypatch.setattr(stripe.AccountLink, "create", create_link)
    user, profile = contractor

    first = api_client.post(
        "/api/contractor/payout-account/onboarding-link",
        data=json.dumps({"return_url": RETURN_URL, "refresh_url": REFRESH_URL}),
        content_type="application/json",
        **auth(user),
    )
    second = api_client.post(
        "/api/contractor/payout-account/onboarding-link",
        data=json.dumps({"return_url": RETURN_URL, "refresh_url": REFRESH_URL}),
        content_type="application/json",
        **auth(user),
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["url"] != second.json()["url"]
    assert len(calls["accounts"]) == 1
    assert len(calls["links"]) == 2
    assert calls["accounts"][0]["type"] == "express"
    assert calls["accounts"][0]["country"] == "AU"
    assert calls["accounts"][0]["capabilities"] == {
        "transfers": {"requested": True}
    }
    assert calls["accounts"][0]["stripe_version"] == STRIPE_API_VERSION
    assert calls["accounts"][0]["idempotency_key"] == (
        f"contractor-connect-account-{profile.pk}"
    )
    account = ContractorPayoutAccount.objects.get(contractor_profile=profile)
    assert account.external_account_id == "acct_connect_1"
    assert account.status == ContractorPayoutAccountStatus.PENDING


def test_non_contractor_cannot_start_onboarding(api_client, customer):
    response = api_client.post(
        "/api/contractor/payout-account/onboarding-link",
        data=json.dumps({"return_url": RETURN_URL, "refresh_url": REFRESH_URL}),
        content_type="application/json",
        **auth(customer),
    )
    assert response.status_code == 403


def test_payout_account_read_does_not_expose_provider_identifier(
    api_client, contractor
):
    user, profile = contractor
    ContractorPayoutAccount.objects.create(
        contractor_profile=profile,
        provider="stripe",
        external_account_id="acct_private",
        status=ContractorPayoutAccountStatus.PENDING,
        requirements_due=["individual.verification.document"],
    )
    response = api_client.get("/api/contractor/payout-account", **auth(user))
    assert response.status_code == 200
    assert response.json() == {
        "status": "PENDING",
        "requirements_due": ["individual.verification.document"],
    }


def test_connect_webhook_updates_account_and_is_idempotent(
    api_client, contractor, stripe_connect_settings
):
    user, profile = contractor
    ContractorPayoutAccount.objects.create(
        contractor_profile=profile,
        provider="stripe",
        external_account_id="acct_sync",
        status=ContractorPayoutAccountStatus.PENDING,
    )
    payload = connect_event(
        "evt_account_sync",
        "account.updated",
        {
            "id": "acct_sync",
            "object": "account",
            "payouts_enabled": True,
            "details_submitted": True,
            "requirements": {"currently_due": []},
        },
    )
    signature = signed_header(payload)

    response = api_client.post(
        "/api/payments/webhooks/stripe-connect",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=signature,
    )
    account = ContractorPayoutAccount.objects.get(contractor_profile=profile)
    assert response.status_code == 200
    assert response.json()["outcome"] == "account_updated"
    assert account.status == ContractorPayoutAccountStatus.ENABLED
    assert account.payouts_enabled and account.details_submitted

    repeated = api_client.post(
        "/api/payments/webhooks/stripe-connect",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=signature,
    )
    assert repeated.status_code == 200
    assert repeated.json()["outcome"] == "duplicate"
    assert PayoutWebhookEvent.objects.filter(
        external_event_id="evt_account_sync"
    ).count() == 1


@pytest.mark.django_db
def test_connect_webhook_rejects_bad_signature(api_client, stripe_connect_settings):
    payload = connect_event(
        "evt_invalid",
        "account.updated",
        {"id": "acct_any", "object": "account"},
    )
    response = api_client.post(
        "/api/payments/webhooks/stripe-connect",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=signed_header(payload, "whsec_wrong"),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "webhook_signature_invalid"
    assert not PayoutWebhookEvent.objects.filter(
        external_event_id="evt_invalid"
    ).exists()


@pytest.mark.django_db
def test_connect_webhook_accepts_any_configured_signing_secret(
    api_client, stripe_connect_settings
):
    stripe_connect_settings.STRIPE_CONNECT_WEBHOOK_SECRET = [
        "whsec_old_domain",
        WEBHOOK_SECRET,
    ]
    payload = connect_event(
        "evt_second_secret",
        "account.updated",
        {"id": "acct_any", "object": "account"},
    )
    response = api_client.post(
        "/api/payments/webhooks/stripe-connect",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=signed_header(payload),
    )
    assert response.status_code == 200, response.content


@pytest.mark.django_db
def test_connect_webhook_unknown_account_is_acknowledged(
    api_client, stripe_connect_settings
):
    payload = connect_event(
        "evt_unknown",
        "account.updated",
        {
            "id": "acct_unknown",
            "object": "account",
            "payouts_enabled": True,
            "details_submitted": True,
            "requirements": {"currently_due": []},
        },
    )
    response = api_client.post(
        "/api/payments/webhooks/stripe-connect",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=signed_header(payload),
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "unknown_account"


@pytest.fixture
def completed_booking(db, contractor, customer):
    _, profile = contractor
    service = ServiceType.objects.create(
        name="Connect test cleaning",
        room_price=Decimal("25.00"),
        base_price=Decimal("100.00"),
    )
    property_obj = Property.objects.create(
        owner=customer, label="Connect property", property_type=PropertyType.HOUSE
    )
    booking = Booking.objects.create(
        customer=customer,
        property=property_obj,
        status=BookingStatus.CONFIRMED,
        computed_price=Decimal("123.45"),
        assigned_contractor=profile,
    )
    from apps.jobs.models import Job, JobStatus
    from apps.payments.models import Payment, PaymentStatus

    Payment.objects.create(
        booking=booking,
        amount=booking.computed_price,
        status=PaymentStatus.SUCCEEDED,
    )
    Job.objects.create(
        booking=booking,
        status=JobStatus.COMPLETED,
    )
    return booking


def enable_account(profile, account_id="acct_enabled"):
    return ContractorPayoutAccount.objects.create(
        contractor_profile=profile,
        provider="stripe",
        external_account_id=account_id,
        status=ContractorPayoutAccountStatus.ENABLED,
        payouts_enabled=True,
        details_submitted=True,
    )


def test_transfer_uses_minor_units_metadata_and_stable_payout_key(
    contractor, completed_booking, stripe_connect_settings, monkeypatch
):
    _, profile = contractor
    account = enable_account(profile)
    calls = []
    results = {}

    def create_transfer(**kwargs):
        calls.append(kwargs)
        return results.setdefault(
            kwargs["idempotency_key"], {"id": "tr_created_once"}
        )

    monkeypatch.setattr(stripe.Transfer, "create", create_transfer)
    payout = payouts_service.release_payout_for_booking(completed_booking)
    assert payout.status == PayoutStatus.SUCCEEDED

    adapter = StripePayoutAdapter()
    repeated = adapter.payout(
        payout.amount,
        account.external_account_id,
        str(payout.id),
        payout_id=payout.id,
        booking_id=completed_booking.id,
    )
    assert repeated.provider_reference == "tr_created_once"
    assert len(calls) == 2
    assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"] == str(payout.id)
    assert calls[0]["amount"] == 12345
    assert calls[0]["currency"] == "aud"
    assert calls[0]["destination"] == account.external_account_id
    assert calls[0]["transfer_group"] == str(completed_booking.id)
    assert calls[0]["metadata"] == {
        "payout_id": str(payout.id),
        "booking_id": str(completed_booking.id),
    }
    assert calls[0]["stripe_version"] == STRIPE_API_VERSION


def test_transfer_timeout_leaves_payout_pending(
    contractor, completed_booking, stripe_connect_settings, monkeypatch
):
    _, profile = contractor
    enable_account(profile)

    def timeout(**kwargs):
        raise stripe.APIConnectionError("network timeout")

    monkeypatch.setattr(stripe.Transfer, "create", timeout)
    payout = payouts_service.release_payout_for_booking(completed_booking)
    payout.refresh_from_db()
    assert payout.status == PayoutStatus.PENDING
    assert payout.failure_reason.startswith("provider_error:")


def test_payout_without_enabled_account_stays_pending_without_transfer(
    contractor, completed_booking, stripe_connect_settings, monkeypatch
):
    _, profile = contractor
    enable_account(profile)
    ContractorPayoutAccount.objects.filter(contractor_profile=profile).update(
        status=ContractorPayoutAccountStatus.RESTRICTED,
        payouts_enabled=False,
    )

    def forbidden_transfer(**kwargs):
        pytest.fail("Stripe Transfer must not be called for a non-enabled account.")

    monkeypatch.setattr(stripe.Transfer, "create", forbidden_transfer)
    payout = payouts_service.release_payout_for_booking(completed_booking)
    assert payout.status == PayoutStatus.PENDING


def test_transfer_retries_keep_same_idempotency_key(monkeypatch, stripe_connect_settings):
    calls = []
    created = {}

    def create_transfer(**kwargs):
        calls.append(kwargs)
        key = kwargs["idempotency_key"]
        if key not in created:
            created[key] = {"id": "tr_one"}
        return created[key]

    monkeypatch.setattr(stripe.Transfer, "create", create_transfer)
    adapter = StripePayoutAdapter()
    for _ in range(2):
        result = adapter.payout(
            Decimal("10.00"),
            "acct_worker",
            "payout-record-uuid",
            payout_id="payout-record-uuid",
            booking_id="booking-uuid",
        )
        assert result.provider_reference == "tr_one"
    assert len(created) == 1
    assert [call["idempotency_key"] for call in calls] == [
        "payout-record-uuid",
        "payout-record-uuid",
    ]


def test_new_api_modules_do_not_access_the_orm():
    api_paths = (
        Path("apps/contractors/api/payout_accounts.py"),
        Path("apps/payouts/api/connect_webhooks.py"),
    )
    for path in api_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(
            isinstance(node, ast.Attribute) and node.attr == "objects"
            for node in ast.walk(tree)
        ), path
