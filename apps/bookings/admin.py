"""Django Admin — Booking Domain."""

from django.contrib import admin

from .models import Booking, BookingServiceSelection, DispatchOffer


class BookingServiceSelectionInline(admin.TabularInline):
    """أسطر الخدمات تُعرض داخل الحجز — لا معنى لها منفصلة."""

    model = BookingServiceSelection
    extra = 0
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("service_type",)


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    """
    ⚠️ computed_price للقراءة فقط: لقطة مجمَّدة تُكتب مرة واحدة آليًا عند
       قبول المقاول (المرحلة التالية). تحريرها يدويًا يفسد معنى اللقطة.
    """

    list_display = ("id", "customer", "property", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("customer__phone", "customer__email")
    ordering = ("-created_at",)
    readonly_fields = (
        "id",
        "computed_price",
        "assigned_contractor",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("customer", "property")
    inlines = [BookingServiceSelectionInline]


@admin.register(BookingServiceSelection)
class BookingServiceSelectionAdmin(admin.ModelAdmin):
    list_display = ("booking", "service_type", "room_count", "created_at")
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("booking", "service_type")


@admin.register(DispatchOffer)
class DispatchOfferAdmin(admin.ModelAdmin):
    """
    ⚠️ للقراءة فقط: العروض يولّدها محرّك الإسناد ويردّ عليها المقاول عبر
       الـAPI. تحريرها يدويًا يلتف على قواعد المهلة والتتابع.
    """

    list_display = (
        "id",
        "booking",
        "contractor",
        "status",
        "distance_km",
        "offered_at",
        "expires_at",
    )
    list_filter = ("status",)
    ordering = ("-offered_at",)
    readonly_fields = (
        "id",
        "booking",
        "contractor",
        "status",
        "distance_km",
        "offered_at",
        "responded_at",
        "expires_at",
    )
    raw_id_fields = ("booking", "contractor")

    def has_add_permission(self, request):
        return False
