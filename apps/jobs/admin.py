"""Django Admin — Job Execution Domain."""

from django.contrib import admin
from django.db.models import Count

from apps.audit.admin import EMPTY, BackOfficeMixin, ReadOnlyAdminMixin, short_id

from .models import Job, JobPhoto


def _booking_label(booking):
    return booking.public_reference or short_id(booking.pk)


class JobPhotoInline(BackOfficeMixin, ReadOnlyAdminMixin, admin.TabularInline):
    """الصور تُعرض داخل المهمة — لا معنى لها منفصلة."""

    model = JobPhoto
    extra = 0
    fields = ("photo_type", "storage_key", "uploader_link", "uploaded_at")
    readonly_fields = fields
    show_change_link = True
    ordering = ("uploaded_at",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("uploaded_by")

    @admin.display(description="Uploaded by")
    def uploader_link(self, obj):
        return self.link(obj.uploaded_by)


@admin.register(Job)
class JobAdmin(BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    """
    ⚠️ للقراءة فقط: المهام تُنشأ آليًا عند تأكيد الحجز، وانتقالات الحالة
       تمر عبر طبقة الخدمة. التحرير اليدوي يلتف على قواعد §36.3.

    🔒 لا حذف: الحذف يمحو صور الإثبات التي استند إليها تأكيد العميل.
    """

    list_display = (
        "short",
        "booking_link",
        "customer_link",
        "contractor_link",
        "status",
        "photo_count",
        "started_at",
        "marked_done_at",
        "confirmed_at",
        "created_at",
    )
    list_filter = ("status", "created_at", "confirmed_at")
    search_fields = (
        "booking__public_reference",
        "booking__customer__phone",
        "booking__assigned_contractor__business_name",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("booking", "booking__customer", "booking__assigned_contractor")
    inlines = [JobPhotoInline]
    fieldsets = (
        ("Job", {"fields": ("id", "booking_link", "customer_link", "contractor_link", "status")}),
        ("Timeline", {"fields": ("created_at", "started_at", "marked_done_at", "confirmed_at", "updated_at")}),
    )
    readonly_fields = (
        "id",
        "booking_link",
        "customer_link",
        "contractor_link",
        "status",
        "created_at",
        "started_at",
        "marked_done_at",
        "confirmed_at",
        "updated_at",
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("booking", "booking__customer", "booking__assigned_contractor")
            .annotate(_photo_count=Count("photos"))
        )

    @admin.display(description="Job")
    def short(self, obj):
        return short_id(obj.pk)

    @admin.display(description="Booking", ordering="booking__public_reference")
    def booking_link(self, obj):
        return self.link(obj.booking, _booking_label(obj.booking))

    @admin.display(description="Customer", ordering="booking__customer__phone")
    def customer_link(self, obj):
        customer = obj.booking.customer
        return self.link(customer, customer.full_name or customer.phone)

    @admin.display(description="Contractor")
    def contractor_link(self, obj):
        contractor = obj.booking.assigned_contractor
        return self.link(contractor) if contractor else EMPTY

    @admin.display(description="Photos", ordering="_photo_count")
    def photo_count(self, obj):
        return getattr(obj, "_photo_count", None)


@admin.register(JobPhoto)
class JobPhotoAdmin(BackOfficeMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    """🔒 صور الإثبات لا تُحرَّر ولا تُحذف — تأكيد العميل استند إليها."""

    list_display = ("short", "job_link", "booking_ref", "photo_type", "uploader_link", "uploaded_at")
    list_filter = ("photo_type", "uploaded_at")
    search_fields = ("job__booking__public_reference", "uploaded_by__phone", "storage_key")
    date_hierarchy = "uploaded_at"
    ordering = ("-uploaded_at",)
    list_select_related = ("job", "job__booking", "uploaded_by")
    fields = ("id", "job_link", "booking_ref", "photo_type", "storage_key", "uploader_link", "uploaded_at")
    readonly_fields = fields

    @admin.display(description="Photo")
    def short(self, obj):
        return short_id(obj.pk)

    @admin.display(description="Job")
    def job_link(self, obj):
        return self.link(obj.job, f"Job {short_id(obj.job_id)} ({obj.job.get_status_display()})")

    @admin.display(description="Booking", ordering="job__booking__public_reference")
    def booking_ref(self, obj):
        return self.link(obj.job.booking, _booking_label(obj.job.booking))

    @admin.display(description="Uploaded by")
    def uploader_link(self, obj):
        return self.link(obj.uploaded_by)


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
