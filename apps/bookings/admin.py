"""Django Admin — Booking Domain."""

from django.contrib import admin

from .models import Booking, BookingServiceSelection, DispatchOffer


class BookingServiceSelectionInline(admin.TabularInline):
    """أسطر الخدمات تُعرض داخل الحجز — لا معنى لها منفصلة."""

    model = BookingServiceSelection
    extra = 0
    readonly_fields = ("id", "service_type", "room_count", "created_at")
    raw_id_fields = ("service_type",)

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    """
    ⚠️ computed_price للقراءة فقط: لقطة مجمَّدة تُكتب مرة واحدة آليًا عند
       قبول المقاول (المرحلة التالية). تحريرها يدويًا يفسد معنى اللقطة.

    🔒 للقراءة فقط بالكامل: الحالة والموعد والعقار تتغير عبر الـAPI وحده.
       ضبط CANCELLED يدويًا مثلًا كان يترك العروض حيّة، وتغيير الخدمات
       بعد القبول يفصل السعر المجمّد عمّا يُنفَّذ. الإلغاء سياسة مفتوحة (#12).
    """

    list_display = ("id", "customer", "property", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("customer__phone", "customer__email")
    ordering = ("-created_at",)
    raw_id_fields = ("customer", "property")
    inlines = [BookingServiceSelectionInline]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BookingServiceSelection)
class BookingServiceSelectionAdmin(admin.ModelAdmin):
    list_display = ("booking", "service_type", "room_count", "created_at")
    readonly_fields = ("id", "booking", "service_type", "room_count", "created_at")
    raw_id_fields = ("booking", "service_type")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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

    def has_delete_permission(self, request, obj=None):
        return False
