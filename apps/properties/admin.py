"""Django Admin — Properties & Address Domain."""

from django.contrib import admin

from .models import Property, PropertyAddress


class PropertyAddressInline(admin.StackedInline):
    """العنوان كيان منفصل، لكن عرضه بجانب العقار أسهل للتشغيل."""

    model = PropertyAddress
    can_delete = False
    extra = 0
    readonly_fields = ("id", "country", "created_at", "updated_at")


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("__str__", "owner", "property_type", "is_active", "created_at")
    list_filter = ("property_type", "is_active")
    search_fields = ("label", "owner__phone", "owner__email")
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("owner",)
    inlines = [PropertyAddressInline]


@admin.register(PropertyAddress)
class PropertyAddressAdmin(admin.ModelAdmin):
    list_display = ("street_address", "suburb", "state", "postcode", "property")
    list_filter = ("state",)
    search_fields = ("street_address", "suburb", "postcode")
    readonly_fields = ("id", "country", "created_at", "updated_at")
    raw_id_fields = ("property",)
