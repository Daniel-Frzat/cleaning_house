"""
Django Admin — Identity Domain.

Admin مخصص لأن User لا يحتوي username (المعرّف هو phone).

🔒 قواعد لا تُكسر هنا:
   - الدور ADMIN مصدر الحقيقة الوحيد لـis_staff/is_superuser (يُزامَن في
     User.save): is_staff للقراءة دائمًا، وis_superuser لا يظهر إلا لـsuperuser.
   - موظف إداري غير superuser لا يغيّر دور أي حساب ولا is_superuser ولا
     المجموعات ولا الصلاحيات — ولا يحرّر حساب ADMIN آخر إطلاقًا.
   - الحالة (ACTIVE/SUSPENDED) تتغير عبر الأفعال وحدها: الإيقاف يُبطل
     توكنات refresh والأجهزة الموثوقة ويُسجَّل في سجل التدقيق. تحرير الحقل
     مباشرة في النموذج كان يتخطى ذلك كله.
   - email_verified للقراءة: رفعه يدويًا يفتح ربط دخول اجتماعي بحساب
     لم تثبت ملكية بريده (account pre-hijacking).
   - AdminLoginChallenge غير مسجَّل: خطوة دخول عابرة لا قيمة تشغيلية لها.
"""

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.db import transaction
from django.utils import timezone
from ninja_jwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from apps.audit.admin import EMPTY, BackOfficeMixin, ReadOnlyAdminMixin, pretty_json
from apps.audit.services.audit import record
from apps.properties.models import Property

from .forms import UserChangeForm, UserCreationForm
from .models import OTPVerification, SocialAccount, TrustedDevice, User, UserStatus
from .roles import ConfirmedRole


# ============================================================
# أدوات إبطال الجلسات — تُستعمل من الأفعال وإعادة تعيين كلمة السر
# ============================================================
def blacklist_refresh_tokens(user):
    """يضيف كل توكن refresh سارٍ للمستخدم إلى القائمة السوداء. يعيد العدد."""
    outstanding = OutstandingToken.objects.filter(
        user=user, expires_at__gt=timezone.now(), blacklistedtoken__isnull=True
    )
    count = 0
    for token in outstanding:
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        count += int(created)
    return count


def revoke_trusted_devices(user):
    """يُبطل كل جهاز موثوق سارٍ (revoked_at) — لا حذف، فيبقى الأثر."""
    return TrustedDevice.objects.filter(user=user, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )


def can_manage_account(actor, target):
    """
    🔒 حساب ADMIN آخر لا يديره إلا superuser. الحساب نفسه مستثنى من
       القراءة فقط (يحق للموظف رؤية حسابه)، لكن الأفعال ترفض الذات.
    """
    if actor.is_superuser:
        return True
    return target.role != ConfirmedRole.ADMIN


# ============================================================
# Inlines
# ============================================================
class SocialAccountInline(BackOfficeMixin, ReadOnlyAdminMixin, admin.TabularInline):
    model = SocialAccount
    extra = 0
    fields = ("provider", "provider_user_id", "email", "linked_at")
    readonly_fields = fields
    show_change_link = True


class TrustedDeviceInline(BackOfficeMixin, ReadOnlyAdminMixin, admin.TabularInline):
    """
    🔒 لا token_hash. الإبطال عبر فعل "Revoke trusted devices" وحده —
       يضبط revoked_at ولا يحذف السجل.
    """

    model = TrustedDevice
    extra = 0
    fields = ("label", "ip_address", "created_at", "last_used_at", "expires_at", "revoked_at", "state")
    readonly_fields = fields
    verbose_name_plural = "Trusted admin devices"

    @admin.display(description="State")
    def state(self, obj):
        if obj.revoked_at:
            return "Revoked"
        return "Active" if obj.is_valid() else "Expired"


class PropertyInline(BackOfficeMixin, ReadOnlyAdminMixin, admin.TabularInline):
    model = Property
    fk_name = "owner"
    extra = 0
    fields = ("label", "property_type", "address_display", "is_active", "created_at")
    readonly_fields = fields
    show_change_link = True
    verbose_name_plural = "Properties"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("address")

    @admin.display(description="Address")
    def address_display(self, obj):
        address = getattr(obj, "address", None)
        return str(address) if address else EMPTY


# ============================================================
# User
# ============================================================
@admin.register(User)
class UserAdmin(BackOfficeMixin, BaseUserAdmin):
    add_form = UserCreationForm
    form = UserChangeForm
    change_password_form = AdminPasswordChangeForm
    model = User

    list_display = (
        "phone",
        "full_name",
        "email",
        "role_display",
        "is_contractor",
        "status_display",
        "is_active",
        "date_joined",
        "last_login",
    )
    list_display_links = ("phone", "full_name")
    list_filter = (
        "role",
        "is_contractor",
        "status",
        "is_active",
        "is_superuser",
        "must_change_password",
        "date_joined",
    )
    search_fields = ("phone", "email", "full_name")
    ordering = ("-date_joined",)
    date_hierarchy = "date_joined"
    filter_horizontal = ("groups", "user_permissions")
    inlines = [PropertyInline, SocialAccountInline, TrustedDeviceInline]
    actions = ["activate_accounts", "suspend_accounts", "revoke_devices", "clear_login_lockout"]

    base_readonly_fields = (
        "id",
        "is_staff",
        "email_verified",
        "contractor_profile_link",
        "must_change_password",
        "failed_login_attempts",
        "locked_until",
        "password_changed_at",
        "last_login",
        "date_joined",
        "updated_at",
    )
    readonly_fields = base_readonly_fields

    fieldsets = (
        (None, {"fields": ("id", "phone", "password")}),
        ("Personal info", {"fields": ("full_name", "email", "email_verified")}),
        # is_contractor ظاهر حتى يمكن سحب صفة العامل، ويُرفض مع ADMIN
        (
            "Role and status",
            {"fields": ("role", "is_contractor", "contractor_profile_link", "status", "is_active")},
        ),
        (
            "Admin panel access",
            {
                "fields": ("is_staff", "is_superuser", "groups", "user_permissions"),
                "description": "Panel access follows the ADMIN role automatically.",
            },
        ),
        (
            "Login security",
            {
                "fields": (
                    "must_change_password",
                    "failed_login_attempts",
                    "locked_until",
                    "password_changed_at",
                )
            },
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

    # ------------------------------------------------------------
    # الصلاحيات
    # ------------------------------------------------------------
    def get_readonly_fields(self, request, obj=None):
        fields = list(self.base_readonly_fields)
        if obj is not None:
            # 🔒 الإيقاف/التفعيل عبر الأفعال وحدها (توكنات + تدقيق)
            fields += ["status", "is_active"]
        if not request.user.is_superuser:
            fields += ["role", "is_superuser", "groups", "user_permissions"]
        return fields

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if request.user.is_superuser:
            return fieldsets
        # is_superuser لا يُعرض لغير superuser
        cleaned = []
        for name, options in fieldsets:
            fields = tuple(f for f in options.get("fields", ()) if f != "is_superuser")
            cleaned.append((name, {**options, "fields": fields}))
        return cleaned

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is not None and obj.pk != request.user.pk:
            return can_manage_account(request.user, obj)
        return True

    def has_delete_permission(self, request, obj=None):
        """
        🔒 superuser وحده، ولا حذف للذات. الحذف يمحو الحجوزات والدفعات
           تتاليًا (CASCADE) — الإيقاف هو المسار الطبيعي.
        """
        if not request.user.is_superuser:
            return False
        if obj is not None and obj.pk == request.user.pk:
            return False
        return super().has_delete_permission(request, obj)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("contractor_profile")

    def get_inlines(self, request, obj):
        if obj is None:
            return []
        if obj.role == ConfirmedRole.ADMIN:
            # حساب الإدارة لا يملك عقارات؛ الأجهزة الموثوقة تخصّه وحده
            return [SocialAccountInline, TrustedDeviceInline]
        return [PropertyInline, SocialAccountInline]

    # ------------------------------------------------------------
    # العرض
    # ------------------------------------------------------------
    @admin.display(description="Role", ordering="role")
    def role_display(self, obj):
        return obj.get_role_display()

    @admin.display(description="Status", ordering="status")
    def status_display(self, obj):
        return obj.get_status_display()

    @admin.display(description="Contractor profile")
    def contractor_profile_link(self, obj):
        profile = getattr(obj, "contractor_profile", None)
        return self.link(profile) if profile else EMPTY

    # ------------------------------------------------------------
    # الحفظ — تدقيق كل تعديل
    # ------------------------------------------------------------
    AUDITED_FIELDS = ("phone", "full_name", "email", "role", "is_contractor", "status", "is_superuser")

    def save_model(self, request, obj, form, change):
        changed = [f for f in form.changed_data if f in self.AUDITED_FIELDS]
        before = {}
        if change and changed:
            previous = type(obj).objects.filter(pk=obj.pk).values(*changed).first() or {}
            before = {k: str(v) for k, v in previous.items()}
        with transaction.atomic():
            super().save_model(request, obj, form, change)
            if change:
                if changed:
                    record(
                        request.user,
                        "user.update",
                        target=obj,
                        details={
                            "changed": {
                                f: {"from": before.get(f), "to": str(getattr(obj, f))}
                                for f in changed
                            }
                        },
                        request=request,
                    )
            else:
                record(
                    request.user,
                    "user.create",
                    target=obj,
                    details={"role": obj.role, "status": obj.status},
                    request=request,
                )

    def delete_model(self, request, obj):
        with transaction.atomic():
            record(request.user, "user.delete", target=obj, details={"phone": obj.phone}, request=request)
            super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        with transaction.atomic():
            for obj in queryset:
                record(request.user, "user.delete", target=obj, details={"phone": obj.phone}, request=request)
            super().delete_queryset(request, queryset)

    def user_change_password(self, request, id, form_url=""):
        """
        بعد إعادة تعيين كلمة سر من اللوحة: تُبطَل الأجهزة الموثوقة وتوكنات
        refresh، ويُسجَّل الفعل. كلمة سر يضعها شخص آخر مؤقتة
        (must_change_password) حتى يغيّرها صاحبها.
        """
        response = super().user_change_password(request, id, form_url)
        if request.method == "POST" and response.status_code == 302:
            user = self.get_object(request, id)
            if user is not None:
                with transaction.atomic():
                    user.password_changed_at = timezone.now()
                    user.must_change_password = user.pk != request.user.pk
                    user.save(update_fields=["password_changed_at", "must_change_password"])
                    devices = revoke_trusted_devices(user)
                    tokens = blacklist_refresh_tokens(user)
                    record(
                        request.user,
                        "user.password_reset",
                        target=user,
                        details={
                            "temporary": user.must_change_password,
                            "devices_revoked": devices,
                            "tokens_revoked": tokens,
                        },
                        request=request,
                    )
        return response

    # ------------------------------------------------------------
    # الأفعال
    # ------------------------------------------------------------
    def _each_manageable(self, request, queryset, verb):
        """يعيد الحسابات التي يحق للموظف إدارتها، ويبلّغ عن المرفوضة."""
        allowed, refused = [], []
        for user in queryset:
            if user.pk == request.user.pk or not can_manage_account(request.user, user):
                refused.append(user)
            else:
                allowed.append(user)
        if refused:
            self.message_user(
                request,
                f"Skipped {len(refused)} account(s) you cannot {verb}: your own account "
                f"or an ADMIN account (superuser only): "
                + ", ".join(u.phone for u in refused),
                level=messages.WARNING,
            )
        return allowed

    @admin.action(description="Activate selected accounts", permissions=["change"])
    def activate_accounts(self, request, queryset):
        done = 0
        for user in self._each_manageable(request, queryset, "activate"):
            with transaction.atomic():
                previous = user.status
                user.status = UserStatus.ACTIVE
                user.is_active = True
                user.save(update_fields=["status", "is_active", "updated_at"])
                record(
                    request.user,
                    "user.activate",
                    target=user,
                    details={"from": previous, "to": UserStatus.ACTIVE},
                    request=request,
                )
            done += 1
        if done:
            self.message_user(request, f"Activated {done} account(s).", level=messages.SUCCESS)

    @admin.action(description="Suspend selected accounts (signs them out)", permissions=["change"])
    def suspend_accounts(self, request, queryset):
        done = 0
        for user in self._each_manageable(request, queryset, "suspend"):
            with transaction.atomic():
                previous = user.status
                user.status = UserStatus.SUSPENDED
                user.save(update_fields=["status", "updated_at"])
                tokens = blacklist_refresh_tokens(user)
                devices = revoke_trusted_devices(user)
                record(
                    request.user,
                    "user.suspend",
                    target=user,
                    details={
                        "from": previous,
                        "to": UserStatus.SUSPENDED,
                        "tokens_revoked": tokens,
                        "devices_revoked": devices,
                    },
                    request=request,
                )
            done += 1
        if done:
            self.message_user(request, f"Suspended {done} account(s).", level=messages.SUCCESS)

    @admin.action(description="Revoke trusted admin devices", permissions=["change"])
    def revoke_devices(self, request, queryset):
        total = 0
        for user in queryset:
            if user.pk != request.user.pk and not can_manage_account(request.user, user):
                self.message_user(
                    request, f"Skipped {user.phone}: superuser only.", level=messages.WARNING
                )
                continue
            with transaction.atomic():
                revoked = revoke_trusted_devices(user)
                if revoked:
                    record(
                        request.user,
                        "user.revoke_devices",
                        target=user,
                        details={"devices_revoked": revoked},
                        request=request,
                    )
            total += revoked
        self.message_user(request, f"Revoked {total} trusted device(s).", level=messages.SUCCESS)

    @admin.action(description="Clear login lockout", permissions=["change"])
    def clear_login_lockout(self, request, queryset):
        done = 0
        for user in self._each_manageable(request, queryset, "unlock"):
            if not user.failed_login_attempts and not user.locked_until:
                continue
            with transaction.atomic():
                user.failed_login_attempts = 0
                user.locked_until = None
                user.save(update_fields=["failed_login_attempts", "locked_until", "updated_at"])
                record(request.user, "user.clear_lockout", target=user, request=request)
            done += 1
        self.message_user(request, f"Cleared lockout on {done} account(s).", level=messages.SUCCESS)


# ============================================================
# OTP
# ============================================================
@admin.register(OTPVerification)
class OTPVerificationAdmin(BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    """
    عرض للقراءة فقط — سجلات OTP أدلة أمنية ولا تُحرَّر ولا تُحذف يدويًا.
    🔒 لا يوجد code_hash في list_display ولا بحث به ولا في الصفحة.
    """

    list_display = (
        "phone",
        "purpose",
        "status",
        "attempts_count",
        "max_attempts",
        "requested_ip",
        "expires_at",
        "created_at",
    )
    list_filter = ("status", "purpose", "created_at")
    search_fields = ("phone", "requested_ip")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    fieldsets = (
        (None, {"fields": ("id", "phone", "purpose", "status")}),
        ("Attempts", {"fields": ("attempts_count", "max_attempts", "requested_ip")}),
        ("Timing", {"fields": ("expires_at", "created_at")}),
    )
    readonly_fields = (
        "id", "phone", "purpose", "status", "attempts_count",
        "max_attempts", "requested_ip", "expires_at", "created_at",
    )
    exclude = ("code_hash",)


# ============================================================
# Social accounts
# ============================================================
@admin.register(SocialAccount)
class SocialAccountAdmin(BackOfficeMixin, admin.ModelAdmin):
    """
    ربط الهويات الخارجية — للعرض والتدقيق.

    🔒 للقراءة فقط: تغيير user أو provider_user_id كان يسلّم دخول شخص
       لحساب آخر. فكّ الربط (الحذف) لـsuperuser وحده، ويُسجَّل.
    """

    list_display = ("user_link", "provider", "provider_user_id", "email", "linked_at")
    list_filter = ("provider", "linked_at")
    search_fields = ("provider_user_id", "email", "user__phone", "user__email")
    date_hierarchy = "linked_at"
    ordering = ("-linked_at",)
    list_select_related = ("user",)
    fieldsets = (
        (None, {"fields": ("id", "user_link", "provider", "provider_user_id", "email", "linked_at")}),
        ("Provider payload", {"classes": ("collapse",), "fields": ("raw_profile_display",)}),
    )
    readonly_fields = (
        "id", "user_link", "provider", "provider_user_id", "email", "linked_at", "raw_profile_display",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_delete_permission(request, obj)

    @admin.display(description="User", ordering="user__phone")
    def user_link(self, obj):
        return self.link(obj.user)

    @admin.display(description="Raw profile")
    def raw_profile_display(self, obj):
        return pretty_json(obj.raw_profile)

    def delete_model(self, request, obj):
        with transaction.atomic():
            record(request.user, "social_account.unlink", target=obj.user,
                   details={"provider": obj.provider}, request=request)
            super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        with transaction.atomic():
            for obj in queryset.select_related("user"):
                record(request.user, "social_account.unlink", target=obj.user,
                       details={"provider": obj.provider}, request=request)
            super().delete_queryset(request, queryset)


# ============================================================
# توكنات JWT — إعادة تسجيل آمنة لما يسجّله ninja_jwt
# ============================================================
# 🔒 تسجيل ninja_jwt الافتراضي يعرض حقل `token` الخام في صفحة التفاصيل —
#    أي توكن refresh سارٍ مقروء لكل موظف لديه صلاحية العرض. نعيد تسجيلهما
#    بلا ذلك الحقل، وللـsuperuser وحده.
class _SuperuserOnlyMixin:
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser


class OutstandingTokenAdmin(_SuperuserOnlyMixin, BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("jti", "user_link", "created_at", "expires_at", "is_blacklisted")
    search_fields = ("jti", "user__phone", "user__email")
    list_filter = ("created_at", "expires_at")
    ordering = ("-created_at",)
    list_select_related = ("user", "blacklistedtoken")
    fields = ("jti", "user_link", "created_at", "expires_at", "is_blacklisted")
    readonly_fields = fields
    actions = None

    @admin.display(description="User")
    def user_link(self, obj):
        return self.link(obj.user)

    @admin.display(description="Revoked", boolean=True)
    def is_blacklisted(self, obj):
        return hasattr(obj, "blacklistedtoken")


class BlacklistedTokenAdmin(_SuperuserOnlyMixin, BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("jti", "user_link", "blacklisted_at")
    search_fields = ("token__jti", "token__user__phone")
    ordering = ("-blacklisted_at",)
    list_select_related = ("token__user",)
    fields = ("jti", "user_link", "blacklisted_at")
    readonly_fields = fields
    actions = None

    @admin.display(description="JTI")
    def jti(self, obj):
        return obj.token.jti

    @admin.display(description="User")
    def user_link(self, obj):
        return self.link(obj.token.user)


for _model, _admin_class in (
    (OutstandingToken, OutstandingTokenAdmin),
    (BlacklistedToken, BlacklistedTokenAdmin),
):
    if admin.site.is_registered(_model):
        admin.site.unregister(_model)
    admin.site.register(_model, _admin_class)
