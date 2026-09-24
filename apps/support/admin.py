"""Django Admin — Support Domain."""

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import Truncator

from apps.audit.admin import EMPTY, BackOfficeMixin, short_id
from apps.audit.services.audit import record

from .models import SupportRequest, SupportStatus


@admin.register(SupportRequest)
class SupportRequestAdmin(BackOfficeMixin, admin.ModelAdmin):
    """
    ⚠️ الحالة وحدها قابلة للتعديل: الطلبات يفتحها المستخدمون عبر الـAPI،
       ومحتواها شهادة على ما كتبوه — تحريره يدويًا يفسد السجل.

    🔒 RESOLVED نهائية (SupportRequest.clean): يمرّ بها النموذج والأفعال معًا
       عبر full_clean، ويصبح الحقل للقراءة بعدها.

    📌 هذه هي الواجهة الوحيدة لتغيير الحالة في هذه المرحلة (لا مسار API).
       كل تغيير يُسجَّل في سجل التدقيق.
    """

    list_display = (
        "short",
        "user_link",
        "category",
        "status",
        "booking_link",
        "excerpt",
        "created_at",
        "updated_at",
    )
    list_filter = ("status", "category", "created_at")
    search_fields = ("user__phone", "user__email", "user__full_name", "message", "booking__public_reference")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user", "booking")
    actions = ["mark_under_review", "mark_resolved"]
    fieldsets = (
        ("Request", {"fields": ("id", "user_link", "booking_link", "category", "message")}),
        ("Handling", {"fields": ("status",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    base_readonly_fields = (
        "id",
        "user_link",
        "booking_link",
        "category",
        "message",
        "created_at",
        "updated_at",
    )
    readonly_fields = base_readonly_fields

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.status == SupportStatus.RESOLVED:
            return self.base_readonly_fields + ("status",)
        return self.base_readonly_fields

    def has_add_permission(self, request):
        """الطلبات تُفتح من التطبيق لا من هنا."""
        return False

    def has_delete_permission(self, request, obj=None):
        """سجل الدعم لا يُحذف — شهادة على واقعة."""
        return False

    @admin.display(description="Request")
    def short(self, obj):
        return short_id(obj.pk)

    @admin.display(description="User", ordering="user__phone")
    def user_link(self, obj):
        return self.link(obj.user, obj.user.full_name or obj.user.phone)

    @admin.display(description="Booking", ordering="booking__public_reference")
    def booking_link(self, obj):
        if obj.booking is None:
            return EMPTY
        return self.link(obj.booking, obj.booking.public_reference or short_id(obj.booking_id))

    @admin.display(description="Message")
    def excerpt(self, obj):
        return Truncator(obj.message).chars(80)

    def save_model(self, request, obj, form, change):
        previous = None
        if change and "status" in form.changed_data:
            previous = type(obj).objects.filter(pk=obj.pk).values_list("status", flat=True).first()
        with transaction.atomic():
            super().save_model(request, obj, form, change)
            if previous is not None and previous != obj.status:
                record(
                    request.user,
                    "support_request.status",
                    target=obj,
                    details={"from": previous, "to": obj.status},
                    request=request,
                )

    def _set_status(self, request, queryset, status):
        done = 0
        for support_request in queryset:
            previous = support_request.status
            if previous == status:
                continue
            support_request.status = status
            try:
                with transaction.atomic():
                    support_request.full_clean()
                    support_request.save(update_fields=["status", "updated_at"])
                    record(
                        request.user,
                        "support_request.status",
                        target=support_request,
                        details={"from": previous, "to": status},
                        request=request,
                    )
                done += 1
            except ValidationError as exc:
                self.message_user(
                    request, f"{short_id(support_request.pk)}: {'; '.join(exc.messages)}", level=messages.ERROR
                )
        self.message_user(request, f"Updated {done} request(s).", level=messages.SUCCESS)

    @admin.action(description="Mark as under review", permissions=["change"])
    def mark_under_review(self, request, queryset):
        self._set_status(request, queryset, SupportStatus.UNDER_REVIEW)

    @admin.action(description="Mark as resolved (final)", permissions=["change"])
    def mark_resolved(self, request, queryset):
        self._set_status(request, queryset, SupportStatus.RESOLVED)
