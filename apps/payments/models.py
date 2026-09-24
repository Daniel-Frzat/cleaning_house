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
    حالة الشحن المباشر (§36.4، §13).

    ⚠️ لا حالات تفويض/حجز (HELD، AUTHORIZED، CAPTURED): الـescrow مسحوب
       من التصميم، وإضافتها تُحيي نموذجًا مُلغى — راجع docstring الملف.

    NOT_CHARGED     : لم يقبل مقاول بعد، فلا محاولة شحن.
    PROCESSING      : محاولة الشحن جارية لدى المزوّد.
    REQUIRES_ACTION : البنك يطلب مصادقة العميل (3-D Secure).
    SUCCEEDED       : تأكد الشحن.
    FAILED          : فشلت آخر محاولة.
    REFUNDED        : استُرد المبلغ فعليًا.

    ⚠️ REFUNDED معرَّفة ولا يصل إليها أي مسار اليوم: الاسترداد عملية
       حقيقية لدى المزوّد وسياسته غير محسومة. لا تُستعمل لتلوين حالة
       بلا استرداد فعلي (§13).

    📌 PENDING مُبقاة للتوافق مع الصفوف القائمة وحدها — المسارات الجديدة
       تبدأ NOT_CHARGED. حذفها كان سيكسر حجوزات مسجَّلة.
    """

    NOT_CHARGED = "NOT_CHARGED", "Not charged"
    PENDING = "PENDING", "Pending (legacy)"
    PROCESSING = "PROCESSING", "Processing"
    REQUIRES_ACTION = "REQUIRES_ACTION", "Requires customer authentication"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"
    REFUNDED = "REFUNDED", "Refunded"


# الحالات التي يجوز إعادة المحاولة منها (§16).
RETRYABLE_PAYMENT_STATUSES = frozenset(
    {PaymentStatus.FAILED, PaymentStatus.REQUIRES_ACTION}
)


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

    # يُملأ عند الفشل فقط — رسالة مقروءة للإدارة لا للعميل.
    failure_reason = models.TextField(null=True, blank=True)

    # 🔒 رمز خطأ المزوّد الخام — للتشخيص الإداري وحده، لا يُكشف للعميل:
    #    تفاصيل الرفض البنكي قد تساعد على تخمين بيانات البطاقة (§15).
    provider_error_code = models.CharField(max_length=64, blank=True, default="")

    # ------------------------------------------------------------
    # المحاولات (§16)
    # ------------------------------------------------------------
    # 📌 يُرقَّم مع كل محاولة جديدة، ويدخل في مفتاح التكرار. المفتاح
    #    الثابت الواحد كان يمنع محاولة مشروعة بطريقة دفع مختلفة: المزوّد
    #    يرى المفتاح نفسه فيعيد نتيجة المحاولة الفاشلة السابقة.
    attempt_number = models.PositiveIntegerField(default=1)

    # 🔒 ملخّص آمن لطريقة الدفع للعرض: {"type","display_name","card_brand",
    #    "last4"}. لا PAN ولا CVC ولا رمز مزوّد (§13).
    method_summary = models.JSONField(null=True, blank=True)

    # 📌 بيانات المصادقة المطلوبة (3-D Secure) كما يعيدها المزوّد —
    #    تُمرَّر للعميل ليكملها بالـSDK ثم تُمسح عند الحسم (§17).
    action_payload = models.JSONField(null=True, blank=True)

    # لحظة تأكيد الشحن — مصدرها تأكيد المزوّد لا استجابة الواجهة (§14).
    paid_at = models.DateTimeField(null=True, blank=True)

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

        # 📌 المبلغ يُقاس بالعرض المقبول المجمَّد لا بـcomputed_price:
        #    الشحن يقع **قبل** تأكيد الحجز الآن (§12)، فـcomputed_price لم
        #    تُكتب بعد لحظة إنشاء الدفعة. اللقطة الحقيقية تعيش على العرض.
        expected = self._expected_amount(booking)

        if expected is None:
            raise ValidationError(
                {"amount": "Booking has no frozen price yet; it cannot be charged."}
            )

        if self.amount != expected:
            raise ValidationError(
                {
                    "amount": (
                        f"Payment amount ({self.amount}) must equal the frozen "
                        f"accepted-offer total ({expected})."
                    )
                }
            )

        # 🔒 حارس السقف (§7): الشحن لا يتجاوز ما وافق عليه العميل أبدًا.
        #    الخرق هنا يعني خللًا في التسعير — يُرفض الشحن ولا يُصحَّح بصمت.
        if booking.max_total is not None and self.amount > booking.max_total:
            raise ValidationError(
                {
                    "amount": (
                        f"Payment amount ({self.amount}) exceeds the customer's "
                        f"approved maximum ({booking.max_total})."
                    )
                }
            )

    @staticmethod
    def _expected_amount(booking):
        """
        المبلغ المتوقَّع: إجمالي العرض المقبول المجمَّد، وإلا computed_price.

        📌 الرجوع إلى computed_price يخدم الحجوزات السابقة لهذا التغيير —
           تلك لا عرض مجمَّد لها، ومبلغها مكتوب على الحجز.
        """
        accepted = booking.dispatch_offers.filter(
            status__in=("ACCEPTED", "ACCEPTED_PENDING_PAYMENT")
        ).first()

        if accepted is not None and accepted.total_amount is not None:
            return accepted.total_amount

        return booking.computed_price
