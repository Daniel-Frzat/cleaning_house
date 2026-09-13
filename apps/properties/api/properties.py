"""
Properties API — Properties & Address Domain (Phase 1)

نقاط النهاية (كلها محمية بـJWT):
    POST   /api/properties        إنشاء عقار + عنوانه في طلب واحد
    GET    /api/properties        قائمة عقارات المستخدم الحالي فقط
    GET    /api/properties/{id}   قراءة عقار واحد
    PATCH  /api/properties/{id}   تعديل عقار و/أو عنوانه
    DELETE /api/properties/{id}   إلغاء تفعيل (soft delete)

طبقتا حماية منفصلتان عمدًا:
  1) بوابة الدور (هنا): CUSTOMER فقط — CONTRACTOR/ADMIN يحصلون على 403.
  2) فحص الملكية (طبقة الخدمة): العقار يخص هذا المستخدم تحديدًا.
لا يُستغنى عن أي منهما: الدور يحمي نقطة النهاية، والملكية تحمي الكائن.

🔒 سياسة الحالة (موثّقة): محاولة الوصول إلى عقار مملوك لمستخدم آخر تُعيد
   404 وليس 403 — حتى لا يكشف الرد وجود عقار لا يملكه الطالب
   (تفادي resource enumeration). عدم وجود العقار أصلًا يعيد 404 أيضًا،
   فالردّان لا يمكن التمييز بينهما.

⚠️ DELETE لا يحذف الصف فعليًا: العقار قد يُشار إليه لاحقًا من
   Bookings/QualityGuarantee.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from ninja import Router
from ninja_jwt.authentication import JWTAuth

from apps.accounts.roles import ConfirmedRole

from ..services import properties as svc
from .schemas import ErrorOut, PropertyIn, PropertyOut, PropertyPatch

router = Router(tags=["Properties"], auth=JWTAuth())


# ------------------------------------------------------------
# أدوات مشتركة
# ------------------------------------------------------------
def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


def _not_found():
    """رد موحّد لكل من: غير موجود، أو موجود لكن ليس ملكًا للطالب."""
    return _error(404, "property_not_found", "Property not found.")


def _require_customer(request):
    """
    بوابة الدور — منفصلة عن فحص الملكية.

    تعيد None عند السماح، أو استجابة 403 جاهزة عند الرفض.
    """
    user = request.user
    if user.role != ConfirmedRole.CUSTOMER:
        return _error(
            403, "invalid_owner_role", "Only customers can manage properties."
        )
    return None


def _validation_error(exc):
    """يحوّل ValidationError من الـModel إلى رد 422 مفهوم."""
    if hasattr(exc, "message_dict"):
        detail = "; ".join(
            f"{field}: {' '.join(msgs)}" for field, msgs in exc.message_dict.items()
        )
    else:
        detail = "; ".join(exc.messages)
    return _error(422, "validation_error", detail)


def _serialize(prop):
    """يحوّل العقار إلى شكل الاستجابة (العنوان قد يكون غير موجود)."""
    address = getattr(prop, "address", None)
    return {
        "id": prop.id,
        "owner_id": prop.owner_id,
        "label": prop.label,
        "property_type": prop.property_type,
        "is_active": prop.is_active,
        "created_at": prop.created_at,
        "updated_at": prop.updated_at,
        "address": None
        if address is None
        else {
            "id": address.id,
            "street_address": address.street_address,
            "suburb": address.suburb,
            "state": address.state,
            "postcode": address.postcode,
            "country": address.country,
            "latitude": address.latitude,
            "longitude": address.longitude,
            "raw_input": address.raw_input,
        },
    }


# ------------------------------------------------------------
# POST /properties
# ------------------------------------------------------------
@router.post(
    "",
    response={201: PropertyOut, 403: ErrorOut, 422: ErrorOut},
    summary="Create a property with its address",
    description=(
        "**Who may call:** `CUSTOMER` only.\n\n"
        "Creates a property together with its address in a single transaction — "
        "there is no separate address endpoint, and a property is never stored "
        "without one.\n\n"
        "The new property is owned by the caller and starts active.\n\n"
        "**Side effects:** none beyond persisting the property and its address."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not a `CUSTOMER`."},
            422: {"description": "The property or address failed validation."},
        }
    },
)
def create_property(request, payload: PropertyIn):
    denied = _require_customer(request)
    if denied:
        return denied

    try:
        with transaction.atomic():
            prop = svc.create_property(
                request.user,
                property_type=payload.property_type,
                label=payload.label,
                address=payload.address.dict(),
            )
    except ValidationError as exc:
        return _validation_error(exc)
    except svc.InvalidOwnerRoleError as exc:
        return _error(403, exc.code, "Only customers can manage properties.")

    prop.refresh_from_db()
    return 201, _serialize(prop)


# ------------------------------------------------------------
# GET /properties
# ------------------------------------------------------------
@router.get(
    "",
    response={200: list[PropertyOut], 403: ErrorOut},
    summary="List own properties",
    description=(
        "**Who may call:** `CUSTOMER` only — the list is always scoped to the "
        "caller's own properties.\n\n"
        "Returns **active properties only**. Deactivated ones are excluded here "
        "but still exist and can still be referenced by past bookings.\n\n"
        "**Side effects:** none — read-only."
    ),
    openapi_extra={"responses": {403: {"description": "The caller is not a `CUSTOMER`."}}},
)
def list_properties(request):
    """
    يعيد العقارات النشطة فقط للمستخدم الحالي.

    العقارات المُلغى تفعيلها (is_active=False) لا تظهر هنا، لكنها تبقى
    موجودة في قاعدة البيانات.
    """
    denied = _require_customer(request)
    if denied:
        return denied

    props = svc.list_properties(request.user).filter(is_active=True)
    return 200, [_serialize(p) for p in props]


# ------------------------------------------------------------
# GET /properties/{id}
# ------------------------------------------------------------
@router.get(
    "/{property_id}",
    response={200: PropertyOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve one own property",
    description=(
        "**Who may call:** `CUSTOMER` only, and only for a property they own.\n\n"
        "**Side effects:** none — read-only.\n\n"
        "A property that belongs to another customer returns the same `404` as one "
        "that does not exist, so the response cannot be used to discover other "
        "customers' properties."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not a `CUSTOMER`."},
            404: {"description": "No such property, or it belongs to another customer."},
        }
    },
)
def retrieve_property(request, property_id: str):
    denied = _require_customer(request)
    if denied:
        return denied

    try:
        prop = svc.get_property(request.user, property_id)
    except (svc.PropertyPermissionError, svc.PropertyNotFoundError):
        # 🔒 نفس الرد للحالتين — لا نكشف وجود عقار لغير مالكه
        return _not_found()

    return 200, _serialize(prop)


# ------------------------------------------------------------
# PATCH /properties/{id}
# ------------------------------------------------------------
@router.patch(
    "/{property_id}",
    response={200: PropertyOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut},
    summary="Update own property and/or its address",
    description=(
        "**Who may call:** `CUSTOMER` only, and only for a property they own.\n\n"
        "Partial update: only the fields present in the body are changed, and the "
        "nested `address` object follows the same rule. The property and its "
        "address are updated in one transaction.\n\n"
        "**Side effects:** editing the address changes where future bookings for "
        "this property are dispatched from. Bookings already made are not "
        "recalculated."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not a `CUSTOMER`."},
            404: {"description": "No such property, or it belongs to another customer."},
            422: {"description": "The submitted property or address fields failed validation."},
        }
    },
)
def update_property(request, property_id: str, payload: PropertyPatch):
    denied = _require_customer(request)
    if denied:
        return denied

    data = payload.dict(exclude_unset=True)
    address_data = data.pop("address", None)

    try:
        with transaction.atomic():
            if data:
                svc.update_property(request.user, property_id, **data)
            elif address_data is None:
                # لا شيء لتعديله — نتحقق من الملكية على الأقل
                svc.get_property(request.user, property_id)

            if address_data:
                # الحقول غير المُرسلة تبقى كما هي
                address_fields = {
                    k: v for k, v in address_data.items() if v is not None
                }
                if address_fields:
                    svc.update_address(request.user, property_id, **address_fields)

            prop = svc.get_property(request.user, property_id)
    except (svc.PropertyPermissionError, svc.PropertyNotFoundError):
        return _not_found()
    except ValidationError as exc:
        return _validation_error(exc)
    except svc.PropertyError as exc:
        return _error(422, exc.code, str(exc))

    return 200, _serialize(prop)


# ------------------------------------------------------------
# DELETE /properties/{id}
# ------------------------------------------------------------
@router.delete(
    "/{property_id}",
    response={200: PropertyOut, 403: ErrorOut, 404: ErrorOut},
    summary="Deactivate own property (soft delete)",
    description=(
        "**Who may call:** `CUSTOMER` only, and only for a property they own.\n\n"
        "**Soft delete:** the row is kept and `is_active` is set to `false`. "
        "Nothing is erased, because past bookings still reference this property. "
        "The deactivated property is returned in the response.\n\n"
        "**Side effects:** the property disappears from `GET /api/properties` and "
        "can no longer be used for new bookings."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not a `CUSTOMER`."},
            404: {"description": "No such property, or it belongs to another customer."},
        }
    },
)
def delete_property(request, property_id: str):
    """
    إلغاء تفعيل فقط (is_active=False) — الصف يبقى في قاعدة البيانات
    لأن العقار قد يُشار إليه لاحقًا من Bookings/QualityGuarantee.
    """
    denied = _require_customer(request)
    if denied:
        return denied

    try:
        prop = svc.deactivate_property(request.user, property_id)
    except (svc.PropertyPermissionError, svc.PropertyNotFoundError):
        return _not_found()

    prop.refresh_from_db()
    return 200, _serialize(prop)
