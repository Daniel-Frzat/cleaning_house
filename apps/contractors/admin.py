"""Django Admin — Contractor Profile Domain."""

from django.contrib import admin

from .models import BusinessRegistration, ContractorProfile, InsuranceDocument


@admin.register(ContractorProfile)
class ContractorProfileAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "user",
        "suburb",
        "state",
        "availability_status",
        "created_at",
    )
    list_filter = ("availability_status", "state")
    search_fields = ("business_name", "suburb", "user__phone", "user__email")
    ordering = ("-created_at",)
    readonly_fields = ("id", "country", "created_at", "updated_at")
    raw_id_fields = ("user",)


class ReviewableDocumentAdmin(admin.ModelAdmin):
    """
    أساس مشترك لعرض مستندات التحقق.

    ⚠️ المراجعة الفعلية تمر عبر طبقة الخدمة (API)، لا من هنا: Django Admin
       يتجاوز قواعد "السبب إلزامي عند الرفض" إن حُرِّرت الحقول مباشرة.
       لذلك حقول المراجعة للقراءة فقط هنا، والعرض للاطلاع والتدقيق.
    """

    list_filter = ("status",)
    ordering = ("-created_at",)
    readonly_fields = (
        "id",
        "reviewed_by",
        "reviewed_at",
        "rejection_reason",
        "status",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("contractor",)


@admin.register(BusinessRegistration)
class BusinessRegistrationAdmin(ReviewableDocumentAdmin):
    list_display = ("business_name", "abn", "contractor", "status", "created_at")
    search_fields = ("business_name", "abn", "contractor__business_name")


@admin.register(InsuranceDocument)
class InsuranceDocumentAdmin(ReviewableDocumentAdmin):
    list_display = (
        "document_reference",
        "contractor",
        "expiry_date",
        "status",
        "created_at",
    )
    list_filter = ("status", "expiry_date")
    search_fields = ("document_reference", "contractor__business_name")
