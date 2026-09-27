"""
Service Catalog Admin API — Services Domain (Change Set §36.2، §5)

نقاط النهاية (كلها محمية بـJWT، وكلها ADMIN فقط):
    POST   /api/admin/services          إنشاء نوع خدمة
    GET    /api/admin/services          قائمة الكل (نشط + معطّل)
    GET    /api/admin/services/{id}     قراءة واحد
    PATCH  /api/admin/services/{id}     تعديل الحقول (بما فيها الأسعار)
    DELETE /api/admin/services/{id}     تعطيل ناعم (is_active=False)

    GET    /api/admin/pricing-config    قراءة إعداد التسعير العام
    PATCH  /api/admin/pricing-config    تحديث جزئي لإعداد التسعير العام

🔒 سياسة الحالة (موثّقة ومقصودة الاختلاف عن Properties Domain):
   الرفض هنا يعيد 403 وليس 404. في Properties كان الإخفاء ضروريًا لتفادي
   كشف وجود عقار لمستخدم آخر (resource enumeration). هنا الكتالوج مورد
   عام واحد للنظام كله ولا يخص مستخدمًا بعينه، فلا شيء يُكشف بالرفض
   الصريح — والمواصفة تطلب 403 نصًّا.

⚠️ لا توجد هنا أي نقطة نهاية موجّهة للعميل. القراءة العامة تعيش في
   api/public_catalog.py (GET /api/services) وتعيد الاسم والوصف فقط —
   الأسعار الخام تبقى حكرًا على المسارات الإدارية في هذا الملف.

⚠️ DELETE لا يحذف الصف فعليًا: الخدمة قد يُشار إليها لاحقًا من Bookings.
"""

import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from ninja import Router
from apps.accounts.authentication import ActiveUserJWTAuth

from ..services import catalog as svc
from .schemas import (
    ErrorOut,
    PricingConfigOut,
    PricingConfigPatch,
    ServiceTypeIn,
    ServiceTypeOut,
    ServiceTypePatch,
)

router = Router(tags=["Admin — Service Catalog"], auth=ActiveUserJWTAuth())


# ------------------------------------------------------------
# أدوات مشتركة
# ------------------------------------------------------------
def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


def _forbidden(exc):
    return _error(403, exc.code, str(exc))


def _not_found():
    return _error(404, "service_not_found", "Service type not found.")


def _validation_error(exc):
    """يحوّل ValidationError من الـModel إلى رد 422 مفهوم."""
    if hasattr(exc, "message_dict"):
        detail = "; ".join(
            f"{field}: {' '.join(msgs)}" for field, msgs in exc.message_dict.items()
        )
    else:
        detail = "; ".join(exc.messages)
    return _error(422, "validation_error", detail)


def _serialize(service):
    return {
        "id": service.id,
        "name": service.name,
        "description": service.description,
        "room_price": service.room_price,
        "base_price": service.base_price,
        "is_active": service.is_active,
        "created_at": service.created_at,
        "updated_at": service.updated_at,
    }


def _serialize_config(config):
    return {
        "price_per_km": config.price_per_km,
        "included_distance_km": config.included_distance_km,
        "maximum_travel_fee": config.maximum_travel_fee,
        "rounding_rule": config.rounding_rule,
        "dispatch_offer_ttl_seconds": config.dispatch_offer_ttl_seconds,
        "service_hours_enabled": config.service_hours_enabled,
        "service_hours_start": config.service_hours_start,
        "service_hours_end": config.service_hours_end,
        "currency": config.currency,
        "pricing_version": config.pricing_version,
        "active_from": config.active_from,
        "updated_at": config.updated_at,
    }


# ============================================================
# ServiceType
# ============================================================
@router.post(
    "/services",
    response={201: ServiceTypeOut, 403: ErrorOut, 422: ErrorOut},
    summary="Create a service type (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "Adds a bookable service to the catalog with its own `base_price` and "
        "`room_price`. Service names are unique. The service starts active and is "
        "immediately bookable.\n\n"
        "**Side effects:** none beyond creating the catalog entry — existing "
        "bookings are untouched."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not an `ADMIN`."},
            422: {"description": "Validation failed, or a service with this name already exists."},
        }
    },
)
def create_service(request, payload: ServiceTypeIn):
    try:
        service = svc.create_service_type(request.user, **payload.dict(), request=request)
    except svc.CatalogPermissionError as exc:
        return _forbidden(exc)
    except ValidationError as exc:
        return _validation_error(exc)
    except IntegrityError:
        return _error(422, "validation_error", "name: Service name already exists.")

    return 201, _serialize(service)


@router.get(
    "/services",
    response={200: list[ServiceTypeOut], 403: ErrorOut},
    summary="List all service types, active and inactive (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "Returns the whole catalog **unfiltered**, including soft-deleted "
        "services, so that an administrator can find an inactive service and "
        "re-activate it.\n\n"
        "**Side effects:** none — read-only."
    ),
    openapi_extra={"responses": {403: {"description": "The caller is not an `ADMIN`."}}},
)
def list_services(request):
    """
    يعيد الكل بلا ترشيح — الإدارة تحتاج رؤية المعطّل لإعادة تفعيله.
    """
    try:
        services = svc.list_service_types(request.user)
    except svc.CatalogPermissionError as exc:
        return _forbidden(exc)

    return 200, [_serialize(s) for s in services]


@router.get(
    "/services/{service_id}",
    response={200: ServiceTypeOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve one service type (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "Returns one service type with its prices, whether it is active or not.\n\n"
        "**Side effects:** none — read-only."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not an `ADMIN`."},
            404: {"description": "No service type with this id."},
        }
    },
)
def retrieve_service(request, service_id: uuid.UUID):
    try:
        service = svc.get_service_type(request.user, service_id)
    except svc.CatalogPermissionError as exc:
        return _forbidden(exc)
    except svc.ServiceTypeNotFoundError:
        return _not_found()

    return 200, _serialize(service)


@router.patch(
    "/services/{service_id}",
    response={200: ServiceTypeOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut},
    summary="Update a service type, prices included (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "Partial update — only the fields present in the body are changed. "
        "`base_price` and `room_price` may be edited at any time.\n\n"
        "**Side effects:** the new prices apply to **future** price calculations "
        "only. Bookings whose price was already frozen at acceptance are never "
        "recalculated, so changing a price here does not alter money already owed."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not an `ADMIN`."},
            404: {"description": "No service type with this id."},
            422: {"description": "Validation failed, or another service already uses this name."},
        }
    },
)
def update_service(request, service_id: uuid.UUID, payload: ServiceTypePatch):
    """
    ⚠️ الأسعار قابلة للتعديل في أي وقت. لا إعادة حساب لأي حجز سابق —
       تجميد السعر على الحجز شأن Booking Domain لاحقًا.
    """
    fields = payload.dict(exclude_unset=True)

    try:
        if fields:
            service = svc.update_service_type(request.user, service_id, request=request, **fields)
        else:
            # لا شيء لتعديله — نتحقق من الصلاحية والوجود على الأقل
            service = svc.get_service_type(request.user, service_id)
    except svc.CatalogPermissionError as exc:
        return _forbidden(exc)
    except svc.ServiceTypeNotFoundError:
        return _not_found()
    except ValidationError as exc:
        return _validation_error(exc)
    except IntegrityError:
        return _error(422, "validation_error", "name: Service name already exists.")
    except svc.CatalogError as exc:
        return _error(422, exc.code, str(exc))

    return 200, _serialize(service)


@router.delete(
    "/services/{service_id}",
    response={200: ServiceTypeOut, 403: ErrorOut, 404: ErrorOut},
    summary="Soft-delete a service type (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "**Soft delete:** the row is kept and `is_active` is set to `false`, "
        "because existing bookings still reference the service. The deactivated "
        "service is returned in the response and can be re-activated with a "
        "`PATCH`.\n\n"
        "**Side effects:** the service can no longer be booked — a new booking "
        "that includes it is rejected with `400`. Bookings already placed are "
        "unaffected."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not an `ADMIN`."},
            404: {"description": "No service type with this id."},
        }
    },
)
def delete_service(request, service_id: uuid.UUID):
    """
    تعطيل فقط (is_active=False) — الصف يبقى في قاعدة البيانات لأن
    الخدمة قد يُشار إليها لاحقًا من Bookings.
    """
    try:
        service = svc.deactivate_service_type(request.user, service_id, request=request)
    except svc.CatalogPermissionError as exc:
        return _forbidden(exc)
    except svc.ServiceTypeNotFoundError:
        return _not_found()

    return 200, _serialize(service)


# ============================================================
# PricingConfig — سعر الكيلومتر العام
# ============================================================
@router.get(
    "/pricing-config",
    response={200: PricingConfigOut, 403: ErrorOut},
    summary="Retrieve the global pricing configuration (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "Returns the single system-wide travel pricing used in every price "
        "calculation: travel fee = max(0, distance - `included_distance_km`) x "
        "`price_per_km`, capped at `maximum_travel_fee`. The customer's "
        "pre-request maximum is services total + `maximum_travel_fee`. One "
        "configuration for the whole platform, created on first access.\n\n"
        "**Side effects:** none — read-only."
    ),
    openapi_extra={"responses": {403: {"description": "The caller is not an `ADMIN`."}}},
)
def retrieve_pricing_config(request):
    try:
        config = svc.get_pricing_config(request.user)
    except svc.CatalogPermissionError as exc:
        return _forbidden(exc)

    return 200, _serialize_config(config)


@router.patch(
    "/pricing-config",
    response={200: PricingConfigOut, 403: ErrorOut, 422: ErrorOut},
    summary="Update the global pricing configuration (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "Partial update: send only the fields to change (at least one). There "
        "is no per-service override. `currency` is not editable.\n\n"
        "**Service hours:** off by default (requests accepted at any hour). "
        "With `service_hours_enabled: true`, on-demand requests and scheduled "
        "visits must fall between `service_hours_start` and `service_hours_end`, "
        "property-local; an end earlier than the start is an overnight window.\n\n"
        "**Side effects:** applies to **future** quotes and offers only; frozen "
        "prices are never recalculated. Any pricing change increments "
        "`pricing_version` (offer TTL and service hours do not). "
        "Recorded in the audit log."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not an `ADMIN`."},
            422: {"description": "A field failed validation, or no field was sent."},
        }
    },
)
def update_pricing_config(request, payload: PricingConfigPatch):
    """
    قيمة واحدة للنظام كله (§5) — ليست لكل خدمة.
    """
    try:
        config = svc.update_pricing_config(
            request.user, request=request, **payload.dict(exclude_none=True)
        )
    except svc.CatalogPermissionError as exc:
        return _forbidden(exc)
    except ValidationError as exc:
        return _validation_error(exc)

    return 200, _serialize_config(config)
