"""
Notifications Domain — أجهزة الإشعارات، قائمة الإشعارات، والرسائل العامة.

📌 Notification هو **القائمة داخل التطبيق** وسجل الإرسال معًا: يُحفظ أولًا
   ثم يُرسل كإشعار منبثق. فشل Firebase لا يُخفي الإشعار من القائمة.

📌 audience: الحساب الواحد قد يكون عميلًا ومقاولًا، فكل إشعار يحمل الصفة
   التي يخصها، فيفتح التطبيق الشاشة الصحيحة (وقد يعرض القائمة حسب الوضع).
"""

import uuid

from django.conf import settings
from django.db import models


class DevicePlatform(models.TextChoices):
    IOS = "IOS", "iOS"
    ANDROID = "ANDROID", "Android"
    WEB = "WEB", "Web"


class DeviceToken(models.Model):
    """
    توكن FCM لجهاز واحد.

    🔒 التوكن فريد عالميًا: جهاز انتقل لحساب آخر (خروج ثم دخول بحساب ثانٍ)
       يُنقل صفه إلى الحساب الجديد، فلا يستلم الأول إشعارات الثاني.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="device_tokens"
    )
    token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=16, choices=DevicePlatform.choices)
    app_version = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen_at"]
        indexes = [models.Index(fields=["user", "-last_seen_at"])]

    def __str__(self):
        return f"{self.get_platform_display()} device of {self.user_id}"


class Audience(models.TextChoices):
    CUSTOMER = "CUSTOMER", "Customer"
    CONTRACTOR = "CONTRACTOR", "Contractor"
    ALL = "ALL", "All"


class PushStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SENT = "SENT", "Sent"
    PARTIAL = "PARTIAL", "Sent to some devices"
    NO_DEVICE = "NO_DEVICE", "No registered device"
    FAILED = "FAILED", "Failed"


class Priority(models.TextChoices):
    HIGH = "HIGH", "High"
    NORMAL = "NORMAL", "Normal"


class Broadcast(models.Model):
    """رسالة عامة من الإدارة لشريحة كاملة — كل مستلم يحصل على Notification."""

    class Target(models.TextChoices):
        CUSTOMERS = "CUSTOMERS", "All customers"
        CONTRACTORS = "CONTRACTORS", "All contractors"
        ALL_USERS = "ALL_USERS", "All customers and contractors"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    target = models.CharField(max_length=16, choices=Target.choices)
    title = models.CharField(max_length=120)
    body = models.CharField(max_length=500)
    recipients_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Broadcast to {self.get_target_display()}: {self.title}"


class Notification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    audience = models.CharField(max_length=16, choices=Audience.choices)
    # رمز الحدث بصيغة نطاق.حدث — مثل "offer.new" (راجع services/events.py)
    type = models.CharField(max_length=48, db_index=True)
    title = models.CharField(max_length=120)
    body = models.CharField(max_length=500)
    # معرّفات للتنقل داخل التطبيق (booking_id, offer_id...) — نصوص فقط
    data = models.JSONField(default=dict, blank=True)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.NORMAL)
    broadcast = models.ForeignKey(
        Broadcast, on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications"
    )

    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # سجل الإرسال المنبثق
    push_status = models.CharField(
        max_length=16, choices=PushStatus.choices, default=PushStatus.PENDING, db_index=True
    )
    push_attempts = models.PositiveSmallIntegerField(default=0)
    pushed_at = models.DateTimeField(null=True, blank=True)
    push_error = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "read_at"]),
        ]

    def __str__(self):
        return f"{self.type} → {self.user_id}"
