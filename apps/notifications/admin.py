"""Django Admin — Notifications (عرض ومتابعة الإرسال؛ الإرسال العام من لوحة التحكم)."""

from django.contrib import admin
from django.utils.text import Truncator

from apps.audit.admin import BackOfficeMixin, ReadOnlyAdminMixin

from .models import Broadcast, DeviceToken, Notification


@admin.register(Notification)
class NotificationAdmin(BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("created_at", "user_link", "type", "audience", "title", "push_status", "is_read")
    list_filter = ("push_status", "audience", "type", "priority")
    search_fields = ("user__phone", "user__email", "title")
    date_hierarchy = "created_at"
    list_select_related = ("user",)
    ordering = ("-created_at",)
    fieldsets = (
        (None, {"fields": ("user", "audience", "type", "priority", "title", "body", "data")}),
        ("State", {"fields": ("created_at", "read_at", "broadcast")}),
        ("Push delivery", {"fields": ("push_status", "push_attempts", "pushed_at", "push_error")}),
    )

    @admin.display(description="User")
    def user_link(self, obj):
        return self.link(obj.user)

    @admin.display(description="Read", boolean=True)
    def is_read(self, obj):
        return obj.read_at is not None


@admin.register(Broadcast)
class BroadcastAdmin(BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("created_at", "target", "title", "recipients_count", "created_by_link")
    list_filter = ("target",)
    search_fields = ("title", "body")
    date_hierarchy = "created_at"
    list_select_related = ("created_by",)

    @admin.display(description="Sent by")
    def created_by_link(self, obj):
        return self.link(obj.created_by)


@admin.register(DeviceToken)
class DeviceTokenAdmin(BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    """🔒 التوكن نفسه لا يُعرض كاملًا — يكفي أول أحرفه للتعرّف على الجهاز."""

    list_display = ("user_link", "platform", "app_version", "token_hint", "last_seen_at", "created_at")
    list_filter = ("platform",)
    search_fields = ("user__phone", "user__email")
    list_select_related = ("user",)
    exclude = ("token",)
    readonly_fields = ("token_hint",)

    def has_delete_permission(self, request, obj=None):
        # حذف توكن = إيقاف الإشعارات عن ذلك الجهاز (مفيد للدعم)
        return request.user.is_superuser

    @admin.display(description="User")
    def user_link(self, obj):
        return self.link(obj.user)

    @admin.display(description="Token")
    def token_hint(self, obj):
        return Truncator(obj.token).chars(16)
