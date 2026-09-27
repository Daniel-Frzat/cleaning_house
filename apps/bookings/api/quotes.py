"""Customer quote API for pre-request maximum pricing."""

from ninja import Router
from apps.accounts.authentication import ActiveUserJWTAuth

from ..services import quotes as svc
from ..services.scheduling import service_hours
from ..services.timezone import get_timezone_for_state
from .schemas import ErrorOut, QuoteIn, QuoteOut

router = Router(tags=["Booking Quotes"], auth=ActiveUserJWTAuth())


def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


@router.post(
    "",
    response={201: QuoteOut, 400: ErrorOut, 403: ErrorOut, 404: ErrorOut},
    summary="Create a maximum-price quote (customer only)",
    description=(
        "Creates a short-lived, single-use quote for the selected property and "
        "services. The maximum is frozen from the current pricing configuration; "
        "it is not the final price because travel distance is unknown until a "
        "contractor is assigned. Use the returned `id` as `quote_id` when "
        "creating the booking.\n\n"
        "`service_hours` gives the rule the server enforces at the property: "
        "an on-demand request is accepted only while `is_open_now` is true, "
        "and a scheduled visit must fall between `opens_at` and `closes_at` "
        "local time, at least `min_lead_minutes` ahead."
    ),
)
def create_quote(request, payload: QuoteIn):
    try:
        quote = svc.create_quote(
            request.user,
            property_id=payload.property_id,
            service_selections=[selection.dict() for selection in payload.service_selections],
        )
    except svc.InvalidCustomerRoleError as exc:
        return _error(403, exc.code, str(exc))
    except svc.BookingPermissionError as exc:
        return _error(403, exc.code, str(exc))
    except svc.PropertyNotServiceableError as exc:
        return _error(400, exc.code, str(exc))
    except svc.QuoteError as exc:
        return _error(400, exc.code, str(exc))
    except Exception as exc:
        from apps.properties.services.properties import PropertyNotFoundError, PropertyPermissionError

        if isinstance(exc, PropertyPermissionError):
            return _error(403, "property_forbidden", str(exc))
        if isinstance(exc, PropertyNotFoundError):
            return _error(404, "property_not_found", "Property not found.")
        raise

    address = getattr(quote.property, "address", None)
    timezone_name = get_timezone_for_state(getattr(address, "state", ""))
    return 201, {
        "id": quote.id,
        "property_id": quote.property_id,
        "maximum_total": quote.maximum_total,
        "services_total": quote.services_total,
        "currency": quote.currency,
        "pricing_version": quote.pricing_version,
        "expires_at": quote.expires_at,
        "created_at": quote.created_at,
        "service_hours": service_hours(timezone_name),
    }