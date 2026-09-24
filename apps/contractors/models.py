"""
Contractor Profile Models — Contractors Domain

يخزّن بيانات المقاول الثابتة التي سيستهلكها Dispatch لاحقًا. لا يحتوي أي
منطق مطابقة أو حساب مسافة — هذه المرحلة تخزين فقط.

⚠️ خارج النطاق في هذه الخطوة عمدًا:
   - أي منطق Dispatch/matching/distance (§36.7 — مرحلة لاحقة).
   - أي استدعاء لـgps_distance أو address_validation adapter — الاثنان
     يبقيان تجريديين حسب Phase 0 (Change Set §34/§4). الإحداثيات هنا
     حقول مخزَّنة تُملأ يدويًا من المقاول أو الإدارة.
   - أي FK لـBooking / Job / Dispatch.

قرار (تكرار مقصود لا اختصار): AustralianState ومحقّق الرمز البريدي
مُعرَّفان هنا محليًا وليسا مستوردين من apps.properties. السبب تقني:
Django يُضمّن choices والـvalidators داخل ملفات migration، فاستيرادهما
من نطاق شقيق يخلق اعتماد migration عابرًا للنطاقات مقابل قيمتين فقط.
استيراد apps.accounts.roles مختلف — ذاك مُعلَن مصدرًا وحيدًا للأدوار.
إن نشأ لاحقًا نطاق مشترك للعناوين (shared/address)، فهذه النسخة وتلك
التي في properties تُدمجان فيه.
"""

import uuid

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


class AustralianState(models.TextChoices):
    """الولايات والأقاليم الأسترالية السبعة (نسخة محلية — راجع docstring)."""

    NSW = "NSW", "New South Wales"
    VIC = "VIC", "Victoria"
    QLD = "QLD", "Queensland"
    WA = "WA", "Western Australia"
    SA = "SA", "South Australia"
    TAS = "TAS", "Tasmania"
    ACT = "ACT", "Australian Capital Territory"
    NT = "NT", "Northern Territory"


# رمز بريدي أسترالي: 4 أرقام بالضبط
POSTCODE_VALIDATOR = RegexValidator(
    regex=r"^\d{4}$",
    message="Postcode must be exactly 4 digits.",
)


class AvailabilityStatus(models.TextChoices):
    """
    جاهزية المقاول لاستقبال العمل.

    ⚠️ الافتراضي UNAVAILABLE عمدًا: الملف المنشأ حديثًا ناقص البيانات
       (غالبًا بلا إحداثيات)، وإتاحته تلقائيًا كانت ستُدخله في حسابات
       Dispatch قبل أن يكون جاهزًا فعلًا. الإتاحة فعل صريح من المقاول.
    """

    AVAILABLE = "AVAILABLE", "Available"
    UNAVAILABLE = "UNAVAILABLE", "Unavailable"


class ContractorProfile(models.Model):
    """
    ملف المقاول — علاقة واحد-لواحد مع المستخدم.

    ⚠️ الملف ذو معنى فقط لمستخدم دوره CONTRACTOR. قاعدة البيانات وحدها
       لا تستطيع فرض ذلك (الدور حقل على جدول آخر قابل للتغيير)، لذا
       الإنفاذ يعيش في طبقة الخدمة — راجع
       services/profile.py:assert_is_contractor.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="contractor_profile",
    )

    business_name = models.CharField(max_length=255, blank=True)

    # ------------------------------------------------------------
    # العنوان الثابت — مُضمَّن هنا وليس كيانًا منفصلًا
    # ------------------------------------------------------------
    # بخلاف PropertyAddress (كيان منفصل بقرار صريح — Change Set قسم 20)،
    # عنوان المقاول واحد وثابت ولا يتعدد، فلا حاجة لجدول مستقل.
    street_address = models.CharField(max_length=255, blank=True)
    suburb = models.CharField(max_length=120, blank=True)

    state = models.CharField(
        max_length=3, choices=AustralianState.choices, blank=True
    )
    postcode = models.CharField(
        max_length=4, validators=[POSTCODE_VALIDATOR], blank=True
    )

    # منتج بسوق واحد حاليًا — الحقل غير قابل للتعديل من المستخدم
    country = models.CharField(max_length=2, default="AU", editable=False)

    # ------------------------------------------------------------
    # الإحداثيات
    # ------------------------------------------------------------
    # ⚠️ تُملأ يدويًا في هذه المرحلة. لا geocoding ولا gps_distance adapter
    #    (Phase 0 — §34/§4). Dispatch سيستهلكها لاحقًا (§36.7).
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )

    availability_status = models.CharField(
        max_length=16,
        choices=AvailabilityStatus.choices,
        default=AvailabilityStatus.UNAVAILABLE,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Contractor profile"
        verbose_name_plural = "Contractor profiles"
        ordering = ["-created_at"]
        indexes = [
            # Dispatch سيرشّح بالجاهزية أولًا (§36.7)
            models.Index(fields=["availability_status"]),
            models.Index(fields=["suburb", "state"]),
        ]

    def __str__(self):
        return self.business_name or f"Contractor profile ({self.user_id})"

    @property
    def has_coordinates(self):
        """
        هل الإحداثيات مكتملة؟

        مجرد فحص اكتمال بيانات — لا يحسب مسافة ولا يستدعي أي adapter.
        Dispatch سيحتاجه لاحقًا لاستبعاد الملفات غير القابلة للقياس.
        """
        return self.latitude is not None and self.longitude is not None


# ============================================================
# التحقق من المقاول — مراجعة يدوية بالكامل (Change Set §6، Infra §5)
# ============================================================
# ⚠️ قرار محوري: هذه المرحلة مراجعة بشرية فقط (MANUAL/ADMIN-REVIEWED).
#    لا استدعاء لأي سجل حكومي ولا خدمة تحقق خارجية، والـadapter التجريدي
#    adapters/business_registry يبقى دون استخدام — لا يُستورد هنا إطلاقًا.
#    التحقق من الـABN شكلي بحت (11 رقمًا)، وليس تأكيدًا لوجود الرقم فعليًا.
#
# ⚠️ خارج النطاق عمدًا (Infra §5 — "policy إعادة التحقق" بند مفتوح):
#    لا جدولة إعادة تحقق، ولا مهمة Celery عند انتهاء الصلاحية، ولا
#    إعادة فحص تلقائية. انتهاء الصلاحية يُقرأ لحظيًا عند حساب الأهلية.


class VerificationStatus(models.TextChoices):
    """
    حالة مراجعة مستند التحقق.

    ⚠️ الافتراضي PENDING: لا شيء يُعدّ متحققًا قبل مراجعة إداري بشري.
    """

    PENDING = "PENDING", "Pending review"
    VERIFIED = "VERIFIED", "Verified"
    REJECTED = "REJECTED", "Rejected"


def validate_abn_checksum(value):
    """
    خوارزمية التحقق الرسمية لـABN (Australian Business Register):
    اطرح 1 من الخانة الأولى، اضرب كل خانة بوزنها، والمجموع يقبل القسمة
    على 89. تلتقط الأخطاء المطبعية — لا تثبت أن الرقم مسجَّل فعلًا.
    """
    if not (isinstance(value, str) and value.isdigit() and len(value) == 11):
        return  # الشكل مسؤولية ABN_VALIDATOR
    weights = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
    digits = [int(c) for c in value]
    digits[0] -= 1
    if sum(d * w for d, w in zip(digits, weights)) % 89 != 0:
        raise ValidationError("ABN is not valid (checksum failed).")


# ABN أسترالي: 11 رقمًا بالضبط.
# ⚠️ فحص شكلي فقط — لا يتحقق من خانة التدقيق (checksum) ولا من وجود
#    الرقم في السجل الحكومي. التحقق الفعلي قرار مؤجَّل (§6).
ABN_VALIDATOR = RegexValidator(
    regex=r"^\d{11}$",
    message="ABN must be exactly 11 digits.",
)


class ReviewableDocument(models.Model):
    """
    أساس مشترك لمستندات التحقق الخاضعة لمراجعة إدارية.

    مجرّد (abstract) — لا جدول له. يجمّع حقول المراجعة المتطابقة بين
    تسجيل النشاط ووثيقة التأمين حتى لا تتفرّق قواعد المراجعة نسختين.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    status = models.CharField(
        max_length=16,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )

    # المراجِع البشري — يبقى فارغًا ما دامت الحالة PENDING.
    # SET_NULL: حذف حساب الإداري لا يجوز أن يمحو سجل المراجعة نفسه.
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # إلزامي عند الرفض — يُفرض في clean() أدناه وفي طبقة الخدمة.
    rejection_reason = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def clean(self):
        """
        🔒 الرفض بلا سبب مرفوض على مستوى الـModel نفسه.

        الإنفاذ هنا وليس في طبقة الخدمة وحدها: أي مسار كتابة (API، أمر
        إداري، shell) يمر عبر full_clean يُمنع من ترك سجل مرفوض بلا سبب.
        """
        super().clean()

        if self.status == VerificationStatus.REJECTED and not (
            self.rejection_reason or ""
        ).strip():
            raise ValidationError(
                {"rejection_reason": "A rejection reason is required when rejecting."}
            )


class BusinessRegistration(ReviewableDocument):
    """
    تسجيل النشاط التجاري للمقاول (ABN) — يُراجَع يدويًا.

    ⚠️ المقاول قد يقدّم أكثر من سجل عبر الزمن (إعادة تقديم بعد رفض،
       أو تحديث بيانات). لا قيد يمنع التعدد عمدًا — "الأحدث" يُحسم
       بـcreated_at عند حساب الأهلية.
    """

    contractor = models.ForeignKey(
        ContractorProfile,
        on_delete=models.CASCADE,
        related_name="business_registrations",
    )

    abn = models.CharField(
        max_length=11,
        validators=[ABN_VALIDATOR, validate_abn_checksum],
        help_text="11-digit ABN. Format and checksum validation only — not a registry lookup.",
    )
    business_name = models.CharField(max_length=255)

    class Meta(ReviewableDocument.Meta):
        verbose_name = "Business registration"
        verbose_name_plural = "Business registrations"
        indexes = [
            models.Index(fields=["contractor", "-created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.business_name} — ABN {self.abn} ({self.status})"


class InsuranceDocument(ReviewableDocument):
    """
    وثيقة تأمين المقاول — تُراجَع يدويًا.

    ⚠️ document_reference نص (رقم بوليصة مثلًا) وليس ملفًا مرفوعًا.
       تكامل تخزين الملفات شأن منفصل لاحقًا (Infra §7) — لا يُبنى الآن.
    """

    contractor = models.ForeignKey(
        ContractorProfile,
        on_delete=models.CASCADE,
        related_name="insurance_documents",
    )

    document_reference = models.CharField(
        max_length=255,
        help_text="Policy number or reference. Not a file upload (Infra §7).",
    )
    expiry_date = models.DateField()

    class Meta(ReviewableDocument.Meta):
        verbose_name = "Insurance document"
        verbose_name_plural = "Insurance documents"
        indexes = [
            models.Index(fields=["contractor", "-created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["expiry_date"]),
        ]

    def __str__(self):
        return f"Insurance {self.document_reference} (expires {self.expiry_date})"

    @property
    def is_expired(self):
        """
        هل انتهت الصلاحية بالنسبة لليوم؟

        قراءة لحظية — لا جدولة ولا مهمة خلفية تراقب الانتهاء (Infra §5).
        """
        return self.expiry_date < timezone.localdate()
