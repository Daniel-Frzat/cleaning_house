"""
Properties & Address Models — Properties Domain (Phase 1)

قرار معماري (Change Set — قسم 20): PropertyAddress يبقى كيانًا منفصلًا
عن Property ولا يُدمج داخله (not flattened).

⚠️ خارج النطاق في هذه الخطوة عمدًا:
   - أي FK لـBooking / Job / QualityGuarantee
   - أي استدعاء لـaddress_validation adapter (قرار مزوّد مفتوح — البند #23)
   - أي استدعاء لـgps_distance adapter (تعبئة الإحداثيات شأن Phase 2)
"""

import uuid

from django.core.validators import RegexValidator
from django.db import models


class PropertyType(models.TextChoices):
    """أنواع العقارات السكنية في السياق الأسترالي."""

    HOUSE = "HOUSE", "House"
    UNIT = "UNIT", "Unit"
    TOWNHOUSE = "TOWNHOUSE", "Townhouse"
    APARTMENT = "APARTMENT", "Apartment"
    OTHER = "OTHER", "Other"


class AustralianState(models.TextChoices):
    """الولايات والأقاليم الأسترالية السبعة."""

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


class Property(models.Model):
    """
    عقار يملكه مستخدم واحد.

    ⚠️ ASSUMPTION — راجع "Change Set — Properties domain, ownership model
       assumption": العقار له مالك واحد بالضبط، لذلك العلاقة ForeignKey
       بسيطة وليست ManyToMany.

       هذا تبسيط مقصود وليس تأكيدًا لقاعدة عمل. إذا تقرر لاحقًا دعم
       الملكية المشتركة (عقار بعدة ملاك، أو تفويض مدير عقار)، فنقطة
       التغيير هي هذا الحقل تحديدًا — يتحول إلى M2M عبر جدول وسيط،
       مع مراجعة كل فحوص الملكية في طبقة الخدمة.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    owner = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="properties",
    )

    # اسم ودّي يختاره المستخدم للتمييز بين عقاراته ("Home"، "Investment Unit 2")
    label = models.CharField(max_length=100, blank=True)

    property_type = models.CharField(
        max_length=16,
        choices=PropertyType.choices,
        default=PropertyType.HOUSE,
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Property"
        verbose_name_plural = "Properties"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "is_active"]),
        ]

    def __str__(self):
        return self.label or f"{self.get_property_type_display()} ({self.id})"


class PropertyAddress(models.Model):
    """
    عنوان العقار — كيان منفصل عن Property (Change Set — قسم 20).

    ⚠️ لا يوجد هنا أي تحقق خارجي من صحة العنوان. المزوّد غير محسوم
       (البند #23)، والتحقق الحالي شكلي فقط (choices + صيغة الرمز البريدي).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    property = models.OneToOneField(
        Property,
        on_delete=models.CASCADE,
        related_name="address",
    )

    street_address = models.CharField(max_length=255)
    suburb = models.CharField(max_length=120)

    state = models.CharField(max_length=3, choices=AustralianState.choices)
    postcode = models.CharField(max_length=4, validators=[POSTCODE_VALIDATOR])

    # منتج بسوق واحد حاليًا — الحقل غير قابل للتعديل من المستخدم
    country = models.CharField(max_length=2, default="AU", editable=False)

    # تُعبَّأ لاحقًا عبر GPS adapter (Phase 2) — لا يُستدعى الآن
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )

    # ما كتبه المستخدم أصلًا قبل أي تطبيع/تحقق مستقبلي
    raw_input = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Property address"
        verbose_name_plural = "Property addresses"
        indexes = [
            models.Index(fields=["suburb", "state"]),
            models.Index(fields=["postcode"]),
        ]

    def __str__(self):
        return f"{self.street_address}, {self.suburb} {self.state} {self.postcode}"
