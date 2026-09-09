"""
Django Admin — Identity Domain (Phase 1 — Step 1)

Admin مخصص لأن User لا يحتوي username (المعرّف هو phone).
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm

from .forms import UserChangeForm, UserCreationForm
from .models import OTPVerification, SocialAccount, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = UserCreationForm
    form = UserChangeForm
    change_password_form = AdminPasswordChangeForm
    model = User

    list_display = ("phone", "role", "status", "is_active")
    list_filter = ("role", "status", "is_active", "is_staff", "is_superuser")
    search_fields = ("phone", "email", "full_name")
    ordering = ("-date_joined",)
    readonly_fields = ("id", "date_joined", "updated_at", "last_login")

    fieldsets = (
        (None, {"fields": ("id", "phone", "password")}),
        ("Personal info", {"fields": ("full_name", "email")}),
        ("Domain", {"fields": ("role", "status")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Important dates", {"fields": ("last_login", "date_joined", "updated_at")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("phone", "full_name", "email", "role", "status", "password1", "password2"),
            },
        ),
    )


@admin.register(OTPVerification)
class OTPVerificationAdmin(admin.ModelAdmin):
    """
    عرض للقراءة فقط — سجلات OTP أدلة أمنية ولا تُحرَّر يدويًا.
    🔒 لا يوجد code_hash في list_display ولا بحث به.
    """

    list_display = ("phone", "purpose", "status", "attempts_count", "max_attempts", "expires_at", "created_at")
    list_filter = ("status", "purpose")
    search_fields = ("phone",)
    ordering = ("-created_at",)
    readonly_fields = (
        "id", "phone", "purpose", "status", "attempts_count",
        "max_attempts", "expires_at", "created_at",
    )
    exclude = ("code_hash",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    """ربط الهويات الخارجية — للعرض والتدقيق."""

    list_display = ("user", "provider", "provider_user_id", "email", "linked_at")
    list_filter = ("provider",)
    search_fields = ("provider_user_id", "email", "user__phone")
    ordering = ("-linked_at",)
    readonly_fields = ("id", "linked_at", "raw_profile")
    raw_id_fields = ("user",)
