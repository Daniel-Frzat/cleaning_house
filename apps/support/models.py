"""
Support Models — Support Domain (MVP)

طلب دعم واحد يقدّمه مستخدم، وقد يربطه بحجز. يغطي شاشتَي "Contact support"
و"Report an issue" الموجودتين في الواجهة.

⚠️ خارج النطاق عمدًا (قرار MVP صريح):
   - لا ردود ولا محادثة: لا SupportReply ولا thread_id ولا حقل
     `assigned_to`. الطلب قناة باتجاه واحد في هذه المرحلة، والرد يتم
     خارج النظام (هاتف/بريد). إضافة نصف محادثة أسوأ من لا محادثة.
   - لا مرفقات: مزوّد التخزين قرار مفتوح، و FakeStorageAdapter يهمل
     محتوى الملفات فعليًا. قبول مرفق اليوم يعني ضياعه صامتًا.
   - لا إشعارات ولا SLA ولا تصعيد: كلها قواعد عمل لم تُطلب.

📌 الحالة تُغيَّرها الإدارة من لوحة Django وحدها في هذه المرحلة — لا
   مسار API لتغييرها، ولا تُقبل من العميل عند الإنشاء.
"""

import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models


class SupportCategory(models.TextChoices):
    """
    تصنيف الطلب.

    📌 قائمة مغلقة لا نص حر: الفرز في لوحة الإدارة يحتاج قيمًا ثابتة،
       والنص الحر يتحول إلى قيم متضاربة بسرعة. التوسيع هجرة بسيطة.

    ⚠️ OTHER موجودة عمدًا: بدونها يضطر المستخدم لاختيار تصنيف خاطئ،
       فيفسد الفرز الذي وُجدت القائمة من أجله.
    """

    BOOKING_ISSUE = "BOOKING_ISSUE", "Booking issue"
    PAYMENT_ISSUE = "PAYMENT_ISSUE", "Payment issue"
    CONTRACTOR_ISSUE = "CONTRACTOR_ISSUE", "Contractor issue"
    APP_ISSUE = "APP_ISSUE", "App issue"
    OTHER = "OTHER", "Other"


class SupportStatus(models.TextChoices):
    """
    حالة معالجة الطلب.

    ⚠️ ثلاث قيم فقط. لا CLOSED ولا REOPENED ولا WAITING_CUSTOMER: كلها
       تفترض دورة محادثة غير موجودة. RESOLVED نهائية في هذه المرحلة.
    """

    SUBMITTED = "SUBMITTED", "Submitted"
    UNDER_REVIEW = "UNDER_REVIEW", "Under review"
    RESOLVED = "RESOLVED", "Resolved"


# حدّ نص الرسالة. واسع بما يكفي لشرح مشكلة كاملة، ومحدود حتى لا يصبح
# الحقل قناة رفع محتوى.
MESSAGE_MAX_LENGTH = 2000


class SupportRequest(models.Model):
    """
    طلب دعم من مستخدم.

    🔒 قيد لا تفرضه قاعدة البيانات ويعيش في clean(): الحجز المربوط يخصّ
       مقدّم الطلب. بدونه يستطيع مستخدم إرفاق حجز غيره برسالته، فيستنتج
       من قبول الطلب أن ذلك الحجز موجود.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # PROTECT: سجل الدعم شهادة على واقعة، ولا يُمحى بحذف الحساب من تحته.
    # (نفس منطق Payout.contractor.)
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="support_requests",
    )

    # 📌 اختياري: "لم يصلني رمز الدخول" أو "التطبيق يتوقف" طلبان بلا حجز.
    # PROTECT كذلك: الحجز المُشار إليه من طلب دعم لا يُحذف من تحته.
    booking = models.ForeignKey(
        "bookings.Booking",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="support_requests",
        help_text="Optional — the booking this request is about.",
    )

    category = models.CharField(
        max_length=24,
        choices=SupportCategory.choices,
    )

    # ⚠️ MaxLengthValidator صراحةً: max_length على TextField يؤثّر في
    #    واجهات النماذج فقط ولا يولّد تحققًا في full_clean — نفس الملاحظة
    #    المسجَّلة على Booking.access_notes.
    message = models.TextField(
        validators=[MaxLengthValidator(MESSAGE_MAX_LENGTH)],
    )

    # 🔒 لا تُقبل من العميل عند الإنشاء: كل طلب يبدأ SUBMITTED.
    status = models.CharField(
        max_length=16,
        choices=SupportStatus.choices,
        default=SupportStatus.SUBMITTED,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Support request"
        verbose_name_plural = "Support requests"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["category"]),
        ]

    def __str__(self):
        return f"Support {self.id} ({self.category}/{self.status})"

    def clean(self):
        """
        🔒 الحجز المربوط يخصّ مقدّم الطلب.

        يُفرض هنا لا في طبقة الخدمة وحدها: أي مسار كتابة يمرّ بـfull_clean
        (API، أمر إداري، shell) يُمنع من ربط حجز غريب.
        """
        super().clean()

        if self.booking_id is None:
            return

        # user_id قد يكون None في نموذج غير مكتمل — لا نُخفي ذلك خلف
        # رسالة ملكية مضلّلة، ونترك فحص الحقل الإلزامي لـfull_clean.
        if self.user_id is None:
            return

        if self.booking.customer_id != self.user_id:
            raise ValidationError(
                {"booking": "This booking does not belong to you."}
            )
