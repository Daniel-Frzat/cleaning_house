"""
Service Catalog & Pricing Models — Services Domain (Change Set §36.2، §5)

قراران معماريان محسومان في هذه المرحلة:

  1) التسعير لكل خدمة (per-service): room_price و base_price يعيشان على
     ServiceType نفسه — لكل خدمة سعر غرفة وسعر أساسي خاصان بها.
     ليست قيمًا عامة مشتركة بين كل الخدمات.

  2) سعر الكيلومتر عام (global): price_per_km قيمة واحدة على مستوى النظام
     كله وليست لكل خدمة — لذلك تعيش في PricingConfig المنفصل.

⚠️ خارج النطاق في هذه الخطوة عمدًا:
   - أي منطق حساب سعر أو إعادة حساب رجعي (retroactive recalculation).
     هذا الـModel يخزّن القيم الحيّة الحالية فقط — لا تاريخ أسعار ولا نسخ.
   - تجميد السعر على الحجز (price snapshotting) شأن Booking Domain لاحقًا،
     وليس مسؤولية هذه المرحلة.
   - أي FK لـBooking / Job.
   - أي نقطة نهاية موجّهة للعميل — كتالوج الأسعار الخام غير مرئي
     للـCUSTOMER/CONTRACTOR في هذه المرحلة.
"""

import uuid
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

# الأسعار لا تكون سالبة. صفر مسموح (خدمة ترويجية أو بلا رسم أساسي).
NON_NEGATIVE = [MinValueValidator(0)]

# دقة نقدية: حتى 99,999,999.99 — كافية لمبالغ الخدمات المنزلية.
PRICE_MAX_DIGITS = 10
PRICE_DECIMAL_PLACES = 2


class RoundingRule(models.TextChoices):
    """
    كيف يُقرَّب الإجمالي النهائي (§7).

    ⚠️ يُطبَّق مرة واحدة على الإجمالي وحده — لا على المكوّنات. التقريب
       المزدوج يُدخل انحرافًا تراكميًا ويجعل المجموع لا يطابق أجزاءه.

    📌 NEAREST_CENT هو الافتراضي وهو السلوك القائم حرفيًا
       (quantize إلى 0.01)، فالترقية لا تغيّر أي سعر.
    """

    NEAREST_CENT = "NEAREST_CENT", "Nearest cent"
    NEAREST_5C = "NEAREST_5C", "Nearest 5 cents"
    NEAREST_10C = "NEAREST_10C", "Nearest 10 cents"
    NEAREST_DOLLAR = "NEAREST_DOLLAR", "Nearest dollar"


class ServiceType(models.Model):
    """
    نوع خدمة معروض في الكتالوج ("General Cleaning"، "Carpet Cleaning"،
    "Garden Cleaning").

    ⚠️ room_price و base_price قابلان للتعديل في أي وقت، وبأثر فوري على
       القراءات اللاحقة فقط. لا يوجد هنا أي إعادة حساب للحجوزات السابقة —
       الحجز الذي يحتاج سعرًا ثابتًا يأخذ نسخته الخاصة في Booking Domain.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)

    # سعر الغرفة الواحدة لهذه الخدمة تحديدًا — ليس سعرًا عامًا.
    room_price = models.DecimalField(
        max_digits=PRICE_MAX_DIGITS,
        decimal_places=PRICE_DECIMAL_PLACES,
        validators=NON_NEGATIVE,
        help_text="Per-room rate for THIS service (not global).",
    )

    # مبلغ أساسي ثابت لهذه الخدمة تحديدًا.
    base_price = models.DecimalField(
        max_digits=PRICE_MAX_DIGITS,
        decimal_places=PRICE_DECIMAL_PLACES,
        validators=NON_NEGATIVE,
        help_text="Flat base amount for THIS service (not global).",
    )

    # تعطيل ناعم: الخدمة تختفي من التداول دون حذف الصف، لأن حجوزات
    # مستقبلية قد تشير إليها.
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Service type"
        verbose_name_plural = "Service types"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name


class PricingConfig(models.Model):
    """
    إعداد التسعير العام — Singleton (صف واحد فقط، pk=1).

    قرار (Change Set §5): price_per_km قيمة عامة واحدة للنظام كله وليست
    لكل خدمة، لذلك لا تعيش على ServiceType.

    لماذا جدول وليس Django settings؟ لأن المطلوب تعديلها عبر
    PATCH /api/admin/pricing-config أثناء التشغيل — وإعدادات Django
    ثابتة لا تتغير إلا بإعادة تشغيل العملية.
    """

    # مفتاح ثابت يفرض وجود صف واحد لا غير.
    SINGLETON_PK = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_PK)

    price_per_km = models.DecimalField(
        max_digits=PRICE_MAX_DIGITS,
        decimal_places=PRICE_DECIMAL_PLACES,
        default=0,
        validators=NON_NEGATIVE,
        help_text="Single global flat rate per kilometre (NOT per service).",
    )

    # ------------------------------------------------------------
    # رسم المسافة — يتحكم به الداشبورد بالكامل (§7)
    # ------------------------------------------------------------
    # 📌 المسافة المجانية: أول كذا كم لا تُحتسب. صفر يعني "كل كيلومتر
    #    محسوب" وهو السلوك السابق تمامًا، فالترقية لا تغيّر أي سعر قائم.
    included_distance_km = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=0,
        validators=NON_NEGATIVE,
        help_text="Free travel distance; only the excess is charged.",
    )

    # 🔒 السقف الذي يحمي وعد "لن يتجاوز" المعروض للعميل: أقصى ما يمكن أن
    #    يبلغه مكوّن المسافة مهما بعُد المقاول. هو نفسه المستعمل في حساب
    #    السقف الأقصى للعرض على العميل قبل البحث (§5).
    # ⚠️ الافتراضي مرتفع عمدًا: صفر كان سيجعل كل رسوم المسافة صفرًا فورًا
    #    بعد الهجرة ويغيّر التسعير القائم صامتًا.
    maximum_travel_fee = models.DecimalField(
        max_digits=PRICE_MAX_DIGITS,
        decimal_places=PRICE_DECIMAL_PLACES,
        default=Decimal("9999.99"),
        validators=NON_NEGATIVE,
        help_text="Cap on the travel component, however far the contractor is.",
    )

    currency = models.CharField(
        max_length=3,
        default="AUD",
        help_text="ISO 4217 code; single-currency product for now.",
    )

    rounding_rule = models.CharField(
        max_length=16,
        choices=RoundingRule.choices,
        default=RoundingRule.NEAREST_CENT,
        help_text="Applied once, to the final total only.",
    )

    # 📌 يُرقَّم تلقائيًا عند كل تعديل. يُجمَّد على العرض والحجز، فيُعرف
    #    لاحقًا بأي قواعد حُسب سعرٌ ما — وهو ما يجعل تدقيق الإدارة ممكنًا.
    pricing_version = models.PositiveIntegerField(
        default=1,
        help_text="Incremented on every change; frozen onto offers and bookings.",
    )

    active_from = models.DateTimeField(
        default=timezone.now,
        help_text="When this configuration took effect.",
    )

    # مهلة العرض — كانت ثابتة 60 دقيقة في الكود (§11).
    # ⚠️ الافتراضي هنا 3600 ثانية = القيمة القديمة نفسها، فالترقية لا
    #    تغيّر سلوك أي عرض قائم. القيمة المناسبة للطلب الفوري أقصر بكثير
    #    ويضبطها الداشبورد.
    dispatch_offer_ttl_seconds = models.PositiveIntegerField(
        default=3600,
        validators=[MinValueValidator(1)],
        help_text="How long a dispatch offer stays answerable.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pricing configuration"
        verbose_name_plural = "Pricing configuration"

    def __str__(self):
        return f"Pricing config v{self.pricing_version} ({self.currency})"

    # الحقول التي يُعدّ تغيّرها تغييرًا في قواعد التسعير.
    # ⚠️ dispatch_offer_ttl_seconds ليست منها: المهلة سياسة إسناد لا سعر،
    #    وترقيم نسخة التسعير لأجلها يجعل الرقم يكذب على التدقيق.
    PRICING_FIELDS = (
        "price_per_km",
        "included_distance_km",
        "maximum_travel_fee",
        "currency",
        "rounding_rule",
    )

    def save(self, *args, **kwargs):
        """
        يمنع إنشاء صف ثانٍ، ويُرقّي نسخة التسعير عند كل تغيير فعلي.

        📌 الترقيم تلقائي لا يدوي: نسخة تعتمد على تذكّر الإدارة لرفعها
           تصبح كذبة صامتة عند أول نسيان — واللقطات المجمَّدة تشير عندها
           إلى قواعد غير التي حُسبت بها.

        ⚠️ التغيير يُقاس بالقيم المخزَّنة لا بالوقت: حفظ بلا تعديل فعلي
           لا يرفع الرقم، فلا يتضخّم بلا معنى.
        """
        self.pk = self.SINGLETON_PK

        previous = PricingConfig.objects.filter(pk=self.SINGLETON_PK).first()
        if previous is not None:
            changed = any(
                getattr(previous, field) != getattr(self, field)
                for field in self.PRICING_FIELDS
            )
            if changed:
                self.pricing_version = previous.pricing_version + 1
                self.active_from = timezone.now()
            else:
                # يُحترم الرقم القائم: الحفظ بلا تغيير تسعيري لا يرقّيه.
                self.pricing_version = previous.pricing_version

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """الـsingleton لا يُحذف — حذفه يترك النظام بلا سعر كيلومتر."""
        raise NotImplementedError("PricingConfig is a singleton and cannot be deleted.")
