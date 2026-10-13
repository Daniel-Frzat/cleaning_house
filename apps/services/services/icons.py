"""
Icons Service — Services Domain (شاشة تفاصيل المنزل، 2026-10-13)

أيقونات يرفعها الأدمن من لوحة التحكم، ويقرؤها التطبيق من مسار عام.

🔒 النوع يُشتق من بايتات الملف (magic bytes / بنية SVG) لا من content_type
   الذي يرسله المتصفح — يتحكم فيه المُرسِل.
🔒 SVG نص قابل للتنفيذ في المتصفح: يُرفض ما فيه سكربت أو معالج حدث أو
   رابط خارجي، ويُقدَّم الملف بترويسة CSP تمنع التنفيذ (api/icons.py).
📌 حذف أيقونة مستعملة يُرفض (icon_in_use) بدل أن تختفي بصمت من الشاشة.
"""

import hashlib
import logging
import re

from django.db import transaction
from django.db.models import Count

from apps.audit.services.audit import record
from apps.audit.services.backoffice import assert_admin

from ..models import CatalogIcon

logger = logging.getLogger(__name__)

PNG = "image/png"
WEBP = "image/webp"
SVG = "image/svg+xml"
ALLOWED_TYPES = (PNG, WEBP, SVG)


class IconError(Exception):
    code = "icon_error"


class IconNotFoundError(IconError):
    code = "icon_not_found"


class EmptyIconError(IconError):
    code = "empty_icon"


class IconTooLargeError(IconError):
    code = "icon_too_large"


class UnsupportedIconFormatError(IconError):
    """ليس PNG ولا WebP ولا SVG آمنًا."""

    code = "unsupported_icon_format"


class IconInUseError(IconError):
    code = "icon_in_use"


# ------------------------------------------------------------
# فحص المحتوى
# ------------------------------------------------------------
# ما يجعل SVG قابلًا للتنفيذ أو جالبًا لمورد خارجي عند فتحه في متصفح.
_SVG_FORBIDDEN = re.compile(
    r"<\s*(script|foreignobject|iframe|embed|object)\b"
    r"|<!\s*(entity|doctype)"
    r"|\son[a-z]+\s*="
    r"|javascript\s*:",
    re.IGNORECASE,
)
# href مسموح لمرجع داخلي (#id) أو صورة مضمَّنة (data:image/...) فقط
_SVG_HREF = re.compile(r"href\s*=\s*[\"']\s*([^\"']*)", re.IGNORECASE)


def _is_safe_svg(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    stripped = text.strip()
    if not re.match(r"^(<\?xml[^>]*>\s*)?(<!--.*?-->\s*)*<svg\b", stripped, re.DOTALL | re.IGNORECASE):
        return False
    if not stripped.lower().endswith("</svg>"):
        return False
    if _SVG_FORBIDDEN.search(text):
        return False
    for target in _SVG_HREF.findall(text):
        target = target.strip().lower()
        if not (target.startswith("#") or target.startswith("data:image/")):
            return False
    return True


def sniff_icon_type(data):
    """النوع القانوني للأيقونة من محتواها، أو None."""
    head = bytes(data[:16])
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return WEBP
    if _is_safe_svg(bytes(data)):
        return SVG
    return None


def _validated(data):
    """(content_type, sha256) لملف صالح، وإلا يرفع خطأ مفهومًا."""
    if not data:
        raise EmptyIconError("The icon file is empty.")
    if len(data) > CatalogIcon.MAX_BYTES:
        raise IconTooLargeError(f"Icons are limited to {CatalogIcon.MAX_BYTES // 1024} KB.")
    content_type = sniff_icon_type(data)
    if content_type is None:
        raise UnsupportedIconFormatError(
            "Upload a PNG, WebP or SVG icon. SVG files must not contain scripts, "
            "event handlers or external links."
        )
    return content_type, hashlib.sha256(data).hexdigest()


def _without_data(queryset):
    """البايتات لا تُحمَّل في القوائم — تُقرأ عند تقديم الملف وحده."""
    return queryset.defer("data")


# ------------------------------------------------------------
# الإدارة
# ------------------------------------------------------------
def list_icons(user, q=None):
    assert_admin(user)
    qs = _without_data(CatalogIcon.objects.all()).annotate(
        used_by_fields=Count("home_detail_fields", distinct=True),
        used_by_options=Count("home_detail_options", distinct=True),
    )
    if q:
        qs = qs.filter(name__icontains=q.strip())
    return qs


def get_icon(user, icon_id):
    assert_admin(user)
    icon = list_icons(user).filter(pk=icon_id).first()
    if icon is None:
        raise IconNotFoundError("Icon not found.")
    return icon


@transaction.atomic
def create_icon(user, name, data, request=None):
    assert_admin(user)
    content_type, digest = _validated(data)
    icon = CatalogIcon(
        name=name.strip(), content_type=content_type, data=bytes(data), size=len(data), sha256=digest
    )
    icon.full_clean()
    icon.save()
    record(
        user, "icon.create", target=icon,
        details={"name": icon.name, "content_type": content_type, "size": icon.size},
        request=request,
    )
    logger.info("Icon created (icon_id=%s, type=%s, by=%s)", icon.id, content_type, user.id)
    return get_icon(user, icon.id)


@transaction.atomic
def replace_icon_file(user, icon_id, data, request=None):
    """يستبدل الملف في مكانه — كل سؤال وخيار يستعمله يتغيّر معه."""
    icon = get_icon(user, icon_id)
    content_type, digest = _validated(data)
    previous = icon.sha256
    CatalogIcon.objects.filter(pk=icon.pk).update(
        content_type=content_type, data=bytes(data), size=len(data), sha256=digest
    )
    record(
        user, "icon.replace", target=icon,
        details={"from_sha256": previous, "to_sha256": digest, "content_type": content_type},
        request=request,
    )
    return get_icon(user, icon_id)


@transaction.atomic
def rename_icon(user, icon_id, name, request=None):
    icon = get_icon(user, icon_id)
    before = icon.name
    CatalogIcon.objects.filter(pk=icon.pk).update(name=name.strip())
    record(user, "icon.rename", target=icon, details={"from": before, "to": name.strip()}, request=request)
    return get_icon(user, icon_id)


@transaction.atomic
def delete_icon(user, icon_id, request=None):
    icon = get_icon(user, icon_id)
    if icon.used_by_fields or icon.used_by_options:
        raise IconInUseError(
            f"This icon is used by {icon.used_by_fields} question(s) and "
            f"{icon.used_by_options} option(s). Remove it from them first."
        )
    record(user, "icon.delete", target=icon, details={"name": icon.name}, request=request)
    CatalogIcon.objects.filter(pk=icon.pk).delete()


def resolve_icon(icon_id):
    """للخدمات الأخرى: يعيد الأيقونة (بلا بايتات) أو None، أو يرفع إن لم توجد."""
    if icon_id is None:
        return None
    icon = _without_data(CatalogIcon.objects.filter(pk=icon_id)).first()
    if icon is None:
        raise IconNotFoundError(f"Icon {icon_id} not found.")
    return icon


# ------------------------------------------------------------
# القراءة العامة — تقديم الملف للتطبيق
# ------------------------------------------------------------
def get_icon_file(icon_id):
    """الأيقونة مع بايتاتها. بلا فحص دور: الأيقونات ليست سرًّا."""
    icon = CatalogIcon.objects.filter(pk=icon_id).first()
    if icon is None:
        raise IconNotFoundError("Icon not found.")
    return icon
