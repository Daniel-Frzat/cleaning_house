"""
Django Admin — Contractor Profile Domain.

📌 مراجعة الوثائق (اعتماد/رفض) تتم بأفعال تستدعي طبقة الخدمة
   (services/verification.py) — لا بتحرير الحقول: الخدمة تفرض الدور،
   ونهائية القرار، وسبب الرفض، ومنع مراجعة الذات، ورفض اعتماد تأمين
   منتهٍ. كل قرار يُسجَّل في سجل التدقيق داخل معاملة القرار نفسها.

🔒 ContractorCurrentLocation غير مسجَّل عمدًا — نفس قرار خصوصية
   JobLocation (راجع jobs/admin.py): الموقع الحيّ لا يُتصفَّح من اللوحة.
"""

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.template.response import TemplateResponse

from apps.audit.admin import EMPTY, BackOfficeMixin, ReadOnlyAdminMixin
from apps.audit.services.audit import record

from .models import BusinessRegistration, ContractorProfile, InsuranceDocument, VerificationStatus
from .services import verification as verification_service


# ============================================================
# Inlines — تاريخ الوثائق على ملف المقاول
# ============================================================
class _DocumentHistoryInline(BackOfficeMixin, ReadOnlyAdminMixin, admin.TabularInline):
    extra = 0
    show_change_link = True
    ordering = ("-created_at",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("reviewed_by")


class BusinessRegistrationInline(_DocumentHistoryInline):
    model = BusinessRegistration
    fields = ("business_name", "abn", "status", "reviewed_by", "reviewed_at", "rejection_reason", "created_at")
    readonly_fields = fields
    verbose_name_plural = "Business registrations (history)"


class InsuranceDocumentInline(_DocumentHistoryInline):
    model = InsuranceDocument
    fields = (
        "document_reference",
        "expiry_date",
        "status",
        "reviewed_by",
        "reviewed_at",
        "rejection_reason",
        "created_at",
    )
    readonly_fields = fields
    verbose_name_plural = "Insurance documents (history)"


# ============================================================
# ContractorProfile
# ============================================================
@admin.register(ContractorProfile)
class ContractorProfileAdmin(BackOfficeMixin, admin.ModelAdmin):
    list_display = (
        "display_name",
        "user_link",
        "phone",
        "suburb",
        "state",
        "availability_status",
        "account_status",
        "pending_documents",
        "created_at",
    )
    list_filter = ("availability_status", "state", "user__status", "created_at")
    search_fields = ("business_name", "suburb", "postcode", "user__phone", "user__email", "user__full_name")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
    inlines = [BusinessRegistrationInline, InsuranceDocumentInline]
    # 🔒 التوفّر قرار المقاول نفسه (الـAPI يمنع الإدارة منه)، ونقل الملف
    #    لمستخدم آخر ينقل تاريخ عروضه ووثائقه معه.
    readonly_fields = (
        "id",
        "user_link",
        "availability_status",
        "country",
        "eligibility",
        "contractor_status",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        ("Contractor", {"fields": ("id", "user_link", "business_name")}),
        ("Business address", {"fields": ("street_address", "suburb", "state", "postcode", "country")}),
        (
            "Dispatch coordinates",
            {
                "fields": ("latitude", "longitude"),
                "description": "Fixed business location. Live location is never shown here.",
            },
        ),
        ("Status", {"fields": ("availability_status", "contractor_status", "eligibility")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def has_add_permission(self, request):
        """الملف يُنشئه المقاول عبر الـAPI."""
        return False

    def has_delete_permission(self, request, obj=None):
        """
        🔒 الحذف يمحو العروض والوثائق (CASCADE) ويترك مهامه بلا منفّذ
           (SET_NULL) فلا تبدأ ولا يُدفع عنها. الإيقاف يتم عبر حالة الحساب.
        """
        return False

    def get_queryset(self, request):
        pending_regs = Q(business_registrations__status=VerificationStatus.PENDING)
        pending_ins = Q(insurance_documents__status=VerificationStatus.PENDING)
        return (
            super()
            .get_queryset(request)
            .select_related("user")
            .annotate(
                _pending_regs=Count("business_registrations", filter=pending_regs, distinct=True),
                _pending_ins=Count("insurance_documents", filter=pending_ins, distinct=True),
            )
        )

    @admin.display(description="Contractor", ordering="business_name")
    def display_name(self, obj):
        return str(obj)

    @admin.display(description="User", ordering="user__full_name")
    def user_link(self, obj):
        return self.link(obj.user, obj.user.full_name or obj.user.phone)

    @admin.display(description="Phone", ordering="user__phone")
    def phone(self, obj):
        return obj.user.phone

    @admin.display(description="Account", ordering="user__status")
    def account_status(self, obj):
        return obj.user.get_status_display()

    @admin.display(description="Pending documents")
    def pending_documents(self, obj):
        return (getattr(obj, "_pending_regs", 0) or 0) + (getattr(obj, "_pending_ins", 0) or 0)

    @admin.display(description="Eligible for work", boolean=True)
    def eligibility(self, obj):
        # محسوبة لحظيًا من طبقة الخدمة — ليست حقلًا مخزَّنًا
        return verification_service.is_contractor_eligible(obj)

    @admin.display(description="Contractor status")
    def contractor_status(self, obj):
        status = verification_service.get_contractor_status(obj.user)
        return verification_service.ContractorStatus(status).label


# ============================================================
# مستندات التحقق — مراجعة عبر طبقة الخدمة
# ============================================================
class RejectionReasonForm(forms.Form):
    reason = forms.CharField(
        label="Rejection reason",
        widget=forms.Textarea(attrs={"rows": 4, "cols": 80}),
        help_text="Shown to the contractor so they know what to correct.",
    )

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("A rejection reason is required.")
        return reason


class ReviewableDocumentAdmin(BackOfficeMixin, admin.ModelAdmin):
    """
    أساس مشترك لعرض مستندات التحقق.

    ⚠️ المراجعة تمر عبر طبقة الخدمة بأفعال "Approve" و"Reject" — لا
       بتحرير الحقول: Django Admin كان يتجاوز قواعد "السبب إلزامي عند
       الرفض" و"القرار نهائي" إن حُرِّرت الحقول مباشرة.

    🔒 كل الحقول للقراءة فقط، ولا إضافة ولا تعديل ولا حذف: تمديد تاريخ
       انتهاء وثيقة معتمدة كان يعيد الأهلية بلا مراجعة، وحذف رفض حديث كان
       يُحيي اعتمادًا قديمًا. المستند شهادة على ما قُدِّم وما قُرِّر.
    """

    # يضبطها كل صنف فرعي
    review_function = None
    audit_domain = None

    list_filter = ("status", "created_at", "reviewed_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("contractor", "contractor__user", "reviewed_by")
    actions = ["approve_selected", "reject_selected"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_review_permission(self, request):
        """المراجعة للأدمن (الدور ADMIN) الذي يرى هذه الوثائق — الخدمة تعيد الفحص."""
        return request.user.has_admin_access() and self.has_view_permission(request)

    @admin.display(description="Contractor", ordering="contractor__business_name")
    def contractor_link(self, obj):
        return self.link(obj.contractor)

    @admin.display(description="Status", ordering="status")
    def status_display(self, obj):
        return obj.get_status_display()

    @admin.display(description="Reviewed by")
    def reviewer_link(self, obj):
        return self.link(obj.reviewed_by) if obj.reviewed_by else EMPTY

    # ------------------------------------------------------------
    # المراجعة
    # ------------------------------------------------------------
    def _review(self, request, queryset, status, reason=None):
        verb = "approve" if status == VerificationStatus.VERIFIED else "reject"
        review = type(self).review_function
        done, failed = 0, []
        for document in queryset:
            try:
                with transaction.atomic():
                    reviewed = review(request.user, document.pk, status, reason)
                    record(
                        request.user,
                        f"{self.audit_domain}.{verb}",
                        target=reviewed,
                        details={"status": status, "reason": reviewed.rejection_reason or ""},
                        request=request,
                    )
                done += 1
            except (
                verification_service.VerificationError,
                verification_service.AdminRoleRequiredError,
                ValidationError,
            ) as exc:
                failed.append(f"{document}: {'; '.join(getattr(exc, 'messages', [str(exc)]))}")
        if done:
            self.message_user(
                request,
                f"{'Approved' if verb == 'approve' else 'Rejected'} {done} document(s).",
                level=messages.SUCCESS,
            )
        for message in failed:
            self.message_user(request, message, level=messages.ERROR)

    @admin.action(description="Approve selected documents", permissions=["review"])
    def approve_selected(self, request, queryset):
        self._review(request, queryset, VerificationStatus.VERIFIED)

    @admin.action(description="Reject selected documents (asks for a reason)", permissions=["review"])
    def reject_selected(self, request, queryset):
        """
        صفحة وسيطة تطلب السبب. الخدمة ترفض الرفض بلا سبب مهما كان — هذه
        الصفحة تجعل ذلك واضحًا قبل الإرسال لا بعده.
        """
        form = None
        if request.POST.get("confirm_reject"):
            form = RejectionReasonForm(request.POST)
            if form.is_valid():
                self._review(request, queryset, VerificationStatus.REJECTED, form.cleaned_data["reason"])
                return None
        if form is None:
            form = RejectionReasonForm()
        opts = self.model._meta
        request.current_app = self.admin_site.name
        context = {
            **self.admin_site.each_context(request),
            "title": f"Reject {opts.verbose_name_plural}",
            "opts": opts,
            "form": form,
            "queryset": queryset,
            "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
            "action": "reject_selected",
            "select_across": request.POST.get("select_across", "0"),
        }
        return TemplateResponse(request, "admin/contractors/reject_reason.html", context)


@admin.register(BusinessRegistration)
class BusinessRegistrationAdmin(ReviewableDocumentAdmin):
    review_function = staticmethod(verification_service.review_business_registration)
    audit_domain = "business_registration"

    list_display = ("business_name", "abn", "contractor_link", "status_display", "reviewer_link", "reviewed_at", "created_at")
    search_fields = ("business_name", "abn", "contractor__business_name", "contractor__user__phone")
    fieldsets = (
        ("Submission", {"fields": ("id", "contractor_link", "business_name", "abn", "created_at")}),
        ("Review", {"fields": ("status_display", "reviewer_link", "reviewed_at", "rejection_reason", "updated_at")}),
    )
    readonly_fields = (
        "id", "contractor_link", "business_name", "abn", "created_at",
        "status_display", "reviewer_link", "reviewed_at", "rejection_reason", "updated_at",
    )


@admin.register(InsuranceDocument)
class InsuranceDocumentAdmin(ReviewableDocumentAdmin):
    review_function = staticmethod(verification_service.review_insurance_document)
    audit_domain = "insurance_document"

    list_display = (
        "document_reference",
        "contractor_link",
        "expiry_date",
        "is_expired_display",
        "status_display",
        "reviewer_link",
        "reviewed_at",
        "created_at",
    )
    list_filter = ("status", "expiry_date", "created_at", "reviewed_at")
    search_fields = ("document_reference", "contractor__business_name", "contractor__user__phone")
    fieldsets = (
        (
            "Submission",
            {"fields": ("id", "contractor_link", "document_reference", "expiry_date", "is_expired_display", "created_at")},
        ),
        ("Review", {"fields": ("status_display", "reviewer_link", "reviewed_at", "rejection_reason", "updated_at")}),
    )
    readonly_fields = (
        "id", "contractor_link", "document_reference", "expiry_date", "is_expired_display", "created_at",
        "status_display", "reviewer_link", "reviewed_at", "rejection_reason", "updated_at",
    )

    @admin.display(description="Expired", boolean=True)
    def is_expired_display(self, obj):
        return obj.is_expired
