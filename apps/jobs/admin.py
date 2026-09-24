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

    def has_delete_permission(self, request, obj=None):
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
        "started_at",
        "marked_done_at",
        "confirmed_at",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("booking",)
    inlines = [JobPhotoInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        """🔒 الحذف يمحو صور الإثبات التي استند إليها تأكيد العميل."""
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

    def has_delete_permission(self, request, obj=None):
        """🔒 صور الإثبات لا تُحذف — تأكيد العميل استند إليها."""
        return False


# ============================================================
# JobLocation — غير مسجَّل في لوحة الإدارة عمدًا
# ============================================================
# 🔒 قرار خصوصية لا سهو: الموقع الحيّ يُعرض للعميل خلال نافذة ASSIGNED
#    وحدها، ثم يتوقف. تسجيله هنا كان سيفتح لكل موظف إداري تصفّح مواقع
#    المقاولين خارج تلك النافذة — وهي بالضبط المراقبة التي وُجدت
#    النافذة لمنعها.
#
# 📌 ولا قيمة تشغيلية تُذكر: النقطة عمرها المفيد تسعون ثانية، وصف واحد
#    يُكتب فوقه بلا تاريخ. أي تحقيق في نزاع يستند إلى صور before/after
#    لا إلى نقطة متحركة.
#
# ⚠️ لو احتاجت الإدارة يومًا رؤية الموقع، فالمسار الصحيح
#    GET /api/bookings/{id}/tracking — يحترم النافذة نفسها.
