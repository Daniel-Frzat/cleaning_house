"""Django Admin — Services Catalog & Pricing Domain."""

from django.contrib import admin

from .models import PricingConfig, ServiceType


@admin.register(ServiceType)
class ServiceTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "room_price", "base_price", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "description")
    ordering = ("name",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(PricingConfig)
class PricingConfigAdmin(admin.ModelAdmin):
    """singleton — لا إضافة ولا حذف من الواجهة، تعديل فقط."""

    list_display = ("__str__", "updated_at")
    readonly_fields = ("id", "created_at", "updated_at")

    def has_add_permission(self, request):
        # الصف ينشأ تلقائيًا عند أول قراءة عبر طبقة الخدمة
        return not PricingConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
