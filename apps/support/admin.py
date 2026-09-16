"""Django Admin — Support Domain."""

from django.contrib import admin

from .models import SupportRequest


@admin.register(SupportRequest)
class SupportRequestAdmin(admin.ModelAdmin):
    """
    ⚠️ الحالة وحدها قابلة للتعديل: الطلبات يفتحها المستخدمون عبر الـAPI،
       ومحتواها شهادة على ما كتبوه — تحريره يدويًا يفسد السجل.

    📌 هذه هي الواجهة الوحيدة لتغيير الحالة في هذه المرحلة (لا مسار API).
    """

    list_display = ("id", "user", "category", "status", "booking", "created_at")
    list_filter = ("status", "category")
    search_fields = ("user__phone", "user__email", "message", "booking__public_reference")
    ordering = ("-created_at",)
    readonly_fields = (
        "id",
        "user",
        "booking",
        "category",
        "message",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("user", "booking")

    def has_add_permission(self, request):
        """الطلبات تُفتح من التطبيق لا من هنا."""
        return False

    def has_delete_permission(self, request, obj=None):
        """سجل الدعم لا يُحذف — شهادة على واقعة."""
        return False
