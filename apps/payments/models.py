"""
Payment Models — Payment Domain (Change Set §36.4، §8؛ Infra §2)

⚠️ نموذج الدفع هنا **شحن مباشر** (direct charge) وليس حجزًا/تفويضًا:
   لا authorize/capture، ولا hold/escrow. لذلك الحالات ثلاث فقط:
   PENDING / SUCCEEDED / FAILED.

   لا تُضف HELD أو AUTHORIZED أو CAPTURED أو RELEASED — هذه تنتمي لمفهوم
   الضمان (escrow) الذي **سُحب** من التصميم. إضافتها تعيد إحياء نموذج
   متقاعد وتناقض §36.4.

⚠️ provider_reference معرّف مبهم (opaque) يعيده المزوّد: يُخزَّن كما هو
   ولا يُفسَّر ولا يُحلَّل في منطق النطاق — شكله يختلف بين المزوّدين،
   وأي تحليل له يربط النطاق بمزوّد بعينه.
"""

import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class PaymentMethod(models.TextChoices):
    """
    وسائل الدفع المعتمدة (§36.4).

    ⚠️ ثلاث قيم فقط — لا تُضاف وسائل أخرى (تحويل بنكي، نقدًا، محفظة)
       قبل أن تُعتمد كقاعدة عمل.
    """

    CARD = "CARD", "Card"
    APPLE_PAY = "APPLE_PAY", "Apple Pay"
    GOOGLE_PAY = "GOOGLE_PAY", "Google Pay"


class PaymentStatus(models.TextChoices):
    """
    حالة الشحن المباشر (§36.4).

    ⚠️ ثلاث حالات فقط. لا حالات تفويض/حجز — راجع docstring الملف.
    """

    PENDING = "PENDING", "Pending"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"


class Payment(models.Model):
    """
    عملية دفع واحدة مرتبطة بحجز واحد.

    ⚠️ العلاقة OneToOne: حجز واحد = دفعة واحدة. هذا قيد على مستوى قاعدة
       البيانات وليس مجرد عرف في طبقة الخدمة — دفاع في العمق ضد الشحن
       المزدوج (Infra §14/§16).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # PROTECT: الحجز المرتبط بدفعة لا يُحذف من تحتها — سجل مالي.
    booking = models.OneToOneField(
        "bookings.Booking",
        on_delete=models.PROTECT,
        related_name="payment",
    )

    # 📌 يجب أن يساوي booking.computed_price لحظة الشحن. الفحص في
    #    clean() أدناه وفي طبقة الخدمة معًا.
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Must equal booking.computed_price at charge time.",
    )

    method = models.CharField(
        max_length=16,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CARD,
    )

    status = models.CharField(
        max_length=16,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )

    # 🔒 مبهم: يُخزَّن ولا يُفسَّر. ولا يُكشف للعميل (تفصيل تشخيصي داخلي).
    provider_reference = models.TextField(null=True, blank=True)

    # يُملأ عند الفشل فقط
    failure_reason = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Payment"
        verbose_name_plural = "Payments"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Payment {self.id} — {self.amount} ({self.status})"

    def clean(self):
        """
        🔒 المبلغ يجب أن يطابق لقطة سعر الحجز بالضبط.

        الإنفاذ هنا وليس في طبقة الخدمة وحدها: أي مسار كتابة يمر بـ
        full_clean (API، أمر إداري، shell) يُمنع من إنشاء دفعة بمبلغ
        مخالف للقطة المجمَّدة.
        """
        super().clean()

        booking = getattr(self, "booking", None)
        if booking is None:
            return

        if booking.computed_price is None:
            raise ValidationError(
                {"amount": "Booking has no computed price; it cannot be charged yet."}
            )

        if self.amount != booking.computed_price:
            raise ValidationError(
                {
                    "amount": (
                        f"Payment amount ({self.amount}) must equal the booking's "
                        f"computed price ({booking.computed_price})."
                    )
                }
            )
