"""
Django Admin — Properties & Address Domain.

🔒 نفس قواعد طبقة الخدمة (services/properties.py) تُفرض هنا في النماذج:
   - المالك لا يتغير (الملكية لا تُنقل).
   - لا تعطيل لعقار عليه حجز معلّق أو في الطريق.
   - لا تعديل لعنوان عقار عليه حجز في الطريق، ولا نقله لولاية أخرى
     وعليه حجز معلّق (الولاية تحدد المنطقة الزمنية المخزَّنة).
   - لا إضافة ولا حذف: العقار ينشئه العميل عبر الـAPI، والإزالة تعطيل ناعم.
"""

from django import forms
from django.contrib import admin
from django.db.models import Count

from apps.audit.admin import EMPTY, BackOfficeMixin

from .models import Property, PropertyAddress
from .services.properties import has_in_flight_bookings, has_pending_bookings

ACTIVE_BOOKINGS_MESSAGE = "This property has active bookings and cannot be {} until they finish."


class PropertyAdminForm(forms.ModelForm):
    class Meta:
        model = Property
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        prop = self.instance
        if (
            prop.pk is not None
            and "is_active" in self.changed_data
            and cleaned.get("is_active") is False
            and (has_in_flight_bookings(prop) or has_pending_bookings(prop))
        ):
            raise forms.ValidationError(ACTIVE_BOOKINGS_MESSAGE.format("deactivated"))
        return cleaned


class PropertyAddressAdminForm(forms.ModelForm):
    class Meta:
        model = PropertyAddress
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        address = self.instance
        if address.pk is None or not self.changed_data:
            return cleaned
        prop = address.property
        if has_in_flight_bookings(prop):
            raise forms.ValidationError(ACTIVE_BOOKINGS_MESSAGE.format("re-addressed"))
        if (
            "state" in self.changed_data
            and cleaned.get("state") != address.state
            and has_pending_bookings(prop)
        ):
            raise forms.ValidationError(ACTIVE_BOOKINGS_MESSAGE.format("moved to another state"))
        return cleaned


class PropertyAddressInline(admin.StackedInline):
    """العنوان كيان منفصل، لكن عرضه بجانب العقار أسهل للتشغيل."""

    model = PropertyAddress
    form = PropertyAddressAdminForm
    can_delete = False
    extra = 0
    max_num = 1
    fields = (
        ("street_address", "suburb"),
        ("state", "postcode", "country"),
        ("latitude", "longitude"),
        "raw_input",
        ("created_at", "updated_at"),
    )
    readonly_fields = ("country", "raw_input", "created_at", "updated_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Property)
class PropertyAdmin(BackOfficeMixin, admin.ModelAdmin):
    form = PropertyAdminForm
    list_display = (
        "display_name",
        "owner_link",
        "property_type",
        "suburb",
        "state",
        "booking_count",
        "is_active",
        "created_at",
    )
    list_filter = ("property_type", "is_active", "address__state", "created_at")
    search_fields = (
        "label",
        "owner__phone",
        "owner__email",
        "owner__full_name",
        "address__street_address",
        "address__suburb",
        "address__postcode",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("owner", "address")
    readonly_fields = ("id", "owner_link", "created_at", "updated_at")
    fieldsets = (
        ("Property", {"fields": ("id", "owner_link", "label", "property_type", "is_active")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    inlines = [PropertyAddressInline]

    def has_add_permission(self, request):
        """العقار ينشئه العميل عبر الـAPI (يفحص أن المالك عميل)."""
        return False

    def has_delete_permission(self, request, obj=None):
        """الإزالة تعطيل ناعم — الحذف يمحو اقتباسات العميل تتاليًا."""
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("owner", "address")
            .annotate(_booking_count=Count("bookings"))
        )

    @admin.display(description="Property", ordering="label")
    def display_name(self, obj):
        return str(obj)

    @admin.display(description="Owner", ordering="owner__phone")
    def owner_link(self, obj):
        return self.link(obj.owner, obj.owner.full_name or obj.owner.phone)

    @admin.display(description="Suburb", ordering="address__suburb")
    def suburb(self, obj):
        address = getattr(obj, "address", None)
        return address.suburb if address else EMPTY

    @admin.display(description="State", ordering="address__state")
    def state(self, obj):
        address = getattr(obj, "address", None)
        return address.get_state_display() if address else EMPTY

    @admin.display(description="Bookings", ordering="_booking_count")
    def booking_count(self, obj):
        return getattr(obj, "_booking_count", None)


@admin.register(PropertyAddress)
class PropertyAddressAdmin(BackOfficeMixin, admin.ModelAdmin):
    form = PropertyAddressAdminForm
    list_display = ("street_address", "suburb", "state", "postcode", "has_coordinates", "property_link")
    list_filter = ("state",)
    search_fields = ("street_address", "suburb", "postcode", "property__owner__phone")
    ordering = ("state", "suburb")
    list_select_related = ("property",)
    readonly_fields = ("id", "property_link", "country", "raw_input", "created_at", "updated_at")
    fieldsets = (
        ("Property", {"fields": ("id", "property_link")}),
        ("Address", {"fields": ("street_address", "suburb", "state", "postcode", "country", "raw_input")}),
        ("Coordinates", {"fields": ("latitude", "longitude")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Property")
    def property_link(self, obj):
        return self.link(obj.property)

    @admin.display(description="GPS", boolean=True)
    def has_coordinates(self, obj):
        return obj.latitude is not None and obj.longitude is not None
