"""
Home Details & Icons API — Services Domain (شاشة "Tell us about your home"، 2026-10-13)

التطبيق:
    GET  /api/home-details/form[?property_id=]     الأسئلة النشطة + القيم المعبّأة مسبقًا
    GET  /api/icons/{icon_id}                      ملف الأيقونة (عام، بلا مصادقة)

لوحة التحكم (ADMIN):
    GET    /api/admin/icons                        قائمة الأيقونات + استعمالها
    POST   /api/admin/icons                        رفع (multipart: name, file)
    GET    /api/admin/icons/{id}
    PATCH  /api/admin/icons/{id}                   إعادة تسمية
    POST   /api/admin/icons/{id}/file              استبدال الملف (multipart: file)
    DELETE /api/admin/icons/{id}                   409 icon_in_use إن كانت مستعملة

    GET    /api/admin/home-detail-fields           كل الأسئلة بخياراتها
    POST   /api/admin/home-detail-fields
    PUT    /api/admin/home-detail-fields/order     ترتيب الأسئلة {ids: [...]}
    GET    /api/admin/home-detail-fields/{id}
    PATCH  /api/admin/home-detail-fields/{id}
    DELETE /api/admin/home-detail-fields/{id}
    POST   /api/admin/home-detail-fields/{id}/options
    PUT    /api/admin/home-detail-fields/{id}/options/order
    PATCH  /api/admin/home-detail-fields/{id}/options/{option_id}
    DELETE /api/admin/home-detail-fields/{id}/options/{option_id}

📌 قرار PO — 2026-10-13: الإجابات معلومات فقط — لا تدخل في السعر.
⚠️ لا ORM هنا — كل شيء عبر services/home_details.py و services/icons.py.
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseNotModified
from django.urls import reverse
from ninja import File, Form, Query, Router, Schema, UploadedFile
from pydantic import Field

from apps.accounts.authentication import ActiveUserJWTAuth, AdminJWTAuth
from apps.audit.api.common import ErrorOut, error, forbidden
from apps.audit.services.backoffice import AdminRequiredError
from apps.properties.services import properties as properties_svc

from ..models import COUNTER_MAX_LIMIT, HomeDetailInputType, HomeDetailSection
from ..services import home_details as svc
from ..services import icons as icons_svc

public_router = Router(tags=["Home details"], auth=ActiveUserJWTAuth())
icon_file_router = Router(tags=["Home details"])
admin_router = Router(tags=["Admin — Home details"], auth=AdminJWTAuth())

Key = Field(..., min_length=1, max_length=50, pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


# ============================================================
# Schemas
# ============================================================
class IconRefOut(Schema):
    id: uuid.UUID
    url: str
    content_type: str


class OptionOut(Schema):
    id: uuid.UUID
    key: str
    label: str
    icon: Optional[IconRefOut] = None


class FormFieldOut(Schema):
    id: uuid.UUID
    key: str
    label: str
    help_text: str
    input_type: HomeDetailInputType
    section: HomeDetailSection
    is_required: bool
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    icon: Optional[IconRefOut] = None
    options: list[OptionOut] = []
    value: Any = None


class FormOut(Schema):
    fields: list[FormFieldOut]


class FormQuery(Schema):
    property_id: Optional[uuid.UUID] = None


class AdminIconOut(Schema):
    id: uuid.UUID
    name: str
    url: str
    content_type: str
    size: int
    used_by_fields: int
    used_by_options: int
    created_at: datetime
    updated_at: datetime


class AdminIconListOut(Schema):
    count: int
    items: list[AdminIconOut]


class IconListQuery(Schema):
    q: Optional[str] = Field(None, max_length=120)


class IconRenameIn(Schema):
    name: str = Field(..., min_length=1, max_length=120)


class AdminOptionOut(Schema):
    id: uuid.UUID
    key: str
    label: str
    icon: Optional[IconRefOut] = None
    sort_order: int
    is_active: bool


class AdminFieldOut(Schema):
    id: uuid.UUID
    key: str
    label: str
    help_text: str
    input_type: HomeDetailInputType
    section: HomeDetailSection
    is_required: bool
    min_value: int
    max_value: int
    default_value: int
    icon: Optional[IconRefOut] = None
    sort_order: int
    is_active: bool
    options: list[AdminOptionOut]
    created_at: datetime
    updated_at: datetime


class AdminFieldListOut(Schema):
    count: int
    items: list[AdminFieldOut]


class FieldIn(Schema):
    key: str = Key
    label: str = Field(..., min_length=1, max_length=80)
    input_type: HomeDetailInputType
    help_text: str = Field("", max_length=200)
    icon_id: Optional[uuid.UUID] = None
    section: HomeDetailSection = HomeDetailSection.MAIN
    is_required: bool = False
    min_value: int = Field(0, ge=0, le=COUNTER_MAX_LIMIT)
    max_value: int = Field(20, ge=0, le=COUNTER_MAX_LIMIT)
    default_value: int = Field(0, ge=0, le=COUNTER_MAX_LIMIT)
    sort_order: Optional[int] = Field(None, ge=0, description="Omitted: placed last.")
    is_active: bool = True


class FieldPatch(Schema):
    label: Optional[str] = Field(None, min_length=1, max_length=80)
    help_text: Optional[str] = Field(None, max_length=200)
    icon_id: Optional[uuid.UUID] = Field(None, description="`null` removes the icon.")
    section: Optional[HomeDetailSection] = None
    is_required: Optional[bool] = None
    min_value: Optional[int] = Field(None, ge=0, le=COUNTER_MAX_LIMIT)
    max_value: Optional[int] = Field(None, ge=0, le=COUNTER_MAX_LIMIT)
    default_value: Optional[int] = Field(None, ge=0, le=COUNTER_MAX_LIMIT)
    sort_order: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None


class OptionIn(Schema):
    key: str = Key
    label: str = Field(..., min_length=1, max_length=80)
    icon_id: Optional[uuid.UUID] = None
    sort_order: Optional[int] = Field(None, ge=0, description="Omitted: placed last.")
    is_active: bool = True


class OptionPatch(Schema):
    label: Optional[str] = Field(None, min_length=1, max_length=80)
    icon_id: Optional[uuid.UUID] = Field(None, description="`null` removes the icon.")
    sort_order: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None


class OrderIn(Schema):
    ids: list[uuid.UUID] = Field(..., description="Every id, exactly once, in display order.")


# ============================================================
# Serialization
# ============================================================
def icon_url(request, icon):
    """رابط مطلق يحمل بصمة المحتوى — يُخزَّن مؤقتًا للأبد ويتجدد عند الاستبدال."""
    path = reverse(
        f"{request.resolver_match.namespace}:catalog_icon_file", kwargs={"icon_id": icon.id}
    )
    return request.build_absolute_uri(f"{path}?v={icon.sha256[:12]}")


def _icon_ref(request, icon):
    if icon is None:
        return None
    return {"id": icon.id, "url": icon_url(request, icon), "content_type": icon.content_type}


def _serialize_icon(request, icon):
    return {
        "id": icon.id,
        "name": icon.name,
        "url": icon_url(request, icon),
        "content_type": icon.content_type,
        "size": icon.size,
        "used_by_fields": icon.used_by_fields,
        "used_by_options": icon.used_by_options,
        "created_at": icon.created_at,
        "updated_at": icon.updated_at,
    }


def _serialize_field(request, field):
    return {
        "id": field.id,
        "key": field.key,
        "label": field.label,
        "help_text": field.help_text,
        "input_type": field.input_type,
        "section": field.section,
        "is_required": field.is_required,
        "min_value": field.min_value,
        "max_value": field.max_value,
        "default_value": field.default_value,
        "icon": _icon_ref(request, field.icon),
        "sort_order": field.sort_order,
        "is_active": field.is_active,
        "options": [
            {
                "id": option.id,
                "key": option.key,
                "label": option.label,
                "icon": _icon_ref(request, option.icon),
                "sort_order": option.sort_order,
                "is_active": option.is_active,
            }
            for option in field.options.all()
        ],
        "created_at": field.created_at,
        "updated_at": field.updated_at,
    }


def _serialize_form_field(request, field, value):
    is_counter = field.input_type == HomeDetailInputType.COUNTER
    return {
        "id": field.id,
        "key": field.key,
        "label": field.label,
        "help_text": field.help_text,
        "input_type": field.input_type,
        "section": field.section,
        "is_required": field.is_required,
        "min_value": field.min_value if is_counter else None,
        "max_value": field.max_value if is_counter else None,
        "icon": _icon_ref(request, field.icon),
        "options": [
            {
                "id": option.id,
                "key": option.key,
                "label": option.label,
                "icon": _icon_ref(request, option.icon),
            }
            for option in field.active_options
        ],
        "value": value,
    }


def _validation_error(exc):
    if hasattr(exc, "message_dict"):
        detail = "; ".join(f"{k}: {' '.join(v)}" for k, v in exc.message_dict.items())
    else:
        detail = "; ".join(exc.messages)
    return error(422, "validation_error", detail)


def _field_errors(exc):
    """أخطاء الأسئلة والخيارات → ردود HTTP."""
    if isinstance(exc, AdminRequiredError):
        return forbidden(exc)
    if isinstance(exc, (svc.HomeDetailFieldNotFoundError, svc.HomeDetailOptionNotFoundError)):
        return error(404, exc.code, str(exc))
    if isinstance(exc, svc.HomeDetailKeyTakenError):
        return error(409, exc.code, str(exc))
    if isinstance(exc, ValidationError):
        return _validation_error(exc)
    # أيقونة غير موجودة في icon_id، سؤال ليس اختيارًا، ترتيب ناقص، حقل ثابت
    if isinstance(exc, (svc.NotAChoiceFieldError, svc.InvalidOrderError, svc.HomeDetailError, icons_svc.IconError)):
        return error(422, exc.code, str(exc))
    raise exc


FIELD_ERRORS = (AdminRequiredError, svc.HomeDetailError, icons_svc.IconError, ValidationError)
ICON_ERRORS = (AdminRequiredError, icons_svc.IconError, ValidationError)


def _icon_errors(exc):
    if isinstance(exc, AdminRequiredError):
        return forbidden(exc)
    if isinstance(exc, icons_svc.IconNotFoundError):
        return error(404, exc.code, str(exc))
    if isinstance(exc, icons_svc.IconInUseError):
        return error(409, exc.code, str(exc))
    if isinstance(exc, icons_svc.IconTooLargeError):
        return error(413, exc.code, str(exc))
    if isinstance(exc, ValidationError):
        return _validation_error(exc)
    if isinstance(exc, (icons_svc.EmptyIconError, icons_svc.UnsupportedIconFormatError, icons_svc.IconError)):
        return error(422, exc.code, str(exc))
    raise exc


# ============================================================
# التطبيق
# ============================================================
@public_router.get(
    "/form",
    response={200: FormOut, 403: ErrorOut, 404: ErrorOut},
    summary="Questions for the \"Tell us about your home\" screen",
    description=(
        "**Who may call:** any signed-in user.\n\n"
        "Returns the active questions in display order, each with its active "
        "options and icons, as configured from the dashboard. Build the screen "
        "from this response — nothing about the questions is hard-coded in the "
        "app.\n\n"
        "* `input_type`: `COUNTER` (− n +, between `min_value` and `max_value`), "
        "`YES_NO`, `SINGLE_CHOICE`, `MULTI_CHOICE`.\n"
        "* `section`: `MAIN` is always shown; `EXTRA` sits behind "
        "\"+ add other conditions (optional)\".\n"
        "* `value`: the starting value. With `property_id`, it is the answer "
        "last saved for that property (if still valid), otherwise the default.\n"
        "* `icon.url` changes whenever the icon is replaced, so it can be cached "
        "indefinitely by URL.\n\n"
        "Send the answers as `home_details: {key: value}` in `POST /api/bookings`. "
        "They are **information only and never change the price**.\n\n"
        "**Side effects:** none — read-only."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "`property_id` belongs to someone else."},
            404: {"description": "No property with this id."},
        }
    },
)
def get_form(request, query: FormQuery = Query(...)):
    try:
        form = svc.build_form(request.user, query.property_id)
    except properties_svc.PropertyPermissionError as exc:
        return error(403, "property_forbidden", str(exc))
    except properties_svc.PropertyNotFoundError:
        return error(404, "property_not_found", "Property not found.")
    return 200, {"fields": [_serialize_form_field(request, f, v) for f, v in form]}


@icon_file_router.get(
    "/{icon_id}",
    url_name="catalog_icon_file",
    summary="Icon file (public)",
    description=(
        "**Who may call:** anyone — no token, so image widgets can load it "
        "directly. Use the `url` returned by the API (it carries `?v=` with "
        "the content fingerprint); the response is cacheable for a year.\n\n"
        "Returns `404` for an unknown icon."
    ),
    response={200: None, 404: ErrorOut},
)
def get_icon_file(request, icon_id: uuid.UUID):
    try:
        icon = icons_svc.get_icon_file(icon_id)
    except icons_svc.IconNotFoundError as exc:
        return error(404, exc.code, str(exc))

    etag = f'"{icon.sha256}"'
    if request.headers.get("If-None-Match") == etag:
        return HttpResponseNotModified()

    response = HttpResponse(bytes(icon.data), content_type=icon.content_type)
    response["ETag"] = etag
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    response["X-Content-Type-Options"] = "nosniff"
    # 🔒 SVG يُفتح في المتصفح كمستند: لا سكربت ولا موارد خارجية أبدًا
    response["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    return response


# ============================================================
# لوحة التحكم — الأيقونات
# ============================================================
@admin_router.get(
    "/icons",
    response={200: AdminIconListOut, 403: ErrorOut},
    summary="List icons with their usage (admin only)",
)
def list_icons(request, query: IconListQuery = Query(...)):
    try:
        icons = list(icons_svc.list_icons(request.user, q=query.q))
    except AdminRequiredError as exc:
        return forbidden(exc)
    return 200, {"count": len(icons), "items": [_serialize_icon(request, i) for i in icons]}


@admin_router.post(
    "/icons",
    response={201: AdminIconOut, 403: ErrorOut, 413: ErrorOut, 422: ErrorOut},
    summary="Upload an icon (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "`multipart/form-data` with `name` and `file`. PNG, WebP or SVG, up to "
        "200 KB. The type is read from the file itself; SVG files with scripts, "
        "event handlers or external links are rejected "
        "(`422 unsupported_icon_format`). Recorded in the audit log."
    ),
)
def upload_icon(request, name: str = Form(..., min_length=1, max_length=120), file: UploadedFile = File(...)):
    try:
        icon = icons_svc.create_icon(request.user, name, file.read(), request=request)
    except ICON_ERRORS as exc:
        return _icon_errors(exc)
    return 201, _serialize_icon(request, icon)


@admin_router.get(
    "/icons/{icon_id}",
    response={200: AdminIconOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve an icon (admin only)",
)
def retrieve_icon(request, icon_id: uuid.UUID):
    try:
        icon = icons_svc.get_icon(request.user, icon_id)
    except ICON_ERRORS as exc:
        return _icon_errors(exc)
    return 200, _serialize_icon(request, icon)


@admin_router.patch(
    "/icons/{icon_id}",
    response={200: AdminIconOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut},
    summary="Rename an icon (admin only)",
)
def rename_icon(request, icon_id: uuid.UUID, payload: IconRenameIn):
    try:
        icon = icons_svc.rename_icon(request.user, icon_id, payload.name, request=request)
    except ICON_ERRORS as exc:
        return _icon_errors(exc)
    return 200, _serialize_icon(request, icon)


@admin_router.post(
    "/icons/{icon_id}/file",
    response={200: AdminIconOut, 403: ErrorOut, 404: ErrorOut, 413: ErrorOut, 422: ErrorOut},
    summary="Replace an icon's file (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "`multipart/form-data` with `file`. Every question and option using "
        "this icon shows the new file; its `url` changes so apps fetch it again."
    ),
)
def replace_icon_file(request, icon_id: uuid.UUID, file: UploadedFile = File(...)):
    try:
        icon = icons_svc.replace_icon_file(request.user, icon_id, file.read(), request=request)
    except ICON_ERRORS as exc:
        return _icon_errors(exc)
    return 200, _serialize_icon(request, icon)


@admin_router.delete(
    "/icons/{icon_id}",
    response={204: None, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    summary="Delete an unused icon (admin only)",
    description="`409 icon_in_use` while a question or option still uses it.",
)
def delete_icon(request, icon_id: uuid.UUID):
    try:
        icons_svc.delete_icon(request.user, icon_id, request=request)
    except ICON_ERRORS as exc:
        return _icon_errors(exc)
    return 204, None


# ============================================================
# لوحة التحكم — الأسئلة والخيارات
# ============================================================
@admin_router.get(
    "/home-detail-fields",
    response={200: AdminFieldListOut, 403: ErrorOut},
    summary="List home-detail questions, active and inactive (admin only)",
)
def list_fields(request):
    try:
        fields = list(svc.list_fields(request.user))
    except AdminRequiredError as exc:
        return forbidden(exc)
    return 200, {"count": len(fields), "items": [_serialize_field(request, f) for f in fields]}


@admin_router.post(
    "/home-detail-fields",
    response={201: AdminFieldOut, 403: ErrorOut, 409: ErrorOut, 422: ErrorOut},
    summary="Add a home-detail question (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "`key` is a stable slug (`bedrooms`, `has-pets`) used in "
        "`home_details` answers; it and `input_type` cannot change later. "
        "`min_value` / `max_value` / `default_value` apply to `COUNTER`; for "
        "`YES_NO`, `default_value` is 0 (No) or 1 (Yes). Choice questions get "
        "their options through `/options`. The app shows it immediately."
    ),
)
def create_field(request, payload: FieldIn):
    data = payload.dict()
    try:
        field = svc.create_field(
            request.user, data.pop("key"), data.pop("label"), data.pop("input_type"),
            request=request, **data,
        )
    except FIELD_ERRORS as exc:
        return _field_errors(exc)
    return 201, _serialize_field(request, field)


@admin_router.put(
    "/home-detail-fields/order",
    response={200: AdminFieldListOut, 403: ErrorOut, 422: ErrorOut},
    summary="Reorder home-detail questions (admin only)",
    description="Send every question id exactly once, in display order (`422 invalid_order` otherwise).",
)
def reorder_fields(request, payload: OrderIn):
    try:
        fields = list(svc.reorder_fields(request.user, payload.ids, request=request))
    except FIELD_ERRORS as exc:
        return _field_errors(exc)
    return 200, {"count": len(fields), "items": [_serialize_field(request, f) for f in fields]}


@admin_router.get(
    "/home-detail-fields/{field_id}",
    response={200: AdminFieldOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve a home-detail question (admin only)",
)
def retrieve_field(request, field_id: uuid.UUID):
    try:
        field = svc.get_field(request.user, field_id)
    except FIELD_ERRORS as exc:
        return _field_errors(exc)
    return 200, _serialize_field(request, field)


@admin_router.patch(
    "/home-detail-fields/{field_id}",
    response={200: AdminFieldOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut},
    summary="Edit a home-detail question (admin only)",
    description=(
        "Partial update. `key` and `input_type` are fixed. `is_active: false` "
        "hides the question from the app without deleting it. Bookings already "
        "placed keep the labels they were made with."
    ),
)
def update_field(request, field_id: uuid.UUID, payload: FieldPatch):
    try:
        field = svc.update_field(
            request.user, field_id, request=request, **payload.dict(exclude_unset=True)
        )
    except FIELD_ERRORS as exc:
        return _field_errors(exc)
    return 200, _serialize_field(request, field)


@admin_router.delete(
    "/home-detail-fields/{field_id}",
    response={204: None, 403: ErrorOut, 404: ErrorOut},
    summary="Delete a home-detail question and its options (admin only)",
    description=(
        "Permanent. Bookings already placed keep their frozen answers; answers "
        "saved on properties for this question are ignored from now on."
    ),
)
def delete_field(request, field_id: uuid.UUID):
    try:
        svc.delete_field(request.user, field_id, request=request)
    except FIELD_ERRORS as exc:
        return _field_errors(exc)
    return 204, None


@admin_router.post(
    "/home-detail-fields/{field_id}/options",
    response={201: AdminFieldOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut},
    summary="Add an option to a choice question (admin only)",
    description=(
        "Only for `SINGLE_CHOICE` / `MULTI_CHOICE` questions "
        "(`422 home_detail_not_choice_field` otherwise). `key` is unique within "
        "the question and fixed. Returns the whole question."
    ),
)
def create_option(request, field_id: uuid.UUID, payload: OptionIn):
    data = payload.dict()
    try:
        field = svc.create_option(
            request.user, field_id, data.pop("key"), data.pop("label"), request=request, **data
        )
    except FIELD_ERRORS as exc:
        return _field_errors(exc)
    return 201, _serialize_field(request, field)


@admin_router.put(
    "/home-detail-fields/{field_id}/options/order",
    response={200: AdminFieldOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut},
    summary="Reorder a question's options (admin only)",
)
def reorder_options(request, field_id: uuid.UUID, payload: OrderIn):
    try:
        field = svc.reorder_options(request.user, field_id, payload.ids, request=request)
    except FIELD_ERRORS as exc:
        return _field_errors(exc)
    return 200, _serialize_field(request, field)


@admin_router.patch(
    "/home-detail-fields/{field_id}/options/{option_id}",
    response={200: AdminFieldOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut},
    summary="Edit an option (admin only)",
)
def update_option(request, field_id: uuid.UUID, option_id: uuid.UUID, payload: OptionPatch):
    try:
        field = svc.update_option(
            request.user, field_id, option_id, request=request, **payload.dict(exclude_unset=True)
        )
    except FIELD_ERRORS as exc:
        return _field_errors(exc)
    return 200, _serialize_field(request, field)


@admin_router.delete(
    "/home-detail-fields/{field_id}/options/{option_id}",
    response={200: AdminFieldOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut},
    summary="Delete an option (admin only)",
    description="Permanent. Returns the question without it. Bookings keep their frozen answers.",
)
def delete_option(request, field_id: uuid.UUID, option_id: uuid.UUID):
    try:
        field = svc.delete_option(request.user, field_id, option_id, request=request)
    except FIELD_ERRORS as exc:
        return _field_errors(exc)
    return 200, _serialize_field(request, field)
