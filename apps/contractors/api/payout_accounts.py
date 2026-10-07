"""Contractor self-service endpoints for payout-account onboarding."""

from django.core.exceptions import ImproperlyConfigured
from ninja import Router, Schema

from apps.accounts.authentication import ActiveUserJWTAuth
from apps.payouts.adapters import PayoutAccountProviderError
from apps.payouts.services import connect as svc

router = Router(tags=["Contractor Payout Account"], auth=ActiveUserJWTAuth())


class OnboardingLinkIn(Schema):
    return_url: str
    refresh_url: str


class OnboardingLinkOut(Schema):
    url: str


class PayoutAccountOut(Schema):
    status: str
    requirements_due: list[str]


class ErrorOut(Schema):
    code: str
    detail: str


def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


@router.post(
    "/payout-account/onboarding-link",
    response={
        200: OnboardingLinkOut,
        400: ErrorOut,
        403: ErrorOut,
        404: ErrorOut,
        503: ErrorOut,
    },
    summary="Create a fresh connected-account onboarding link",
)
def onboarding_link(request, payload: OnboardingLinkIn):
    try:
        return 200, svc.create_onboarding_link(
            request.user, payload.return_url, payload.refresh_url
        )
    except svc.InvalidOnboardingURL as exc:
        return _error(400, exc.code, str(exc))
    except svc.PayoutAccountPermissionError as exc:
        return _error(403, exc.code, str(exc))
    except svc.PayoutAccountNotFoundError as exc:
        return _error(404, exc.code, str(exc))
    except (PayoutAccountProviderError, ImproperlyConfigured) as exc:
        return _error(503, "payout_account_provider_unavailable", str(exc))


@router.get(
    "/payout-account",
    response={200: PayoutAccountOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve own payout-account readiness",
)
def payout_account(request):
    try:
        return 200, svc.get_payout_account(request.user)
    except svc.PayoutAccountPermissionError as exc:
        return _error(403, exc.code, str(exc))
    except svc.PayoutAccountNotFoundError as exc:
        return _error(404, exc.code, str(exc))
