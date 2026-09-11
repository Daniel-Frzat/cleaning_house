"""Django Admin — Payment Domain."""

from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """
    ⚠️ للقراءة فقط: الدفعات تُنشأ آليًا لحظة تأكيد الحجز. تحريرها يدويًا
       يفسد مطابقة المبلغ للقطة السعر ويلتف على سجل المزوّد.
    """

    list_display = ("id", "booking", "amount", "method", "status", "created_at")
    list_filter = ("status", "method")
    search_fields = ("provider_reference", "booking__id")
    ordering = ("-created_at",)
    readonly_fields = (
        "id",
        "booking",
        "amount",
        "method",
        "status",
        "provider_reference",
        "failure_reason",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("booking",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
