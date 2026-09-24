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
    # 🔒 التوفّر قرار المقاول نفسه (الـAPI يمنع الإدارة منه)، ونقل الملف
    #    لمستخدم آخر ينقل تاريخ عروضه ووثائقه معه.
    readonly_fields = ("id", "user", "availability_status", "country", "created_at", "updated_at")
    raw_id_fields = ("user",)

    def has_add_permission(self, request):
        """الملف يُنشئه المقاول عبر الـAPI."""
        return False

    def has_delete_permission(self, request, obj=None):
        """
        🔒 الحذف يمحو العروض والوثائق (CASCADE) ويترك مهامه بلا منفّذ
           (SET_NULL) فلا تبدأ ولا يُدفع عنها. الإيقاف يتم عبر حالة الحساب.
        """
        return False


class ReviewableDocumentAdmin(admin.ModelAdmin):
    """
    أساس مشترك لعرض مستندات التحقق.

    ⚠️ المراجعة الفعلية تمر عبر طبقة الخدمة (API)، لا من هنا: Django Admin
       يتجاوز قواعد "السبب إلزامي عند الرفض" إن حُرِّرت الحقول مباشرة.

    🔒 كل الحقول للقراءة فقط، ولا إضافة ولا حذف: تمديد تاريخ انتهاء وثيقة
       معتمدة كان يعيد الأهلية بلا مراجعة، وحذف رفض حديث كان يُحيي اعتمادًا
       قديمًا. المستند شهادة على ما قُدِّم وما قُرِّر.
    """

    list_filter = ("status",)
    ordering = ("-created_at",)
    raw_id_fields = ("contractor",)

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
