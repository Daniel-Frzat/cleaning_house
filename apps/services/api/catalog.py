"""
Service Catalog Admin API — Services Domain (Change Set §36.2، §5)

نقاط النهاية (كلها محمية بـJWT، وكلها ADMIN فقط):
    POST   /api/admin/services          إنشاء نوع خدمة
    GET    /api/admin/services          قائمة الكل (نشط + معطّل)
    GET    /api/admin/services/{id}     قراءة واحد
    PATCH  /api/admin/services/{id}     تعديل الحقول (بما فيها الأسعار)
    DELETE /api/admin/services/{id}     تعطيل ناعم (is_active=False)

    GET    /api/admin/pricing-config    قراءة سعر الكيلومتر العام
    PATCH  /api/admin/pricing-config    تحديث سعر الكيلومتر العام

🔒 سياسة الحالة (موثّقة ومقصودة الاختلاف عن Properties Domain):
   الرفض هنا يعيد 403 وليس 404. في Properties كان الإخفاء ضروريًا لتفادي
   كشف وجود عقار لمستخدم آخر (resource enumeration). هنا الكتالوج مورد
   عام واحد للنظام كله ولا يخص مستخدمًا بعينه، فلا شيء يُكشف بالرفض
   الصريح — والمواصفة تطلب 403 نصًّا.

⚠️ لا توجد هنا أي نقطة نهاية موجّهة للعميل. عرض الخدمات والأسعار
   للـCUSTOMER شأن Booking Domain في مرحلة لاحقة — لا يُبنى الآن.

⚠️ DELETE لا يحذف الصف فعليًا: الخدمة قد يُشار إليها لاحقًا من Bookings.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from ninja import Router
from ninja_jwt.authentication import JWTAuth

from ..services import catalog as svc
from .schemas import (
    ErrorOut,
    PricingConfigOut,
    PricingConfigPatch,
    ServiceTypeIn,
    ServiceTypeOut,
    ServiceTypePatch,
)

router = Router(tags=["Admin — Service Catalog"], auth=JWTAuth())


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
        service = svc.create_service_type(request.user, **payload.dict())
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
def retrieve_service(request, service_id: str):
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
def update_service(request, service_id: str, payload: ServiceTypePatch):
    """
    ⚠️ الأسعار قابلة للتعديل في أي وقت. لا إعادة حساب لأي حجز سابق —
       تجميد السعر على الحجز شأن Booking Domain لاحقًا.
    """
    fields = payload.dict(exclude_unset=True)

    try:
        if fields:
            service = svc.update_service_type(request.user, service_id, **fields)
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
def delete_service(request, service_id: str):
    """
    تعطيل فقط (is_active=False) — الصف يبقى في قاعدة البيانات لأن
    الخدمة قد يُشار إليها لاحقًا من Bookings.
    """
    try:
        service = svc.deactivate_service_type(request.user, service_id)
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
    summary="Retrieve the global price_per_km (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "Returns the single system-wide travel rate used in every price "
        "calculation. This is one value for the whole platform, not one per "
        "service, and the configuration row is created on first access if it does "
        "not exist yet.\n\n"
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
    summary="Update the global price_per_km (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "Sets the single system-wide travel rate (§5). There is no per-service "
        "override.\n\n"
        "**Side effects:** the new rate applies to **future** price calculations "
        "only. Bookings whose price was already frozen at acceptance are never "
        "recalculated."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not an `ADMIN`."},
            422: {"description": "`price_per_km` failed validation."},
        }
    },
)
def update_pricing_config(request, payload: PricingConfigPatch):
    """
    قيمة واحدة للنظام كله (§5) — ليست لكل خدمة.
    """
    try:
        config = svc.update_pricing_config(request.user, payload.price_per_km)
    except svc.CatalogPermissionError as exc:
        return _forbidden(exc)
    except ValidationError as exc:
        return _validation_error(exc)

    return 200, _serialize_config(config)
