"""
ما بعد التنظيف — قرارات PO 2026-09-27:

- التقييم إلزامي: العميل يقيّم العامل (1–5) مرة واحدة بعد اكتمال المهمة،
  ولا يطلب تنظيفًا جديدًا قبل تقييم السابق.
- ضمان إعادة التنظيف 72 ساعة للخدمات التي تفعّل الإدارة ضمانها، والأدمن
  يقرر قبول الطلب أو رفضه، بلا حد لعدد الطلبات.
- الفاتورة بترقيم تسلسلي سنوي INV-YYYY-NNNNNN، بلا GST.
"""

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Review(models.Model):
    """تقييم العميل للعامل — واحد لكل حجز، ولا يُعدَّل."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.OneToOneField("bookings.Booking", on_delete=models.PROTECT, related_name="review")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reviews_given")
    # PROTECT: التقييم أثر دائم على العامل
    contractor = models.ForeignKey(
        "contractors.ContractorProfile", on_delete=models.PROTECT, related_name="reviews"
    )
    stars = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True, default="", max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["contractor", "-created_at"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(stars__gte=1, stars__lte=5), name="review_stars_1_to_5"
            ),
        ]

    def __str__(self):
        return f"{self.stars}★ for booking {self.booking_id}"


class RecleanArea(models.TextChoices):
    KITCHEN = "KITCHEN", "Kitchen"
    BATHROOMS = "BATHROOMS", "Bathrooms"
    BEDROOMS = "BEDROOMS", "Bedrooms"
    LIVING_AREAS = "LIVING_AREAS", "Living areas"
    WINDOWS = "WINDOWS", "Windows"
    OTHER = "OTHER", "Other"


class RecleanStatus(models.TextChoices):
    SUBMITTED = "SUBMITTED", "Submitted"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class RecleanRequest(models.Model):
    """
    طلب إعادة تنظيف ضمن الضمان. الأدمن يقرر.

    📌 لا حد للعدد، لكن طلب واحد مفتوح (SUBMITTED) لكل حجز في وقت واحد.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.ForeignKey("bookings.Booking", on_delete=models.PROTECT, related_name="reclean_requests")
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reclean_requests"
    )
    areas = models.JSONField(default=list)
    details = models.TextField(blank=True, default="", max_length=2000)
    status = models.CharField(max_length=16, choices=RecleanStatus.choices, default=RecleanStatus.SUBMITTED)
    decision_note = models.TextField(blank=True, default="", max_length=1000)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["booking"],
                condition=models.Q(status="SUBMITTED"),
                name="one_open_reclean_request_per_booking",
            ),
        ]

    def __str__(self):
        return f"Re-clean {self.status} for booking {self.booking_id}"


class InvoiceCounter(models.Model):
    """آخر رقم فاتورة لكل سنة — يُقفل صفه عند الإصدار فلا يتكرر رقم."""

    year = models.PositiveIntegerField(primary_key=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.year}: {self.last_number}"


class Invoice(models.Model):
    """
    فاتورة حجز مدفوع. الرقم يُصدر مرة واحدة ولا يتغير.

    📌 البنود والمبالغ لا تُخزَّن هنا: تُبنى من اللقطات المجمَّدة (الاقتباس
       والعرض المقبول والدفعة) عند كل قراءة، فلا مصدر حقيقة ثانٍ.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.OneToOneField("bookings.Booking", on_delete=models.PROTECT, related_name="invoice")
    number = models.CharField(max_length=32, unique=True)
    issued_at = models.DateTimeField()

    def __str__(self):
        return self.number
