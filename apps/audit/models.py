"""
Audit Log — سجل أفعال الإدارة.

كل فعل إداري يغيّر حالة (مراجعة وثيقة، إيقاف حساب، إعادة تعيين كلمة سر،
تغيير حالة طلب دعم، مطابقة دفعة...) يُسجَّل هنا: من فعل، ماذا، على أي كائن،
ومتى ومن أي عنوان.

🔒 للإضافة فقط: لا تعديل ولا حذف — لا عبر الـAPI ولا من لوحة الإدارة.
   السجل الذي يمكن تحريره ليس سجلًا.

⚠️ details لا يحمل أسرارًا: لا كلمات سر (ولا المؤقتة)، ولا رموز OTP، ولا
   توكنات. يحمل ما تغيّر وقيمه غير الحساسة فقط.
"""

import uuid

from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # SET_NULL: حذف حساب لا يمحو أثر أفعاله؛ actor_label يحفظ هويته نصًا
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    actor_label = models.CharField(max_length=255, blank=True)

    # بصيغة نطاق.فعل — مثل "admin_account.create" أو "support_request.status"
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=64, blank=True, db_index=True)
    target_id = models.CharField(max_length=64, blank=True, db_index=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Audit log entry"
        verbose_name_plural = "Audit log"

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_label} {self.action}"
