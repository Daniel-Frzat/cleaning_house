"""Django Admin — Job Execution Domain."""

from django.contrib import admin

from .models import Job, JobPhoto


class JobPhotoInline(admin.TabularInline):
    """الصور تُعرض داخل المهمة — لا معنى لها منفصلة."""

    model = JobPhoto
    extra = 0
    readonly_fields = ("id", "photo_type", "storage_key", "uploaded_by", "uploaded_at")
    raw_id_fields = ("uploaded_by",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    """
    ⚠️ للقراءة فقط: المهام تُنشأ آليًا عند تأكيد الحجز، وانتقالات الحالة
       تمر عبر طبقة الخدمة. التحرير اليدوي يلتف على قواعد §36.3.
    """

    list_display = ("id", "booking", "status", "marked_done_at", "confirmed_at")
    list_filter = ("status",)
    ordering = ("-created_at",)
    readonly_fields = (
        "id",
        "booking",
        "status",
        "marked_done_at",
        "confirmed_at",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("booking",)
    inlines = [JobPhotoInline]

    def has_add_permission(self, request):
        return False


@admin.register(JobPhoto)
class JobPhotoAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "photo_type", "uploaded_by", "uploaded_at")
    list_filter = ("photo_type",)
    ordering = ("-uploaded_at",)
    readonly_fields = (
        "id",
        "job",
        "photo_type",
        "storage_key",
        "uploaded_by",
        "uploaded_at",
    )
    raw_id_fields = ("job", "uploaded_by")

    def has_add_permission(self, request):
        return False
