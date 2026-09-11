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

from django.core.validators import MinValueValidator
from django.db import models

# الأسعار لا تكون سالبة. صفر مسموح (خدمة ترويجية أو بلا رسم أساسي).
NON_NEGATIVE = [MinValueValidator(0)]

# دقة نقدية: حتى 99,999,999.99 — كافية لمبالغ الخدمات المنزلية.
PRICE_MAX_DIGITS = 10
PRICE_DECIMAL_PLACES = 2


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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pricing configuration"
        verbose_name_plural = "Pricing configuration"

    def __str__(self):
        return f"Pricing config (price_per_km={self.price_per_km})"

    def save(self, *args, **kwargs):
        """يمنع إنشاء صف ثانٍ مهما كانت قيمة pk المُمرَّرة."""
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """الـsingleton لا يُحذف — حذفه يترك النظام بلا سعر كيلومتر."""
        raise NotImplementedError("PricingConfig is a singleton and cannot be deleted.")
