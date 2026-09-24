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

import secrets
import uuid

from django.core.validators import MaxLengthValidator
from django.db import IntegrityError, models, transaction
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


class DispatchStatus(models.TextChoices):
    """
    تقدّم البحث عن مقاول — مقروء للعميل.

    📌 سبب الوجود: الحجز يبقى PENDING سواء كان البحث جاريًا أو انتهى بلا
       مرشَّح، فلم يكن العميل يستطيع التمييز بين "ما زلنا نبحث" و"لا يوجد
       عامل متاح". الحالتان كانتا متطابقتين تمامًا في الرد.

    ⚠️ حقل عرض لا قرار: لا يغيّر status ولا يُلغي حجزًا ولا يُطلق إعادة
       محاولة. القرار المفتوح #16 يبقى مفتوحًا كما هو.

    SEARCHING          : عرض نشط لدى مقاول، أو بحث لم يبدأ بعد.
    NO_CONTRACTOR      : جُرِّب كل مرشَّح مؤهَّل ولم يبقَ أحد.
    ASSIGNED           : قَبِل مقاول — البحث انتهى.
    """

    SEARCHING = "SEARCHING", "Searching"
    NO_CONTRACTOR = "NO_CONTRACTOR", "No contractor available"
    ASSIGNED = "ASSIGNED", "Assigned"


class DistanceSource(models.TextChoices):
    """
    من أين جاءت المسافة المستعملة في التسعير (§9).

    📌 يُخزَّن على العرض حتى لا يكون الانتقال بين المصدرين صامتًا: تدقيق
       الإدارة يجب أن يرى بأي أساس حُسب سعرٌ ما.

    ROUTE     : مسافة طريق فعلية من مزوّد الاتجاهات.
    HAVERSINE : مسافة خط مستقيم محلية — احتياطي بإعداد صريح.
    """

    ROUTE = "ROUTE", "Route distance (directions provider)"
    HAVERSINE = "HAVERSINE", "Straight-line distance (fallback)"


# حدّ ملاحظات الوصول: تعليمات وصول لا رسالة. الحدّ يمنع إساءة استعمال
# الحقل كقناة تواصل، ويبقى واسعًا لأي تعليمات معقولة.
ACCESS_NOTES_MAX_LENGTH = 500


# ------------------------------------------------------------
# الرقم المرجعي المقروء (public_reference)
# ------------------------------------------------------------
# 📌 سبب الوجود: الـUUID لا يُقرأ في مكالمة دعم ولا يُملى هاتفيًا. هذا
#    الحقل واجهة الحجز أمام الإنسان وحده — والمعرّف الداخلي يبقى الـUUID،
#    ولا يقبل أي مسار قيمةً غيره.
#
# 🔒 غير تسلسلي: التسلسل يكشف حجم الأعمال (الحجز CLN-000042 يخبر من رآه
#    أنه الحجز الثاني والأربعون)، ويسمح بتخمين مراجع حجوزات أخرى.
#
# ⚠️ secrets لا random — نفس قرار apps/accounts/services/otp.py: المولّد
#    الافتراضي في random ليس آمنًا تشفيريًا، وحالة مولّده قابلة للاستنتاج
#    من مخرجات سابقة. التكلفة هنا صفر والفارق أمني.
PUBLIC_REFERENCE_PREFIX = "CLN"

# 🔒 أبجدية بلا B و I و O و S و Z: الحقل يُملى صوتيًا في الدعم، وهذه
#    الحروف تُخلط بـ8 و1 و0 و5 و2. إسقاطها يمنع الخلط من أصله بدل
#    معالجته لاحقًا.
PUBLIC_REFERENCE_ALPHABET = "0123456789ACDEFGHJKLMNPQRTUVWXY"

PUBLIC_REFERENCE_LENGTH = 6

# عدد محاولات التوليد قبل الاستسلام عند تصادم نادر. القيد الفريد في
# قاعدة البيانات هو الحَكَم لا الفحص المسبق — الفحص المسبق يترك نافذة
# سباق بينه وبين الحفظ.
PUBLIC_REFERENCE_MAX_ATTEMPTS = 5


def generate_public_reference():
    """
    يولّد مرجعًا بالشكل CLN-7F3K9Q.

    لا يفحص التفرد: ذلك شأن القيد الفريد وحلقة إعادة المحاولة في save().
    """
    suffix = "".join(
        secrets.choice(PUBLIC_REFERENCE_ALPHABET)
        for _ in range(PUBLIC_REFERENCE_LENGTH)
    )
    return f"{PUBLIC_REFERENCE_PREFIX}-{suffix}"


class Booking(models.Model):
    """
    حجز يقدّمه عميل على أحد عقاراته.

    ⚠️ قيدان لا تفرضهما قاعدة البيانات ويعيشان في طبقة الخدمة:
       1) customer دوره CUSTOMER (الدور حقل على جدول آخر قابل للتغيير).
       2) property مملوك لنفس العميل — يُفحص عبر
          properties/services/properties.py:assert_owns.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # 📌 الرقم المرجعي المقروء — للعرض والدعم وحدهما. يُولَّد مرة واحدة
    #    عند أول حفظ ولا يتغيّر بعدها.
    # ⚠️ editable=False: لا يصل من أي نموذج ولا من لوحة الإدارة. المرجع
    #    الذي يتغيّر بعد أن رآه العميل ليس مرجعًا.
    # ⚠️ ليس معرّفًا: لا يقبله أي مسار في الـAPI، والبحث الداخلي يبقى
    #    على الـUUID.
    public_reference = models.CharField(
        max_length=16,
        unique=True,
        editable=False,
        db_index=True,
        help_text="Human-readable booking number (e.g. CLN-7F3K9Q); display only.",
    )

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

    # ------------------------------------------------------------
    # موعد الزيارة
    # ------------------------------------------------------------
    # 📌 نقطة زمنية واحدة بالـUTC — لا نطاق ولا مدة ولا معرّف فترة.
    #    المدة وسياسة التكرار بندان مفتوحان (🟡 #17) ولا يُفترضان هنا.
    #
    # ⚠️ nullable على مستوى قاعدة البيانات لتوافق الصفوف السابقة وحدها.
    #    الإنشاء الجديد عبر الـAPI يفرضه إلزاميًا في الـschema.
    scheduled_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Visit date and time, stored in UTC.",
    )

    # 🔒 للعرض فقط: اسم IANA مشتق من ولاية عنوان العقار وقت الإنشاء
    #    (services/timezone.py). لا يدخل أي حساب لاحق — التحويل للعرض
    #    يتم منه، والتسعير والإسناد لا يقرآنه إطلاقًا.
    customer_timezone = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="IANA timezone derived from the property address; display only.",
    )

    # ------------------------------------------------------------
    # تقدّم البحث عن مقاول — للعرض وحده
    # ------------------------------------------------------------
    # 📌 يفصل "ما زلنا نبحث" عن "لا يوجد عامل": الحالتان كانتا كلتاهما
    #    status=PENDING بلا أي فارق مرئي.
    #
    # ⚠️ لا يحلّ محل status ولا يتحكم بأي منطق: محرّك الإسناد يكتبه،
    #    ولا يقرأه أحد سوى طبقة العرض.
    dispatch_status = models.CharField(
        max_length=16,
        choices=DispatchStatus.choices,
        default=DispatchStatus.SEARCHING,
        help_text="Progress of the contractor search; display only.",
    )

    # آخر مرة حاول فيها محرّك الإسناد إيجاد مقاول — يسمح للعميل بعرض
    # "نبحث منذ ..." بلا تخمين.
    last_dispatch_attempt_at = models.DateTimeField(null=True, blank=True)

    # 📌 جولة الإسناد الحالية (§4). تُرفَّع عند كل إعادة محاولة، فيُعاد
    #    النظر في كل المقاولين من جديد.
    # ⚠️ لماذا رقم جولة بدل حذف العروض القديمة: الحذف يُفقد سجلّ من رفض
    #    ومتى، وهو ما تحتاجه تقارير أداء المقاولين لاحقًا. الترقيم يحتفظ
    #    بكل شيء ويجعل الاستبعاد محصورًا بالجولة الجارية.
    dispatch_round = models.PositiveIntegerField(
        default=1,
        help_text="Incremented on each retry; offers are scoped to a round.",
    )

    # ملاحظات وصول يكتبها العميل بنفسه لهذه الزيارة: "المفتاح تحت السجادة"،
    # "الكلب في الحديقة"، "الجرس معطّل — اطرق".
    #
    # 🔒 لا تُكشف إلا للمقاول المُسنَد بعد القبول (api/bookings.py،
    #    api/jobs.py). المقاول الذي يُعرض عليه الحجز ولم يقبل بعد لا يراها:
    #    قد تحوي مكان مفتاح المنزل، وعرضها على من قد يرفض ولن يزور المنزل
    #    أبدًا كشفٌ بلا مقابل.
    #
    # ⚠️ نصّ حرّ من العميل — لا يُفسَّر ولا يُبنى عليه أي منطق. ليس بديلًا
    #    عن العنوان ولا عن الإحداثيات: الإسناد يقرأ الإحداثيات وحدها.
    # ⚠️ MaxLengthValidator صراحةً: max_length على TextField يؤثّر في
    #    واجهات النماذج فقط ولا يولّد أي تحقق في full_clean — الحدّ بدونه
    #    كان سيمرّ صامتًا.
    access_notes = models.TextField(
        blank=True,
        default="",
        validators=[MaxLengthValidator(ACCESS_NOTES_MAX_LENGTH)],
        help_text=(
            "Customer's own arrival instructions for this visit. "
            "Visible only to the assigned contractor."
        ),
    )

    # ------------------------------------------------------------
    # الطلب الفوري (§3) واقتباس السعر (§5، §6)
    # ------------------------------------------------------------
    # 📌 لحظة الطلب. منفصل عن created_at عمدًا: الأخير تفصيل تخزين يتغيّر
    #    معناه لو أُنشئ صف بمسار آخر (استيراد، أمر إداري)، وهذا يعني
    #    "متى طلب العميل عاملًا" ويُستعمل في العرض وفي حساب مدة البحث.
    requested_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text="When the customer requested a cleaner (on-demand).",
    )

    # الاقتباس الذي وافق عليه العميل. SET_NULL: حذف اقتباس قديم لا يجوز
    # أن يمحو حجزًا، والسقف المجمَّد أدناه يبقى محفوظًا على الحجز نفسه.
    quote = models.ForeignKey(
        "bookings.BookingQuote",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
    )

    # 🔒 السقف الذي وافق عليه العميل. الشحن لا يتجاوزه أبدًا — يُفحص قبل
    #    أي محاولة شحن، ويُرفض الشحن عند المخالفة بدل تجاوزه بصمت (§7).
    max_total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Customer-approved ceiling, frozen from the quote.",
    )

    # نسخة قواعد التسعير المستعملة — تدقيق إداري (§9).
    pricing_version = models.PositiveIntegerField(null=True, blank=True)

    currency = models.CharField(max_length=3, default="AUD")

    # 📌 مرجع طريقة الدفع لدى المزوّد. رمز مبهم لا بيانات بطاقة (§6).
    # 🔒 لا PAN ولا CVC ولا أي بيان حسّاس — يُخزَّن ما يعيده المزوّد فقط.
    payment_method_reference = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Opaque provider token for the selected method. Never card data.",
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
        return f"Booking {self.public_reference or self.id} ({self.status})"

    def save(self, *args, **kwargs):
        """
        يضمن وجود مرجع مقروء عند أول حفظ.

        ⚠️ التفرد يُفرض بالقيد في قاعدة البيانات لا بفحص مسبق: بين
           `exists()` والحفظ نافذة سباق قد يكتب فيها طلب آخر المرجع نفسه.
           لذا نحاول الحفظ ونلتقط IntegrityError ثم نولّد مرجعًا جديدًا.

        ⚠️ المحاولة الأخيرة تُعيد رفع الخطأ: الاستسلام الصامت كان سيترك
           حجزًا بلا مرجع. واحتمال خمسة تصادمات متتالية يقارب الصفر
           (فضاء 31^6 ≈ 887 مليون)، فبلوغه يعني خللًا يستحق الظهور.

        📌 الالتفاف بـtransaction.atomic لكل محاولة إلزامي: IntegrityError
           يُفسد المعاملة الجارية في PostgreSQL، فبدون نقطة حفظ داخلية
           تفشل المحاولة التالية بـTransactionManagementError لا بتصادم.

        ⚠️ المرجع يُولَّد مرة واحدة فقط: الصف الذي يحمل مرجعًا يُحفظ
           كالمعتاد بلا أي تدخّل، فلا يتغيّر مرجع حجز قائم أبدًا.
        """
        if self.public_reference:
            return super().save(*args, **kwargs)

        last_attempt = PUBLIC_REFERENCE_MAX_ATTEMPTS - 1

        for attempt in range(PUBLIC_REFERENCE_MAX_ATTEMPTS):
            self.public_reference = generate_public_reference()
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                if attempt == last_attempt:
                    raise
                # 📌 المحاولة التالية إدراج لا تحديث: بعد فشل الحفظ يضبط
                #    Django الحالة كأن الصف حُفظ، فيتحول الحفظ التالي إلى
                #    UPDATE على صف غير موجود ويمر بلا أثر.
                self._state.adding = True

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
    # 📌 قَبِل المقاول والشحن جارٍ — الحجز **ليس** مؤكَّدًا بعد (§12).
    #    هذه الحالة تُوقف العرض على مقاولين آخرين دون أن تُسنِد أحدًا.
    ACCEPTED_PENDING_PAYMENT = "ACCEPTED_PENDING_PAYMENT", "Accepted, awaiting payment"
    ACCEPTED = "ACCEPTED", "Accepted"
    DECLINED = "DECLINED", "Declined"
    EXPIRED = "EXPIRED", "Expired"


# مهلة الرد على العرض (§36.6).
# ⚠️ احتياطي فقط: القيمة الفعلية تأتي من PricingConfig.dispatch_offer_ttl_seconds
#    ويضبطها الداشبورد (§11). يبقى هنا لتوافق الكود القائم ولحالة تعذّر
#    قراءة الإعداد.
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

    # 32: يتّسع لـACCEPTED_PENDING_PAYMENT (24 حرفًا) مع هامش.
    status = models.CharField(
        max_length=32,
        choices=DispatchOfferStatus.choices,
        default=DispatchOfferStatus.PENDING,
    )

    # جولة الإسناد التي وُلد فيها هذا العرض (§4).
    dispatch_round = models.PositiveIntegerField(default=1)

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

    # ------------------------------------------------------------
    # لقطة التسعير المجمَّدة على العرض (§10)
    # ------------------------------------------------------------
    # 📌 سبب الوجود: المقاول يجب أن يرى أرباحه **قبل** أن يضغط قبول.
    #    السعر كان يُحسب بعد القبول، فكان يقبل على المجهول.
    #
    # ⚠️ تُجمَّد كلها لحظة إنشاء العرض ولا يُعاد حسابها: لو تحرّك المقاول
    #    أو غيّرت الإدارة الأسعار بين العرض والقبول، يبقى ما رآه هو ما
    #    يُشحن ويُدفع له.
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Frozen final total for this offer; never recomputed.",
    )

    # 🔒 صفر عمولة: يساوي total_amount دائمًا. حقل منفصل لأن المعنى مختلف
    #    (ما يُدفع للمقاول) ولأن أي عمولة مستقبلية تُغيَّر هنا صراحةً.
    contractor_earnings = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Frozen contractor payout for this offer; zero commission.",
    )

    travel_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Admin-only breakdown component.",
    )

    services_total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Admin-only breakdown component.",
    )

    currency = models.CharField(max_length=3, default="AUD")

    pricing_version = models.PositiveIntegerField(null=True, blank=True)

    # 📌 مصدر المسافة (§9): ROUTE من مزوّد الاتجاهات، أو HAVERSINE احتياطًا
    #    بإعداد صريح. يُخزَّن حتى لا يكون الانتقال بينهما صامتًا في التدقيق.
    distance_source = models.CharField(
        max_length=16,
        choices=DistanceSource.choices,
        default=DistanceSource.HAVERSINE,
    )

    # زمن الوصول المقدَّر من مزوّد الاتجاهات — null حين لا مزوّد.
    # ⚠️ لا يُختلق أبدًا: haversine مسافة لا زمن.
    eta_seconds = models.PositiveIntegerField(null=True, blank=True)

    offered_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        verbose_name = "Dispatch offer"
        verbose_name_plural = "Dispatch offers"
        ordering = ["-offered_at"]
        constraints = [
            # 📌 التفرد لكل جولة لا للأبد: لا يُعرض الحجز مرتين على
            #    المقاول نفسه داخل الجولة الواحدة، لكن إعادة المحاولة
            #    جولة جديدة يجوز فيها عرضه عليه ثانيةً (§4).
            models.UniqueConstraint(
                fields=["booking", "contractor", "dispatch_round"],
                name="unique_offer_per_booking_contractor_round",
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

    def is_reserved(self):
        """
        هل هذا العرض يحجز الحجز حاليًا؟

        📌 القبول بانتظار الدفع يحجز الحجز: لا يُعرض على مقاول آخر، ولا
           يُسنَد أحد بعد. المحاولة والمحاولة المعادة تعيشان في هذه الحالة.
        """
        return self.status in (
            DispatchOfferStatus.ACCEPTED_PENDING_PAYMENT,
            DispatchOfferStatus.ACCEPTED,
        )


# ============================================================
# اقتباس السعر قبل الحجز (§5)
# ============================================================
# 📌 سبب الوجود: شاشة المراجعة تعرض "Up to A$152.00" قبل أن يوجد أي
#    مقاول. السعر النهائي لا يُعرف قبل القبول (المسافة مجهولة)، لكن
#    السقف يُعرف: مجموع الخدمات + أقصى رسم مسافة.
#
# 🔒 السقف هو ما يوافق عليه العميل. الشحن اللاحق لا يتجاوزه أبدًا —
#    مضمون رياضيًا لأن رسم المسافة مسقوف.
#
# ⚠️ الاقتباس **غير قابل للتعديل** بعد إنشائه: أسعار الخدمات تُجمَّد فيه،
#    فتغيير الداشبورد للأسعار بين شاشة المراجعة وقبول المقاول لا يمسّ ما
#    وافق عليه العميل.

# صلاحية الاقتباس. قصيرة عمدًا: الطلب فوري، والسقف يعكس أسعارًا قد
# تتغيّر. طويلة بما يكفي لإكمال شاشة المراجعة بلا عجلة.
QUOTE_TTL_MINUTES = 30


class BookingQuote(models.Model):
    """
    لقطة تسعير مجمَّدة يوافق عليها العميل قبل طلب عامل.

    🔒 يُستهلك مرة واحدة: الحجز الذي يستعمله يُربط به، ولا يُعاد استعماله
       لحجز ثانٍ — وإلا لأمكن تثبيت سعر قديم إلى الأبد.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    customer = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="booking_quotes",
    )

    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="booking_quotes",
    )

    # 📌 اختيارات الخدمات وأسعارها لحظة الاقتباس — مجمَّدة كـJSON:
    #    [{"service_type_id": str, "name": str, "room_count": int,
    #      "room_price": str, "base_price": str}, ...]
    # ⚠️ الأسماء تُحفظ كذلك (§9): الخدمة قد يُعاد تسميتها لاحقًا، والفاتورة
    #    يجب أن تعرض ما رآه العميل.
    service_snapshot = models.JSONField(
        help_text="Frozen service selections, names and prices at quote time."
    )

    # مجموع الخدمات المجمَّد — لا يُعاد حسابه من الكتالوج أبدًا.
    services_total = models.DecimalField(max_digits=10, decimal_places=2)

    # 🔒 السقف الذي يوافق عليه العميل ويُشحن ضمنه.
    maximum_total = models.DecimalField(max_digits=10, decimal_places=2)

    currency = models.CharField(max_length=3, default="AUD")

    # نسخة قواعد التسعير المستعملة — للتدقيق الإداري (§9).
    pricing_version = models.PositiveIntegerField()

    expires_at = models.DateTimeField(db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Booking quote"
        verbose_name_plural = "Booking quotes"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["customer", "-created_at"]),
        ]

    def __str__(self):
        return f"Quote {self.id} (max {self.maximum_total} {self.currency})"

    def is_expired(self, now=None):
        """قراءة لحظية — لا تكتب ولا تغيّر حالة."""
        return self.expires_at <= (now or timezone.now())
