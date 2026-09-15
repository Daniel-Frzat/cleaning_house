"""
Job Execution Models — Jobs Domain (Change Set §20، §36.3؛ Infra §7)

تنفيذ العمل بعد تأكيد الحجز: بدء المهمة، صور قبل/بعد، ثم تأكيد العميل.

⚠️ خارج النطاق عمدًا:
   - الإلغاء: لا حالة CANCELLED هنا. سياسة الإلغاء بند مفتوح صراحةً
     (§18 — Important #12)، وإضافة الحالة استباقًا تفترض قرارًا لم يُتخذ.
   - تخزين الملفات الفعلي: storage_key مرجع مبهم من الـadapter، ولا
     يُفسَّر ولا يُحلَّل هنا (Infra §7). لا S3 ولا boto3 ولا كتابة قرص.

📌 دورة الحياة (§36.3):
   ASSIGNED → IN_PROGRESS → AWAITING_CUSTOMER_CONFIRMATION → COMPLETED
   المقاول يبدأ ثم يعلن الإنجاز، والعميل هو من يؤكّده.
"""

import uuid

from django.db import models


class JobStatus(models.TextChoices):
    """
    حالة تنفيذ المهمة (§36.3).

    ⚠️ أربع حالات — لا CANCELLED ولا غيرها. راجع docstring الملف.

    ASSIGNED                       : قَبِل المقاول، ولم يبدأ التنفيذ بعد.
    IN_PROGRESS                    : المقاول أعلن بدء العمل.
    AWAITING_CUSTOMER_CONFIRMATION : المقاول أعلن الإنجاز، بانتظار العميل.
    COMPLETED                      : العميل أكّد الإنجاز.

    📌 ASSIGNED أُضيفت لأن القبول والبدء كانا لحظة واحدة، فلم يكن للعميل
       أي طريقة ليعرف أن مقاولًا قَبِل لكنه لم يصل بعد. الفصل يعطي أيضًا
       تطبيق المقاول لحظة "أنا في الطريق / أبدأ الآن" التي لم تكن موجودة.
    """

    ASSIGNED = "ASSIGNED", "Assigned"
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    AWAITING_CUSTOMER_CONFIRMATION = (
        "AWAITING_CUSTOMER_CONFIRMATION",
        "Awaiting customer confirmation",
    )
    COMPLETED = "COMPLETED", "Completed"


class PhotoType(models.TextChoices):
    """صورة قبل العمل أو بعده."""

    BEFORE = "BEFORE", "Before"
    AFTER = "AFTER", "After"


class Job(models.Model):
    """
    مهمة تنفيذ مرتبطة بحجز واحد.

    ⚠️ تُنشأ فقط عند وصول الحجز إلى CONFIRMED — لا عند إنشائه (PENDING).
       الإنشاء آلي عند قبول المقاول للعرض، بجانب الشحن المباشر.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # PROTECT: الحجز المرتبط بمهمة لا يُحذف من تحتها — سجل تنفيذي.
    booking = models.OneToOneField(
        "bookings.Booking",
        on_delete=models.PROTECT,
        related_name="job",
    )

    status = models.CharField(
        max_length=32,
        choices=JobStatus.choices,
        default=JobStatus.ASSIGNED,
    )

    # يُملأ حين يعلن المقاول بدء العمل (ASSIGNED → IN_PROGRESS)
    started_at = models.DateTimeField(null=True, blank=True)

    # يُملأ حين يعلن المقاول الإنجاز
    marked_done_at = models.DateTimeField(null=True, blank=True)

    # يُملأ حين يؤكّد العميل (§36.3) — العميل وحده من يُكمل المهمة
    confirmed_at = models.DateTimeField(null=True, blank=True)

    # عمليًا: لحظة بدء المهمة (أي لحظة تأكيد الحجز)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Job"
        verbose_name_plural = "Jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Job {self.id} ({self.status})"

    def accepts_photos(self):
        """
        هل تقبل المهمة رفع صور الآن؟

        الرفع مسموح أثناء التنفيذ وحده: بعد إعلان الإنجاز أو اكتماله
        تصبح الأدلة مجمَّدة، وإضافة صورة بعدها تغيّر سجلًا استُند إليه
        في التأكيد.

        ⚠️ ASSIGNED لا تقبل صورًا: المقاول لم يصل بعد، وصورة "قبل" تُلتقط
           قبل الوصول لا تصف الموقع. البدء فعل صريح يسبق أي دليل.
        """
        return self.status == JobStatus.IN_PROGRESS


class JobPhoto(models.Model):
    """
    صورة قبل/بعد مرتبطة بمهمة.

    ⚠️ storage_key مرجع مبهم يعيده الـadapter: يُخزَّن كما هو ولا يُفسَّر
       (شكله يختلف بين المزوّدين). ولا يُكشف للعميل — يُستبدل بـsigned URL.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name="photos",
    )

    photo_type = models.CharField(max_length=8, choices=PhotoType.choices)

    # 🔒 مبهم: لا يُفسَّر، ولا يُكشف للعميل
    storage_key = models.TextField()

    # ⚠️ يجب أن يكون مستخدم المقاول المُسنَد للحجز — يُفرض في طبقة الخدمة
    #    (قاعدة البيانات لا تستطيع ربط هذا بـbooking.assigned_contractor).
    uploaded_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="uploaded_job_photos",
    )

    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Job photo"
        verbose_name_plural = "Job photos"
        ordering = ["uploaded_at"]
        indexes = [
            models.Index(fields=["job", "photo_type"]),
        ]

    def __str__(self):
        return f"{self.photo_type} photo for job {self.job_id}"
