"""
Django Admin — Audit Log + أدوات مشتركة للوحة الإدارة.

📌 الأدوات المشتركة (روابط الكائنات، تنسيق المبالغ، تصدير CSV) تعيش هنا
   لأن كل admin.py آخر يستوردها، وسجل التدقيق نفسه هو ما تكتب فيه
   أفعال الإدارة. لا تعتمد على اسم موقع ثابت: الروابط تُبنى من
   `self.admin_site.name`، فتعمل على admin.site الافتراضي وعلى أي
   AdminSite مخصص يُسجَّل عليه النموذج لاحقًا.

🔒 AuditLog للقراءة فقط وللـsuperuser وحده: لا إضافة ولا تعديل ولا حذف.
   السجل الذي يمكن تحريره ليس سجلًا.
"""

import csv
import json

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.http import StreamingHttpResponse
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import AuditLog
from .services.audit import record

EMPTY = "—"


# ============================================================
# أدوات مشتركة
# ============================================================
def admin_change_url(admin_site, obj):
    """رابط صفحة الكائن في هذا الموقع، أو None إن لم يكن مسجَّلًا فيه."""
    if obj is None or obj.pk is None:
        return None
    model = type(obj)
    if not admin_site.is_registered(model):
        return None
    opts = model._meta
    try:
        return reverse(
            f"{admin_site.name}:{opts.app_label}_{opts.model_name}_change",
            args=[obj.pk],
        )
    except NoReverseMatch:
        return None


def format_money(amount, currency="AUD"):
    """مبلغ مقروء بعملته: `AUD 1,234.50`."""
    if amount is None:
        return EMPTY
    return f"{currency or 'AUD'} {amount:,.2f}"


def short_id(value):
    """أول ثمانية محارف من الـUUID — للقوائم وحدها."""
    return str(value)[:8] if value else EMPTY


def pretty_json(value):
    if value in (None, "", {}, []):
        return EMPTY
    return format_html(
        '<pre style="white-space:pre-wrap;margin:0">{}</pre>',
        json.dumps(value, indent=2, ensure_ascii=False, default=str),
    )


class BackOfficeMixin:
    """
    سلوك مشترك لكل ModelAdmin/Inline في المشروع.

    link(obj): رابط إلى صفحة الكائن إن كان مسجَّلًا، وإلا نصه فقط.
    """

    list_per_page = 50
    empty_value_display = EMPTY

    def link(self, obj, label=None):
        if obj is None:
            return EMPTY
        text = label if label is not None else str(obj)
        url = admin_change_url(self.admin_site, obj)
        if url is None:
            return text
        return format_html('<a href="{}">{}</a>', url, text)


class ReadOnlyAdminMixin:
    """نموذج شاهد: عرض فقط — لا إضافة ولا تعديل ولا حذف."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class _Echo:
    """كائن ملف وهمي: csv.writer يكتب فيه فيعيد السطر كما هو للبث."""

    def write(self, value):
        return value


def csv_export_action(columns, filename_prefix, audit_action):
    """
    يبني فعل "تصدير المحدَّد CSV" يبث الصفوف بلا تحميلها كلها في الذاكرة.

    columns: قائمة (عنوان, دالة(obj) → قيمة). الأعمدة تُختار صراحةً لكل
    نموذج — لا تصدير تلقائي لكل الحقول، حتى لا يتسرب رمز مزوّد أو ملاحظة
    خاصة لمجرد أنها حقل.
    """

    def export_csv(modeladmin, request, queryset):
        writer = csv.writer(_Echo())
        count = queryset.count()
        record(
            request.user,
            audit_action,
            details={"rows": count},
            request=request,
        )

        def rows():
            yield writer.writerow([header for header, _ in columns])
            for obj in queryset.iterator(chunk_size=500):
                yield writer.writerow(
                    ["" if (v := getter(obj)) is None else v for _, getter in columns]
                )

        stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        response = StreamingHttpResponse(rows(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="{filename_prefix}-{stamp}.csv"'
        )
        return response

    export_csv.short_description = "Export selected rows to CSV"
    export_csv.allowed_permissions = ("view",)
    return export_csv


# ============================================================
# AuditLog
# ============================================================
@admin.register(AuditLog)
class AuditLogAdmin(BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("created_at", "actor_display", "action", "target_label", "ip_address")
    list_filter = ("action", "target_type", "created_at")
    search_fields = ("actor_label", "action", "target_id", "target_type")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("actor",)
    fieldsets = (
        ("Who", {"fields": ("actor_display", "actor_label", "ip_address")}),
        ("What", {"fields": ("action", "target_display", "target_type", "target_id")}),
        ("Details", {"fields": ("details_display",)}),
        ("When", {"fields": ("created_at",)}),
    )
    readonly_fields = (
        "actor_display",
        "actor_label",
        "ip_address",
        "action",
        "target_display",
        "target_type",
        "target_id",
        "details_display",
        "created_at",
    )

    # 🔒 superuser وحده يرى السجل — حتى لو مُنح موظف صلاحية view صراحةً
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    @admin.display(description="Actor", ordering="actor_label")
    def actor_display(self, obj):
        if obj.actor is None:
            return obj.actor_label or "System"
        return self.link(obj.actor, obj.actor_label or str(obj.actor))

    @admin.display(description="Target", ordering="target_type")
    def target_label(self, obj):
        if not obj.target_type:
            return EMPTY
        return f"{obj.target_type} {short_id(obj.target_id)}"

    @admin.display(description="Target")
    def target_display(self, obj):
        """صفحة التفاصيل وحدها: يبحث عن الكائن ليربطه (استعلام واحد)."""
        label = self.target_label(obj)
        if not obj.target_type:
            return label
        for model in self.admin_site._registry:
            if model.__name__ == obj.target_type:
                try:
                    target = model._default_manager.filter(pk=obj.target_id).first()
                except (ValueError, TypeError, ValidationError):
                    target = None
                if target is not None:
                    return self.link(target, label)
                break
        return label

    @admin.display(description="Details")
    def details_display(self, obj):
        return pretty_json(obj.details)
