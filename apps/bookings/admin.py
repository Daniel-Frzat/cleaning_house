"""
Django Admin — Booking Domain.

🔒 كل ما هنا للقراءة فقط: الحجز وأسطر خدماته وعروض الإسناد والاقتباسات
   تتغير عبر الـAPI وطبقة الخدمة وحدهما. لا إضافة ولا تعديل ولا حذف.
"""

from django.contrib import admin

from apps.audit.admin import (
    EMPTY,
    BackOfficeMixin,
    ReadOnlyAdminMixin,
    csv_export_action,
    format_money,
    pretty_json,
    short_id,
)

from .models import Booking, BookingQuote, BookingServiceSelection, DispatchOffer


def _booking_label(booking):
    return booking.public_reference or short_id(booking.pk)


# ============================================================
# Inlines
# ============================================================
class BookingServiceSelectionInline(BackOfficeMixin, ReadOnlyAdminMixin, admin.TabularInline):
    """أسطر الخدمات تُعرض داخل الحجز — لا معنى لها منفصلة."""

    model = BookingServiceSelection
    extra = 0
    fields = ("service_link", "room_count", "created_at")
    readonly_fields = fields

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("service_type")

    @admin.display(description="Service")
    def service_link(self, obj):
        return self.link(obj.service_type)


class DispatchOfferInline(BackOfficeMixin, ReadOnlyAdminMixin, admin.TabularInline):
    """سجل العروض كاملًا بكل جولاته — من رفض ومتى."""

    model = DispatchOffer
    extra = 0
    fields = (
        "contractor_link",
        "dispatch_round",
        "status",
        "distance_km",
        "total_display",
        "offered_at",
        "responded_at",
        "expires_at",
    )
    readonly_fields = fields
    show_change_link = True
    ordering = ("-offered_at",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("contractor")

    @admin.display(description="Contractor")
    def contractor_link(self, obj):
        return self.link(obj.contractor)

    @admin.display(description="Total")
    def total_display(self, obj):
        return format_money(obj.total_amount, obj.currency)


# ============================================================
# Booking
# ============================================================
BOOKING_CSV_COLUMNS = [
    ("reference", lambda b: b.public_reference),
    ("id", lambda b: str(b.pk)),
    ("status", lambda b: b.get_status_display()),
    ("dispatch_status", lambda b: b.get_dispatch_status_display()),
    ("customer_phone", lambda b: b.customer.phone),
    ("customer_email", lambda b: b.customer.email),
    ("property", lambda b: str(b.property)),
    ("contractor", lambda b: str(b.assigned_contractor) if b.assigned_contractor else ""),
    ("requested_at", lambda b: b.requested_at.isoformat() if b.requested_at else ""),
    ("scheduled_at", lambda b: b.scheduled_at.isoformat() if b.scheduled_at else ""),
    ("currency", lambda b: b.currency),
    ("max_total", lambda b: b.max_total),
    ("computed_price", lambda b: b.computed_price),
    ("pricing_version", lambda b: b.pricing_version),
    ("created_at", lambda b: b.created_at.isoformat()),
]
# 🔒 خارج التصدير عمدًا: access_notes (قد تحوي مكان المفتاح) و
#    payment_method_reference (رمز مزوّد).


@admin.register(Booking)
class BookingAdmin(BackOfficeMixin, admin.ModelAdmin):
    """
    ⚠️ computed_price للقراءة فقط: لقطة مجمَّدة تُكتب مرة واحدة آليًا عند
       قبول المقاول. تحريرها يدويًا يفسد معنى اللقطة.

    🔒 للقراءة فقط بالكامل: الحالة والموعد والعقار تتغير عبر الـAPI وحده.
       ضبط CANCELLED يدويًا مثلًا كان يترك العروض حيّة، وتغيير الخدمات
       بعد القبول يفصل السعر المجمّد عمّا يُنفَّذ. الإلغاء سياسة مفتوحة (#12).
    """

    list_display = (
        "reference",
        "customer_link",
        "property_link",
        "status",
        "dispatch_status",
        "contractor_link",
        "price_display",
        "payment_status",
        "requested_at",
        "created_at",
    )
    list_filter = ("status", "dispatch_status", "created_at", "scheduled_at")
    search_fields = (
        "public_reference",
        "customer__phone",
        "customer__email",
        "customer__full_name",
        "assigned_contractor__business_name",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("customer", "property", "assigned_contractor", "payment")
    inlines = [BookingServiceSelectionInline, DispatchOfferInline]
    actions = [
        csv_export_action(BOOKING_CSV_COLUMNS, "bookings", "booking.export"),
    ]

    fieldsets = (
        (
            "Booking",
            {"fields": ("id", "public_reference", "status", "customer_link", "property_link")},
        ),
        ("Schedule", {"fields": ("requested_at", "scheduled_at", "customer_timezone")}),
        (
            "Dispatch",
            {
                "fields": (
                    "dispatch_status",
                    "dispatch_round",
                    "last_dispatch_attempt_at",
                    "contractor_link",
                )
            },
        ),
        (
            "Pricing (frozen snapshots)",
            {"fields": ("quote_link", "max_total_display", "computed_price_display", "pricing_version")},
        ),
        ("Related records", {"fields": ("payment_link", "job_link", "payout_link")}),
        (
            "Customer arrival notes",
            {
                "classes": ("collapse",),
                "description": "Visible to the assigned contractor only; do not share.",
                "fields": ("access_notes",),
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    readonly_fields = (
        "id",
        "public_reference",
        "status",
        "customer_link",
        "property_link",
        "requested_at",
        "scheduled_at",
        "customer_timezone",
        "dispatch_status",
        "dispatch_round",
        "last_dispatch_attempt_at",
        "contractor_link",
        "quote_link",
        "max_total_display",
        "computed_price_display",
        "pricing_version",
        "payment_link",
        "job_link",
        "payout_link",
        "access_notes",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        # ⚠️ ChangeList يتجاهل list_select_related متى وُجد select_related هنا
        return super().get_queryset(request).select_related(
            "customer", "property", "assigned_contractor", "quote", "payment"
        )

    @admin.display(description="Reference", ordering="public_reference")
    def reference(self, obj):
        return _booking_label(obj)

    @admin.display(description="Customer", ordering="customer__phone")
    def customer_link(self, obj):
        return self.link(obj.customer, obj.customer.full_name or obj.customer.phone)

    @admin.display(description="Property")
    def property_link(self, obj):
        return self.link(obj.property)

    @admin.display(description="Contractor", ordering="assigned_contractor__business_name")
    def contractor_link(self, obj):
        return self.link(obj.assigned_contractor)

    @admin.display(description="Price")
    def price_display(self, obj):
        if obj.computed_price is not None:
            return format_money(obj.computed_price, obj.currency)
        if obj.max_total is not None:
            return f"≤ {format_money(obj.max_total, obj.currency)}"
        return EMPTY

    @admin.display(description="Payment")
    def payment_status(self, obj):
        payment = getattr(obj, "payment", None)
        return payment.get_status_display() if payment else EMPTY

    @admin.display(description="Quote")
    def quote_link(self, obj):
        return self.link(obj.quote)

    @admin.display(description="Approved maximum")
    def max_total_display(self, obj):
        return format_money(obj.max_total, obj.currency)

    @admin.display(description="Final price")
    def computed_price_display(self, obj):
        return format_money(obj.computed_price, obj.currency)

    @admin.display(description="Payment")
    def payment_link(self, obj):
        payment = getattr(obj, "payment", None)
        if payment is None:
            return EMPTY
        return self.link(
            payment,
            f"{format_money(payment.amount, obj.currency)} — {payment.get_status_display()}",
        )

    @admin.display(description="Job")
    def job_link(self, obj):
        job = getattr(obj, "job", None)
        return self.link(job, job.get_status_display()) if job else EMPTY

    @admin.display(description="Payout")
    def payout_link(self, obj):
        payout = getattr(obj, "payout", None)
        if payout is None:
            return EMPTY
        return self.link(
            payout,
            f"{format_money(payout.amount, obj.currency)} — {payout.get_status_display()}",
        )


@admin.register(BookingServiceSelection)
class BookingServiceSelectionAdmin(BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("booking_link", "service_link", "room_count", "created_at")
    list_filter = ("service_type", "created_at")
    search_fields = ("booking__public_reference", "service_type__name")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("booking", "service_type")
    fields = ("id", "booking_link", "service_link", "room_count", "created_at")
    readonly_fields = fields

    @admin.display(description="Booking", ordering="booking__public_reference")
    def booking_link(self, obj):
        return self.link(obj.booking, _booking_label(obj.booking))

    @admin.display(description="Service", ordering="service_type__name")
    def service_link(self, obj):
        return self.link(obj.service_type)


@admin.register(DispatchOffer)
class DispatchOfferAdmin(BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    """
    ⚠️ للقراءة فقط: العروض يولّدها محرّك الإسناد ويردّ عليها المقاول عبر
       الـAPI. تحريرها يدويًا يلتف على قواعد المهلة والتتابع.
    """

    list_display = (
        "short",
        "booking_link",
        "contractor_link",
        "status",
        "dispatch_round",
        "distance_km",
        "total_display",
        "offered_at",
        "responded_at",
        "expires_at",
    )
    list_filter = ("status", "distance_source", "offered_at")
    search_fields = ("booking__public_reference", "contractor__business_name", "contractor__user__phone")
    date_hierarchy = "offered_at"
    ordering = ("-offered_at",)
    list_select_related = ("booking", "contractor")
    fieldsets = (
        ("Offer", {"fields": ("id", "booking_link", "contractor_link", "status", "dispatch_round")}),
        ("Distance", {"fields": ("distance_km", "distance_source", "eta_seconds")}),
        (
            "Price snapshot",
            {
                "fields": (
                    "services_total_display",
                    "travel_fee_display",
                    "total_display",
                    "earnings_display",
                    "pricing_version",
                )
            },
        ),
        ("Timing", {"fields": ("offered_at", "responded_at", "expires_at")}),
    )
    readonly_fields = (
        "id",
        "booking_link",
        "contractor_link",
        "status",
        "dispatch_round",
        "distance_km",
        "distance_source",
        "eta_seconds",
        "services_total_display",
        "travel_fee_display",
        "total_display",
        "earnings_display",
        "pricing_version",
        "offered_at",
        "responded_at",
        "expires_at",
    )

    @admin.display(description="Offer")
    def short(self, obj):
        return short_id(obj.pk)

    @admin.display(description="Booking", ordering="booking__public_reference")
    def booking_link(self, obj):
        return self.link(obj.booking, _booking_label(obj.booking))

    @admin.display(description="Contractor", ordering="contractor__business_name")
    def contractor_link(self, obj):
        return self.link(obj.contractor)

    @admin.display(description="Services total")
    def services_total_display(self, obj):
        return format_money(obj.services_total, obj.currency)

    @admin.display(description="Travel fee")
    def travel_fee_display(self, obj):
        return format_money(obj.travel_fee, obj.currency)

    @admin.display(description="Total", ordering="total_amount")
    def total_display(self, obj):
        return format_money(obj.total_amount, obj.currency)

    @admin.display(description="Contractor earnings")
    def earnings_display(self, obj):
        return format_money(obj.contractor_earnings, obj.currency)


@admin.register(BookingQuote)
class BookingQuoteAdmin(BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    """🔒 لقطة تسعير مجمَّدة وافق عليها العميل — شاهد لا يُحرَّر."""

    list_display = (
        "short",
        "customer_link",
        "property_link",
        "services_total_display",
        "maximum_total_display",
        "pricing_version",
        "expires_at",
        "is_expired_display",
        "created_at",
    )
    list_filter = ("pricing_version", "created_at")
    search_fields = ("customer__phone", "customer__email", "bookings__public_reference")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("customer", "property")
    fieldsets = (
        ("Quote", {"fields": ("id", "customer_link", "property_link", "bookings_display")}),
        (
            "Frozen prices",
            {
                "fields": (
                    "services_total_display",
                    "maximum_total_display",
                    "pricing_version",
                    "snapshot_display",
                )
            },
        ),
        ("Timing", {"fields": ("expires_at", "is_expired_display", "created_at")}),
    )
    readonly_fields = (
        "id",
        "customer_link",
        "property_link",
        "bookings_display",
        "services_total_display",
        "maximum_total_display",
        "pricing_version",
        "snapshot_display",
        "expires_at",
        "is_expired_display",
        "created_at",
    )

    @admin.display(description="Quote")
    def short(self, obj):
        return short_id(obj.pk)

    @admin.display(description="Customer", ordering="customer__phone")
    def customer_link(self, obj):
        return self.link(obj.customer, obj.customer.full_name or obj.customer.phone)

    @admin.display(description="Property")
    def property_link(self, obj):
        return self.link(obj.property)

    @admin.display(description="Services total", ordering="services_total")
    def services_total_display(self, obj):
        return format_money(obj.services_total, obj.currency)

    @admin.display(description="Maximum total", ordering="maximum_total")
    def maximum_total_display(self, obj):
        return format_money(obj.maximum_total, obj.currency)

    @admin.display(description="Expired", boolean=True)
    def is_expired_display(self, obj):
        return obj.is_expired()

    @admin.display(description="Used by booking")
    def bookings_display(self, obj):
        from django.utils.html import format_html_join

        bookings = list(obj.bookings.all())
        if not bookings:
            return EMPTY
        return format_html_join(", ", "{}", ((self.link(b, _booking_label(b)),) for b in bookings))

    @admin.display(description="Service snapshot")
    def snapshot_display(self, obj):
        return pretty_json(obj.service_snapshot)
