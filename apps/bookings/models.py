"""
Booking Models — Booking Domain (Change Set §36.1، §20)

الحجز وأسطر خدماته، وعروض الإسناد التلقائي (§36.1، §36.5، §36.6).

⚠️ خارج النطاق عمدًا:
   - أي استدعاء لـgps_distance adapter: المسافة تُحسب محليًا
     (services/distance.py) بمعادلة haversine على إحداثيات مخزَّنة،
     وليست Routing Engine (Infra §8).
   - أي سلوك بديل حين لا يوجد مقاول مرشَّح — القرار المفتوح #16.

📌 لقطة السعر (price snapshot): computed_price هو اللقطة المؤجَّلة من
   §36.2. تُكتب مرة واحدة لحظة قبول المقاول (services/offers.py)، ثم
   تُجمَّد ولا يُعاد حسابها مهما تغيّرت أسعار الكتالوج لاحقًا.
   apps/services/services/pricing.py يقرأ القيم الحيّة عمدًا ولا يحتفظ
   بشيء — الحفظ مسؤولية هذا النموذج وحده.
"""

import uuid

from django.db import models
from django.utils import timezone


class BookingStatus(models.TextChoices):
    """
    حالة الحجز (§36.1).

    ⚠️ ثلاث قيم فقط في هذه المرحلة — لا تُضاف قيم أخرى استباقًا
       (IN_PROGRESS / COMPLETED وما شابه) قبل أن تُحسم دورة حياة العمل.

    PENDING   : أُنشئ، ولا مقاول مُسنَدًا بعد ولا سعر مكشوفًا.
    CONFIRMED : قَبِل مقاولٌ العرض، فكُشف السعر وثُبِّت (§36.1).
    CANCELLED : أُلغي.
    """

    PENDING = "PENDING", "Pending"
    CONFIRMED = "CONFIRMED", "Confirmed"
    CANCELLED = "CANCELLED", "Cancelled"


class Booking(models.Model):
    """
    حجز يقدّمه عميل على أحد عقاراته.

    ⚠️ قيدان لا تفرضهما قاعدة البيانات ويعيشان في طبقة الخدمة:
       1) customer دوره CUSTOMER (الدور حقل على جدول آخر قابل للتغيير).
       2) property مملوك لنفس العميل — يُفحص عبر
          properties/services/properties.py:assert_owns.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    customer = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="bookings",
    )

    # PROTECT: العقار المرتبط بحجوزات لا يُحذف من تحتها — الحجز سجل
    # تعاقدي، وحذف العقار يجب أن يفشل بدل أن يُفقد الحجز صامتًا.
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.PROTECT,
        related_name="bookings",
    )

    status = models.CharField(
        max_length=16,
        choices=BookingStatus.choices,
        default=BookingStatus.PENDING,
    )

    # 📌 لقطة السعر المجمَّدة (§36.2). null حتى يقبل مقاولٌ العرض.
    # ⚠️ لا يُعاد حسابه بعد ضبطه — تغيّر أسعار الكتالوج لاحقًا لا يمسّه.
    computed_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Frozen price snapshot, set once on contractor acceptance.",
    )

    # يُملأ عند قبول عرض (services/offers.py). SET_NULL: حذف ملف المقاول
    # لا يجوز أن يمحو الحجز نفسه.
    assigned_contractor = models.ForeignKey(
        "contractors.ContractorProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_bookings",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Booking"
        verbose_name_plural = "Bookings"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["customer", "-created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Booking {self.id} ({self.status})"

    # ⚠️ دالة عادية لا @property: حقل الحجز اسمه `property` (كما تنص
    #    المواصفة)، وهو يحجب الـbuiltin داخل جسم الصنف، فيصبح المُزخرِف
    #    @property إشارةً إلى الـForeignKey لا إلى الـbuiltin.
    def is_price_revealed(self):
        """
        هل ثُبِّتت اللقطة؟

        فحص حالة فقط — لا يحسب سعرًا ولا يستدعي محرّك التسعير.
        """
        return self.computed_price is not None


class BookingServiceSelection(models.Model):
    """
    سطر خدمة داخل حجز (جدول وسيط).

    ⚠️ لا حقل سعر هنا عمدًا (§36.2): السعر يُحسب ولا يُخزَّن لكل سطر،
       والعميل لا يرى تفصيلًا بالبنود — يرى إجماليًا واحدًا فقط.
       تخزين سعر لكل سطر كان سيصنع مصدر حقيقة ثانيًا يناقض اللقطة
       المجمَّدة على الحجز.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="service_selections",
    )

    # PROTECT: نوع خدمة مُشار إليه من حجوزات لا يُحذف فعليًا — الكتالوج
    # يستخدم التعطيل الناعم (is_active=False) أصلًا.
    service_type = models.ForeignKey(
        "services.ServiceType",
        on_delete=models.PROTECT,
        related_name="booking_selections",
    )

    room_count = models.PositiveIntegerField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Booking service selection"
        verbose_name_plural = "Booking service selections"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["booking"]),
        ]

    def __str__(self):
        return f"{self.service_type_id} × {self.room_count} rooms"


# ============================================================
# الإسناد التلقائي والعروض (Change Set §36.1، §36.5، §36.6)
# ============================================================


class DispatchOfferStatus(models.TextChoices):
    """
    حالة عرض الإسناد.

    ⚠️ EXPIRED و DECLINED يُعاملان بالسلوك نفسه (§36.6: "عدم الرد يُعامل
       معاملة الرفض")، لكنهما حالتان منفصلتان عمدًا: التمييز بين "رفض
       صراحةً" و"لم يردّ" ضروري لأي تقرير أداء لاحق للمقاولين.
    """

    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    DECLINED = "DECLINED", "Declined"
    EXPIRED = "EXPIRED", "Expired"


# مهلة الرد على العرض (§36.6)
OFFER_TTL_MINUTES = 60


class DispatchOffer(models.Model):
    """
    عرض إسناد موجَّه لمقاول واحد بخصوص حجز واحد.

    ⚠️ لا يُعاد عرض الحجز نفسه على المقاول نفسه مرتين — يضمنه قيد
       التفرد (booking, contractor) أدناه، لا منطق التطبيق وحده.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="dispatch_offers",
    )

    contractor = models.ForeignKey(
        "contractors.ContractorProfile",
        on_delete=models.CASCADE,
        related_name="dispatch_offers",
    )

    status = models.CharField(
        max_length=16,
        choices=DispatchOfferStatus.choices,
        default=DispatchOfferStatus.PENDING,
    )

    # 📌 المسافة المستخدمة فعليًا عند توليد هذا العرض — تُخزَّن ولا يُعاد
    #    حسابها عند القبول. لو أُعيد حسابها لحظة القبول وكان المقاول قد
    #    حدّث إحداثياته بينهما، لاختلف السعر عن المسافة التي بُني عليها
    #    العرض. اللقطة يجب أن تطابق العرض المقبول.
    distance_km = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Distance used for this offer; frozen, never recomputed.",
    )

    offered_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        verbose_name = "Dispatch offer"
        verbose_name_plural = "Dispatch offers"
        ordering = ["-offered_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["booking", "contractor"],
                name="unique_offer_per_booking_contractor",
            ),
        ]
        indexes = [
            # مهمة انتهاء الصلاحية ترشّح بالحالة والوقت معًا
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["booking", "status"]),
            models.Index(fields=["contractor", "status"]),
        ]

    def __str__(self):
        return f"Offer {self.id} -> {self.contractor_id} ({self.status})"

    def is_expired(self, now=None):
        """
        هل تجاوز العرض مهلته؟

        قراءة لحظية بحتة — لا تكتب شيئًا ولا تغيّر الحالة. تغيير الحالة
        إلى EXPIRED شأن المهمة الدورية وحدها.
        """
        moment = now or timezone.now()
        return self.expires_at <= moment

    def is_actionable(self, now=None):
        """
        هل يمكن الرد على هذا العرض الآن؟

        PENDING وغير منتهٍ. العرض المنتهي زمنيًا غير قابل للرد حتى قبل أن
        تحدّثه المهمة الدورية — الوقت هو الحَكَم، لا آخر تشغيل للمهمة.
        """
        return self.status == DispatchOfferStatus.PENDING and not self.is_expired(now)
