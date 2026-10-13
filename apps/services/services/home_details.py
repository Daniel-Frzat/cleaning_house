"""
Home Details Service — Services Domain (شاشة "Tell us about your home"، 2026-10-13)

الأسئلة وخياراتها يديرها الأدمن من لوحة التحكم؛ التطبيق يبني الشاشة مما
يعيده GET /api/home-details/form، ويرسل الإجابات مع الحجز.

📌 قرار PO — 2026-10-13: الإجابات معلومات فقط — لا تدخل في السعر.
   تُجمَّد على الحجز بتسمياتها (freeze_answers)، وآخرها يُحفظ على العقار
   لتعبئة الشاشة مسبقًا.
🔒 key والنوع (input_type) ثابتان بعد الإنشاء: الإجابات المحفوظة على
   العقارات مفهرسة بالمفتاح ومشكَّلة حسب النوع.
"""

import logging

from django.db import transaction
from django.db.models import Max, Prefetch

from apps.audit.services.audit import record
from apps.audit.services.backoffice import assert_admin

from ..models import (
    CHOICE_INPUT_TYPES,
    HomeDetailField,
    HomeDetailInputType,
    HomeDetailOption,
)
from .icons import resolve_icon

logger = logging.getLogger(__name__)


class HomeDetailError(Exception):
    code = "home_detail_error"


class HomeDetailFieldNotFoundError(HomeDetailError):
    code = "home_detail_field_not_found"


class HomeDetailOptionNotFoundError(HomeDetailError):
    code = "home_detail_option_not_found"


class HomeDetailKeyTakenError(HomeDetailError):
    code = "home_detail_key_taken"


class NotAChoiceFieldError(HomeDetailError):
    """الخيارات لأسئلة الاختيار وحدها."""

    code = "home_detail_not_choice_field"


class InvalidOrderError(HomeDetailError):
    code = "invalid_order"


class InvalidHomeDetailsError(HomeDetailError):
    """إجابات العميل لا تطابق الأسئلة الحالية."""

    code = "invalid_home_details"


FIELD_EDITABLE = (
    "label",
    "help_text",
    "icon_id",
    "section",
    "is_required",
    "min_value",
    "max_value",
    "default_value",
    "sort_order",
    "is_active",
)
OPTION_EDITABLE = ("label", "icon_id", "sort_order", "is_active")


def _audit_changes(obj, fields):
    changes = {}
    for key, value in fields.items():
        before = getattr(obj, key)
        if before != value:
            changes[key] = {"from": _plain(before), "to": _plain(value)}
    return changes


def _plain(value):
    return value if isinstance(value, (bool, int, str)) or value is None else str(value)


def _next_order(queryset):
    top = queryset.aggregate(top=Max("sort_order"))["top"]
    return 0 if top is None else top + 1


def _apply(obj, fields):
    for key, value in fields.items():
        if key == "icon_id":
            obj.icon = resolve_icon(value)
        else:
            setattr(obj, key, value)


# ============================================================
# الإدارة — الأسئلة
# ============================================================
def _fields_queryset():
    options = HomeDetailOption.objects.select_related("icon").defer("icon__data")
    return (
        HomeDetailField.objects.select_related("icon")
        .defer("icon__data")
        .prefetch_related(Prefetch("options", queryset=options))
    )


def list_fields(user):
    """كل الأسئلة — النشطة والمعطّلة — بترتيب العرض."""
    assert_admin(user)
    return _fields_queryset()


def get_field(user, field_id):
    assert_admin(user)
    field = _fields_queryset().filter(pk=field_id).first()
    if field is None:
        raise HomeDetailFieldNotFoundError("Question not found.")
    return field


@transaction.atomic
def create_field(user, key, label, input_type, request=None, sort_order=None, **fields):
    assert_admin(user)
    if HomeDetailField.objects.filter(key=key).exists():
        raise HomeDetailKeyTakenError(f"A question with key '{key}' already exists.")

    unknown = set(fields) - set(FIELD_EDITABLE)
    if unknown:
        raise HomeDetailError(f"Unknown field(s): {', '.join(sorted(unknown))}.")

    field = HomeDetailField(key=key, label=label, input_type=input_type)
    _apply(field, {k: v for k, v in fields.items() if v is not None})
    field.sort_order = _next_order(HomeDetailField.objects) if sort_order is None else sort_order
    field.full_clean()
    field.save()

    record(
        user, "home_detail_field.create", target=field,
        details={"key": key, "label": label, "input_type": input_type},
        request=request,
    )
    logger.info("Home detail field created (key=%s, by=%s)", key, user.id)
    return get_field(user, field.id)


@transaction.atomic
def update_field(user, field_id, request=None, **fields):
    field = get_field(user, field_id)
    unknown = set(fields) - set(FIELD_EDITABLE)
    if unknown:
        raise HomeDetailError(
            f"Cannot change: {', '.join(sorted(unknown))}. The key and input type are fixed."
        )

    changes = _audit_changes(field, fields)
    _apply(field, fields)
    field.full_clean()
    field.save()

    record(user, "home_detail_field.update", target=field, details={"changes": changes}, request=request)
    return get_field(user, field.id)


@transaction.atomic
def delete_field(user, field_id, request=None):
    """
    حذف فعلي مع خياراته. آمن: لقطات الحجوزات مستقلة عن هذا الصف، والإجابة
    المحفوظة على العقارات تُهمَل عند التعبئة المسبقة.
    """
    field = get_field(user, field_id)
    record(
        user, "home_detail_field.delete", target=field,
        details={"key": field.key, "label": field.label},
        request=request,
    )
    HomeDetailField.objects.filter(pk=field.pk).delete()


def _reorder(queryset, ids, what):
    rows = {str(row.pk): row for row in queryset}
    wanted = [str(i) for i in ids]
    if len(set(wanted)) != len(wanted) or set(wanted) != set(rows):
        raise InvalidOrderError(f"Send every {what} id exactly once.")
    for position, row_id in enumerate(wanted):
        row = rows[row_id]
        if row.sort_order != position:
            row.sort_order = position
            row.save(update_fields=["sort_order", "updated_at"])


@transaction.atomic
def reorder_fields(user, ids, request=None):
    assert_admin(user)
    _reorder(HomeDetailField.objects.select_for_update(), ids, "question")
    record(user, "home_detail_field.reorder", details={"order": [str(i) for i in ids]}, request=request)
    return list_fields(user)


# ============================================================
# الإدارة — الخيارات
# ============================================================
def _choice_field(user, field_id):
    field = get_field(user, field_id)
    if field.input_type not in CHOICE_INPUT_TYPES:
        raise NotAChoiceFieldError("Options can only be added to single- or multiple-choice questions.")
    return field


def _get_option(field, option_id):
    option = HomeDetailOption.objects.filter(pk=option_id, field=field).first()
    if option is None:
        raise HomeDetailOptionNotFoundError("Option not found.")
    return option


@transaction.atomic
def create_option(user, field_id, key, label, request=None, sort_order=None, **fields):
    field = _choice_field(user, field_id)
    if field.options.filter(key=key).exists():
        raise HomeDetailKeyTakenError(f"This question already has an option with key '{key}'.")

    option = HomeDetailOption(field=field, key=key, label=label)
    _apply(option, {k: v for k, v in fields.items() if v is not None})
    option.sort_order = _next_order(field.options) if sort_order is None else sort_order
    option.full_clean()
    option.save()

    record(
        user, "home_detail_option.create", target=option,
        details={"field": field.key, "key": key, "label": label},
        request=request,
    )
    return get_field(user, field.id)


@transaction.atomic
def update_option(user, field_id, option_id, request=None, **fields):
    field = _choice_field(user, field_id)
    option = _get_option(field, option_id)
    unknown = set(fields) - set(OPTION_EDITABLE)
    if unknown:
        raise HomeDetailError(f"Cannot change: {', '.join(sorted(unknown))}. The key is fixed.")

    changes = _audit_changes(option, fields)
    _apply(option, fields)
    option.full_clean()
    option.save()

    record(user, "home_detail_option.update", target=option, details={"changes": changes}, request=request)
    return get_field(user, field.id)


@transaction.atomic
def delete_option(user, field_id, option_id, request=None):
    field = _choice_field(user, field_id)
    option = _get_option(field, option_id)
    record(
        user, "home_detail_option.delete", target=option,
        details={"field": field.key, "key": option.key, "label": option.label},
        request=request,
    )
    HomeDetailOption.objects.filter(pk=option.pk).delete()
    return get_field(user, field.id)


@transaction.atomic
def reorder_options(user, field_id, ids, request=None):
    field = _choice_field(user, field_id)
    _reorder(field.options.select_for_update(), ids, "option")
    record(
        user, "home_detail_option.reorder", target=field,
        details={"order": [str(i) for i in ids]},
        request=request,
    )
    return get_field(user, field.id)


# ============================================================
# العميل — الشاشة والإجابات
# ============================================================
def active_fields():
    """الأسئلة النشطة بخياراتها النشطة (في active_options) — بترتيب العرض."""
    options = (
        HomeDetailOption.objects.filter(is_active=True).select_related("icon").defer("icon__data")
    )
    return list(
        HomeDetailField.objects.filter(is_active=True)
        .select_related("icon")
        .defer("icon__data")
        .prefetch_related(Prefetch("options", queryset=options, to_attr="active_options"))
    )


def default_value(field):
    if field.input_type == HomeDetailInputType.COUNTER:
        return field.default_value
    if field.input_type == HomeDetailInputType.YES_NO:
        return bool(field.default_value)
    if field.input_type == HomeDetailInputType.MULTI_CHOICE:
        return []
    return None


def _check_value(field, value):
    """القيمة بشكلها القانوني، وإلا InvalidHomeDetailsError برسالة يفهمها العميل."""
    kind = field.input_type
    if kind == HomeDetailInputType.COUNTER:
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidHomeDetailsError(f"'{field.label}' must be a whole number.")
        if not field.min_value <= value <= field.max_value:
            raise InvalidHomeDetailsError(
                f"'{field.label}' must be between {field.min_value} and {field.max_value}."
            )
        return value

    if kind == HomeDetailInputType.YES_NO:
        if not isinstance(value, bool):
            raise InvalidHomeDetailsError(f"'{field.label}' must be true or false.")
        return value

    keys = [option.key for option in field.active_options]
    if kind == HomeDetailInputType.SINGLE_CHOICE:
        if value is None:
            return None
        if not isinstance(value, str) or value not in keys:
            raise InvalidHomeDetailsError(f"'{field.label}' has no option '{value}'.")
        return value

    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InvalidHomeDetailsError(f"'{field.label}' must be a list of option keys.")
    unknown = [v for v in value if v not in keys]
    if unknown:
        raise InvalidHomeDetailsError(f"'{field.label}' has no option '{unknown[0]}'.")
    # ترتيب الخيارات كما في الشاشة، بلا تكرار
    return [key for key in keys if key in value]


def _is_empty(field, value):
    return value is None or (field.input_type == HomeDetailInputType.MULTI_CHOICE and not value)


def _value_labels(field, value):
    kind = field.input_type
    if kind == HomeDetailInputType.COUNTER:
        return [str(value)]
    if kind == HomeDetailInputType.YES_NO:
        return ["Yes" if value else "No"]
    labels = {option.key: option.label for option in field.active_options}
    chosen = [] if value is None else ([value] if isinstance(value, str) else value)
    return [labels[key] for key in chosen]


def freeze_answers(answers):
    """
    يتحقق من إجابات العميل ويعيد (لقطة الحجز، {key: value} للعقار).

    اللقطة قائمة بترتيب الشاشة، كل عنصر:
        {key, label, input_type, section, value, value_labels}
    التسميات مجمَّدة: إعادة تسمية سؤال أو خيار لاحقًا لا تغيّر ما يقرؤه
    المقاول على هذا الحجز.
    """
    if not isinstance(answers, dict):
        raise InvalidHomeDetailsError("home_details must be an object of {key: value}.")

    fields = active_fields()
    known = {field.key for field in fields}
    unknown = sorted(set(answers) - known)
    if unknown:
        raise InvalidHomeDetailsError(f"Unknown question(s): {', '.join(unknown)}.")

    snapshot, normalized = [], {}
    for field in fields:
        value = _check_value(field, answers[field.key]) if field.key in answers else None
        if _is_empty(field, value):
            if field.is_required:
                raise InvalidHomeDetailsError(f"'{field.label}' is required.")
            if value is None:
                continue
        normalized[field.key] = value
        snapshot.append(
            {
                "key": field.key,
                "label": field.label,
                "input_type": field.input_type,
                "section": field.section,
                "value": value,
                "value_labels": _value_labels(field, value),
            }
        )
    return snapshot, normalized


def build_form(user, property_id=None):
    """
    [(field, value)] للشاشة. value = آخر إجابة محفوظة على العقار إن بقيت
    صالحة للأسئلة الحالية، وإلا القيمة الافتراضية.

    🔒 العقار عبر get_property: يفرض الملكية (404 لغير المالك).
    """
    saved = {}
    if property_id is not None:
        from apps.properties.services import properties as properties_svc

        saved = properties_svc.get_property(user, property_id).home_details or {}

    form = []
    for field in active_fields():
        value = default_value(field)
        if field.key in saved:
            try:
                value = _check_value(field, saved[field.key])
            except InvalidHomeDetailsError:
                pass  # سؤال تغيّرت حدوده أو حُذف خياره — يعود للافتراضي
        form.append((field, value))
    return form
