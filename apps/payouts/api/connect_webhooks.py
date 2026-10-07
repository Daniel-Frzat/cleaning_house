"""Stripe Connect connected-account webhook endpoint."""

from django.core.exceptions import ImproperlyConfigured
from ninja import Router, Schema

from ..services import connect as svc

router = Router(tags=["Payouts"])


class ErrorOut(Schema):
    code: str
    detail: str


def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


@router.post(
    "/webhooks/stripe-connect",
    auth=None,
    response={200: dict, 400: ErrorOut, 503: ErrorOut},
    summary="Stripe Connect connected-account webhook",
    description=(
        "Verifies the Stripe Connect signing secret and synchronizes "
        "`account.updated`; all other verified event types are acknowledged."
    ),
)
def stripe_connect_webhook(request):
    try:
        outcome = svc.handle_connect_account_webhook(request.body, request.headers)
    except svc.ConnectWebhookRejectedError as exc:
        return _error(400, exc.code, str(exc))
    except (svc.ConnectWebhookNotConfiguredError, ImproperlyConfigured) as exc:
        return _error(503, "webhook_not_configured", str(exc))
    return 200, {"received": True, "outcome": outcome}
