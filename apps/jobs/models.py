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

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


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

    def is_tracking_window_open(self):
        """
        هل نافذة تتبع الموقع مفتوحة الآن؟

        ASSIGNED وحدها: المقاول قَبِل ولم يصل بعد، وهي المدة التي يعني
        فيها سؤال "أين العامل؟" شيئًا.

        🔒 مضادّ تمامًا لـaccepts_photos: التتبع قبل البدء، والصور بعده.
           لا لحظة تقبل الاثنين — فور إعلان البدء يصبح العامل داخل
           المنزل، ومتابعة موقعه هناك مراقبة لا خدمة.

        ⚠️ الإغلاق ليس حذفًا: آخر نقطة تبقى مخزَّنة، لكنها تتوقف عن
           الظهور للعميل وعن قبول تحديثات جديدة.
        """
        return self.status == JobStatus.ASSIGNED


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


# ============================================================
# موقع المقاول أثناء التوجّه (En-route tracking)
# ============================================================
# 📌 سبب الوجود: ContractorProfile.latitude/longitude هما **عنوان العمل
#    الثابت** الذي يرتّب به Dispatch المرشَّحين بالمسافة. الكتابة فوقهما
#    بموقع متحرّك تُفسد الإسناد: مقاول يقود عبر المدينة كان سيغيّر صامتًا
#    أي الحجوزات هو أقربها إليها. فالموقع الحيّ كيان منفصل تمامًا.
#
# 🔒 نافذة العرض ضيّقة عمدًا: من ASSIGNED حتى IN_PROGRESS. التتبع يجيب
#    سؤال "أين العامل؟" وهو سؤال ما قبل الوصول وحده. بعد إعلان البدء
#    يكون العامل داخل المنزل، وتتبعه هناك مراقبة لا خدمة.
#
# ⚠️ آخر موقع فقط — لا مسار: صف واحد لكل مهمة يُكتب فوقه. المسار الكامل
#    أرشيف تحركات لا حاجة له اليوم ويحمل مسؤولية خصوصية، ويحتاج سياسة
#    حذف غير موجودة.

# بعد هذه المدة تُعدّ النقطة متقادمة فتُعلَّم is_stale. 90 ثانية تحتمل
# تحديثًا كل 30 ثانية مع تعثّر شبكة مرتين قبل أن نعلن التقادم.
# ⚠️ عتبة عرض لا قاعدة عمل: لا تُخفي الموقع ولا توقف التتبع، بل تخبر
#    الواجهة أن تعرض "آخر تحديث منذ ..." بدل نقطة توحي بأنها حيّة.
LOCATION_STALE_AFTER_SECONDS = 90


class JobLocation(models.Model):
    """
    آخر موقع معلوم للمقاول المُسنَد، أثناء توجّهه إلى العقار.

    🔒 يكتبه المقاول المُسنَد وحده، ويقرأه العميل المالك (والإدارة).
       يُفرض في طبقة الخدمة — قاعدة البيانات لا تستطيع ربط هذا
       بـbooking.assigned_contractor.

    ⚠️ ليس دليل حضور ولا سجل عمل: نقطة واحدة تُستبدل مع كل تحديث، فلا
       تصلح للاستشهاد بها في نزاع. دليل الإنجاز هو صور before/after.
    """

    # 📌 OneToOne لا ForeignKey: صف واحد لكل مهمة بحكم البنية لا بحكم
    #    انضباط التطبيق — قاعدة البيانات نفسها تمنع تراكم نقاط المسار.
    job = models.OneToOneField(
        Job,
        on_delete=models.CASCADE,
        related_name="location",
    )

    # حدود وتدقيق مطابقان لإحداثيات العقار وملف المقاول (9,6).
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)

    # 📌 دقة القياس بالأمتار كما يبلّغها الجهاز. تُعرض للعميل كدائرة عدم
    #    يقين حول النقطة: قراءة بدقة 500 متر ليست كذبة، لكن رسمها كنقطة
    #    حادّة على الخريطة كذب.
    # ⚠️ اختيارية: أجهزة ومتصفحات لا تبلّغها، ورفض التحديث لغيابها كان
    #    سيُسقط تتبعًا صالحًا.
    accuracy_m = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Reported GPS accuracy radius in metres, if the device supplied it.",
    )

    # 📌 لحظة التقاط الجهاز للنقطة، كما يبلّغها هو.
    # ⚠️ لا يُوثق به وحده: ساعة الجهاز قد تكون مضبوطة خطأً أو مزوَّرة،
    #    فنقطة قديمة قد تبدو حديثة. يُعرض للاطلاع، وحساب التقادم
    #    (is_stale) يقوم على received_at وحده.
    recorded_at = models.DateTimeField(
        help_text="When the device captured the fix (client clock — not trusted)."
    )

    # 🔒 ختم الخادم لحظة الاستلام — المرجع الوحيد في حساب التقادم.
    #    auto_now: كل كتابة تستبدل النقطة السابقة ووقتها معًا.
    received_at = models.DateTimeField(auto_now=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Job location"
        verbose_name_plural = "Job locations"
        ordering = ["-received_at"]

    def __str__(self):
        return f"Location for job {self.job_id} at {self.received_at}"

    def is_stale(self, now=None):
        """
        هل تقادمت النقطة فلم تعد تصف مكان العامل الآن؟

        📌 يُحسب على received_at لا recorded_at: ساعة الجهاز غير موثوقة،
           وتطبيق متوقف عن الإرسال هو بالضبط ما نريد كشفه.

        ⚠️ التقادم ليس خطأً: التطبيق قد يكون في الخلفية أو الشبكة ضعيفة.
           الواجهة تعرض "آخر تحديث منذ ..." بدل نقطة توحي بأنها حيّة.
        """
        moment = now or timezone.now()
        age = (moment - self.received_at).total_seconds()
        return age > LOCATION_STALE_AFTER_SECONDS
