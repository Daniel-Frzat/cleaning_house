"""
Django Admin — Services Catalog & Pricing Domain.

📌 إنشاء الخدمات وتعديلها يمر عبر طبقة الخدمة (services/catalog.py) لا عبر
   حفظ النموذج مباشرة: الخدمة تفرض دور ADMIN والحقول القابلة للتعديل.
   كل تعديل يُسجَّل في سجل التدقيق.

🔒 لا حذف لنوع خدمة: الحجوزات تشير إليه (PROTECT) — الإيقاف تعطيل ناعم.
"""

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count

from apps.audit.admin import BackOfficeMixin, format_money
from apps.audit.services.audit import record

from .models import PricingConfig, ServiceType
from .services import catalog as catalog_service


@admin.register(ServiceType)
class ServiceTypeAdmin(BackOfficeMixin, admin.ModelAdmin):
    list_display = ("name", "room_price_display", "base_price_display", "is_active", "times_booked", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "description")
    ordering = ("name",)
    readonly_fields = ("id", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("id", "name", "description", "is_active")}),
        (
            "Pricing",
            {
                "fields": ("room_price", "base_price"),
                "description": "Changes apply to new quotes only; frozen booking prices never change.",
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    actions = ["activate_selected", "deactivate_selected"]

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_times_booked=Count("booking_selections"))

    @admin.display(description="Room price", ordering="room_price")
    def room_price_display(self, obj):
        return format_money(obj.room_price)

    @admin.display(description="Base price", ordering="base_price")
    def base_price_display(self, obj):
        return format_money(obj.base_price)

    @admin.display(description="Times booked", ordering="_times_booked")
    def times_booked(self, obj):
        return getattr(obj, "_times_booked", None)

    def save_model(self, request, obj, form, change):
        """الحفظ عبر طبقة الخدمة — لا obj.save() مباشرة."""
        try:
            with transaction.atomic():
                if change:
                    fields = {f: form.cleaned_data[f] for f in form.changed_data}
                    if not fields:
                        return
                    saved = catalog_service.update_service_type(request.user, obj.pk, **fields)
                    record(
                        request.user,
                        "service_type.update",
                        target=saved,
                        details={"changed": {k: str(v) for k, v in fields.items()}},
                        request=request,
                    )
                else:
                    saved = catalog_service.create_service_type(
                        request.user,
                        name=obj.name,
                        room_price=obj.room_price,
                        base_price=obj.base_price,
                        description=obj.description,
                        is_active=obj.is_active,
                    )
                    record(request.user, "service_type.create", target=saved, request=request)
        except catalog_service.CatalogPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        # المسار الإداري يحتاج الكائن المحفوظ (للرابط والسجل)
        obj.pk = saved.pk
        obj.created_at = saved.created_at
        obj.updated_at = saved.updated_at
        obj._state.adding = False
        obj._state.db = saved._state.db

    def _set_active(self, request, queryset, active):
        done = 0
        for service in queryset:
            if service.is_active == active:
                continue
            try:
                with transaction.atomic():
                    if active:
                        catalog_service.update_service_type(request.user, service.pk, is_active=True)
                    else:
                        catalog_service.deactivate_service_type(request.user, service.pk)
                    record(
                        request.user,
                        "service_type.activate" if active else "service_type.deactivate",
                        target=service,
                        request=request,
                    )
                done += 1
            except (catalog_service.CatalogError, ValidationError) as exc:
                self.message_user(request, f"{service}: {exc}", level=messages.ERROR)
        self.message_user(
            request,
            f"{'Activated' if active else 'Deactivated'} {done} service(s).",
            level=messages.SUCCESS,
        )

    @admin.action(description="Activate selected services", permissions=["change"])
    def activate_selected(self, request, queryset):
        self._set_active(request, queryset, True)

    @admin.action(description="Deactivate selected services", permissions=["change"])
    def deactivate_selected(self, request, queryset):
        self._set_active(request, queryset, False)


@admin.register(PricingConfig)
class PricingConfigAdmin(BackOfficeMixin, admin.ModelAdmin):
    """
    singleton — لا إضافة (ما دام الصف موجودًا) ولا حذف، تعديل فقط.

    📌 رقم النسخة يُرقَّى تلقائيًا في PricingConfig.save عند أي تغيير
       تسعيري؛ لذلك pricing_version وactive_from للقراءة. والعملة كذلك:
       منتج بعملة واحدة، وتغييرها يفصل كل المبالغ المجمَّدة عن معناها.
    """

    list_display = (
        "__str__",
        "price_per_km_display",
        "included_distance_km",
        "maximum_travel_fee_display",
        "rounding_rule",
        "dispatch_offer_ttl_seconds",
        "active_from",
        "updated_at",
    )
    readonly_fields = ("id", "currency", "pricing_version", "active_from", "created_at", "updated_at")
    fieldsets = (
        ("Version", {"fields": ("id", "pricing_version", "active_from", "currency")}),
        (
            "Travel pricing",
            {"fields": ("price_per_km", "included_distance_km", "maximum_travel_fee", "rounding_rule")},
        ),
        ("Dispatch", {"fields": ("dispatch_offer_ttl_seconds",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def has_add_permission(self, request):
        # الصف ينشأ تلقائيًا عند أول قراءة عبر طبقة الخدمة
        return not PricingConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Price per km", ordering="price_per_km")
    def price_per_km_display(self, obj):
        return format_money(obj.price_per_km, obj.currency)

    @admin.display(description="Maximum travel fee", ordering="maximum_travel_fee")
    def maximum_travel_fee_display(self, obj):
        return format_money(obj.maximum_travel_fee, obj.currency)

    def save_model(self, request, obj, form, change):
        before = {}
        if change and form.changed_data:
            previous = PricingConfig.objects.filter(pk=obj.pk).values(*form.changed_data).first() or {}
            before = {k: str(v) for k, v in previous.items()}
        with transaction.atomic():
            super().save_model(request, obj, form, change)
            if form.changed_data or not change:
                record(
                    request.user,
                    "pricing_config.update" if change else "pricing_config.create",
                    target=obj,
                    details={
                        "pricing_version": obj.pricing_version,
                        "changed": {
                            f: {"from": before.get(f), "to": str(getattr(obj, f))}
                            for f in form.changed_data
                        },
                    },
                    request=request,
                )
