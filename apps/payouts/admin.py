"""Django Admin — Payout Domain."""

from django.contrib import admin

from apps.audit.admin import BackOfficeMixin, ReadOnlyAdminMixin, csv_export_action, format_money, short_id

from .models import Payout

PAYOUT_CSV_COLUMNS = [
    ("id", lambda p: str(p.pk)),
    ("booking_reference", lambda p: p.booking.public_reference),
    ("contractor_phone", lambda p: p.contractor.phone),
    ("contractor_name", lambda p: p.contractor.full_name),
    ("currency", lambda p: p.booking.currency),
    ("amount", lambda p: p.amount),
    ("status", lambda p: p.get_status_display()),
    ("provider_reference", lambda p: p.provider_reference),
    ("failure_reason", lambda p: p.failure_reason),
    ("created_at", lambda p: p.created_at.isoformat()),
]


@admin.register(Payout)
class PayoutAdmin(BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    """
    ⚠️ للقراءة فقط: الدفعات تُنشأ آليًا لحظة تأكيد العميل. تحريرها يدويًا
       يفسد مطابقة المبلغ للقطة السعر ويلتف على سجل المزوّد.
    """

    list_display = (
        "short",
        "booking_link",
        "contractor_link",
        "amount_display",
        "status",
        "created_at",
        "updated_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "provider_reference",
        "booking__public_reference",
        "contractor__phone",
        "contractor__full_name",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("booking", "contractor")
    actions = [csv_export_action(PAYOUT_CSV_COLUMNS, "payouts", "payout.export")]
    fieldsets = (
        ("Payout", {"fields": ("id", "booking_link", "contractor_link", "amount_display", "status")}),
        ("Provider", {"fields": ("provider_reference", "failure_reason")}),
        ("Timeline", {"fields": ("created_at", "updated_at")}),
    )
    readonly_fields = (
        "id",
        "booking_link",
        "contractor_link",
        "amount_display",
        "status",
        "provider_reference",
        "failure_reason",
        "created_at",
        "updated_at",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("booking", "contractor")

    @admin.display(description="Payout")
    def short(self, obj):
        return short_id(obj.pk)

    @admin.display(description="Booking", ordering="booking__public_reference")
    def booking_link(self, obj):
        return self.link(obj.booking, obj.booking.public_reference or short_id(obj.booking_id))

    @admin.display(description="Contractor", ordering="contractor__phone")
    def contractor_link(self, obj):
        return self.link(obj.contractor, obj.contractor.full_name or obj.contractor.phone)

    @admin.display(description="Amount", ordering="amount")
    def amount_display(self, obj):
        return format_money(obj.amount, obj.booking.currency)
