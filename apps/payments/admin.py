"""Django Admin — Payment Domain."""

from django.contrib import admin

from apps.audit.admin import (
    BackOfficeMixin,
    ReadOnlyAdminMixin,
    csv_export_action,
    format_money,
    pretty_json,
    short_id,
)

from .models import Payment

PAYMENT_CSV_COLUMNS = [
    ("id", lambda p: str(p.pk)),
    ("booking_reference", lambda p: p.booking.public_reference),
    ("customer_phone", lambda p: p.booking.customer.phone),
    ("currency", lambda p: p.booking.currency),
    ("amount", lambda p: p.amount),
    ("method", lambda p: p.get_method_display()),
    ("status", lambda p: p.get_status_display()),
    ("attempt_number", lambda p: p.attempt_number),
    ("provider_reference", lambda p: p.provider_reference),
    ("failure_reason", lambda p: p.failure_reason),
    ("paid_at", lambda p: p.paid_at.isoformat() if p.paid_at else ""),
    ("created_at", lambda p: p.created_at.isoformat()),
]
# 🔒 خارج التصدير وخارج الصفحة: action_payload (بيانات مصادقة 3-D Secure
#    يعيدها المزوّد للعميل) — لا تُعرض لأحد في اللوحة.


@admin.register(Payment)
class PaymentAdmin(BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    """
    ⚠️ للقراءة فقط: الدفعات تُنشأ آليًا لحظة تأكيد الحجز. تحريرها يدويًا
       يفسد مطابقة المبلغ للقطة السعر ويلتف على سجل المزوّد.
    """

    list_display = (
        "short",
        "booking_link",
        "customer_link",
        "amount_display",
        "method",
        "status",
        "attempt_number",
        "paid_at",
        "created_at",
    )
    list_filter = ("status", "method", "created_at", "paid_at")
    search_fields = (
        "provider_reference",
        "booking__public_reference",
        "booking__customer__phone",
        "booking__customer__email",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("booking", "booking__customer")
    actions = [csv_export_action(PAYMENT_CSV_COLUMNS, "payments", "payment.export")]
    fieldsets = (
        (
            "Payment",
            {
                "fields": (
                    "id",
                    "booking_link",
                    "customer_link",
                    "amount_display",
                    "method",
                    "method_summary_display",
                    "status",
                    "attempt_number",
                )
            },
        ),
        ("Provider", {"fields": ("provider_reference", "provider_error_code", "failure_reason")}),
        ("Timeline", {"fields": ("created_at", "paid_at", "updated_at")}),
    )
    readonly_fields = (
        "id",
        "booking_link",
        "customer_link",
        "amount_display",
        "method",
        "method_summary_display",
        "status",
        "attempt_number",
        "provider_reference",
        "provider_error_code",
        "failure_reason",
        "created_at",
        "paid_at",
        "updated_at",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("booking", "booking__customer")

    @admin.display(description="Payment")
    def short(self, obj):
        return short_id(obj.pk)

    @admin.display(description="Booking", ordering="booking__public_reference")
    def booking_link(self, obj):
        return self.link(obj.booking, obj.booking.public_reference or short_id(obj.booking_id))

    @admin.display(description="Customer", ordering="booking__customer__phone")
    def customer_link(self, obj):
        customer = obj.booking.customer
        return self.link(customer, customer.full_name or customer.phone)

    @admin.display(description="Amount", ordering="amount")
    def amount_display(self, obj):
        return format_money(obj.amount, obj.booking.currency)

    @admin.display(description="Method details")
    def method_summary_display(self, obj):
        return pretty_json(obj.method_summary)
