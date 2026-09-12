"""
Payout Models — Payout Domain (Change Set §36.5)

دفع المقاول عند تأكيد العميل إنجاز العمل.

📌 قرارات محسومة في هذه الجلسة، لا تُخالَف:

  1) دفع **فوري لكل Job** — لا تجميع ولا دفعات مجمَّعة. لذلك لا يوجد
     في هذا الملف (ولا يجوز أن يوجد) أي كيان PayoutBatch، ولا حالة
     BATCHED أو SCHEDULED، ولا حقل batch_id. الحالات ثلاث فقط.

  2) المبلغ كامل بلا عمولة: amount == booking.computed_price بالضبط.
     صفر عمولة (§36.5 — Merchant Model). أي خصم يحتاج قرار عمل صريح.

  3) الربط بـBooking لا بـJob (نفس نمط Payment — §43): الملكية القانونية
     للمبلغ تعود للحجز، فهو المصدر الأصلي للسعر المجمَّد computed_price.
     Job هو المُحفِّز الزمني (COMPLETED)، لا مالك المبلغ.

⚠️ provider_reference معرّف مبهم يعيده المزوّد: يُخزَّن كما هو ولا
   يُفسَّر ولا يُحلَّل في منطق النطاق.
"""

import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class PayoutStatus(models.TextChoices):
    """
    حالة الدفع للمقاول.

    ⚠️ ثلاث حالات فقط. لا BATCHED ولا SCHEDULED ولا QUEUED — الدفع فوري
       بقرار محسوم، وأي حالة تُلمّح إلى تأجيل أو تجميع تخالفه.
    """

    PENDING = "PENDING", "Pending"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"


class Payout(models.Model):
    """
    دفعة واحدة للمقاول مقابل حجز واحد.

    ⚠️ العلاقة OneToOne: حجز واحد = دفعة مقاول واحدة. قيد على مستوى
       قاعدة البيانات لا مجرد عرف في طبقة الخدمة — دفاع في العمق ضد
       الدفع المزدوج (Infra §14/§16)، نفس نمط Payment.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # PROTECT: الحجز المرتبط بدفعة لا يُحذف من تحتها — سجل مالي.
    booking = models.OneToOneField(
        "bookings.Booking",
        on_delete=models.PROTECT,
        related_name="payout",
    )

    # 📌 يُنسخ من booking.assigned_contractor.user وقت الإنشاء ولا يُشتق
    #    لاحقًا: إسناد الحجز قد يتغيّر أو يُفرَّغ (SET_NULL)، والدفعة
    #    يجب أن تبقى شاهدة على من استحقّ المبلغ فعلًا لحظة الدفع.
    # PROTECT: حساب مقاول له دفعات لا يُحذف من تحتها.
    contractor = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="payouts",
    )

    # 📌 المبلغ الكامل بلا عمولة — يُفرض في clean() أدناه.
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Full booking.computed_price — zero commission (§36.5).",
    )

    status = models.CharField(
        max_length=16,
        choices=PayoutStatus.choices,
        default=PayoutStatus.PENDING,
    )

    # 🔒 مبهم: يُخزَّن ولا يُفسَّر
    provider_reference = models.TextField(null=True, blank=True)

    # يُملأ عند الفشل فقط
    failure_reason = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Payout"
        verbose_name_plural = "Payouts"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["contractor", "-created_at"]),
        ]

    def __str__(self):
        return f"Payout {self.id} — {self.amount} ({self.status})"

    def clean(self):
        """
        🔒 المبلغ يجب أن يساوي لقطة سعر الحجز بالضبط — بلا خصم.

        الإنفاذ على مستوى الـModel لا طبقة الخدمة وحدها (نفس قرار
        Payment.clean في §43): أي مسار كتابة يمر بـfull_clean (API،
        أمر إداري، shell) يُمنع من إنشاء دفعة بمبلغ مخالف.

        ⚠️ لا عمولة: المساواة تامة وليست نسبة. لو قُرِّرت عمولة لاحقًا
           فهي قاعدة عمل جديدة تُعدَّل هنا صراحةً، لا تُدسّ في حساب.
        """
        super().clean()

        booking = getattr(self, "booking", None)
        if booking is None:
            return

        if booking.computed_price is None:
            raise ValidationError(
                {"amount": "Booking has no computed price; nothing can be paid out."}
            )

        if self.amount != booking.computed_price:
            raise ValidationError(
                {
                    "amount": (
                        f"Payout amount ({self.amount}) must equal the booking's "
                        f"computed price ({booking.computed_price}) exactly — "
                        "payouts carry zero commission (§36.5)."
                    )
                }
            )
