"""
تسجيل أفعال الإدارة — نقطة الكتابة الوحيدة في AuditLog.

الاستخدام من أي نطاق:
    from apps.audit.services.audit import record
    record(request.user, "support_request.status", target=obj,
           details={"from": "SUBMITTED", "to": "RESOLVED"}, request=request)

📌 يُستدعى داخل معاملة الفعل نفسه: فعل تراجع لا يترك أثرًا كاذبًا، وفعل
   نجح لا يمر بلا أثر.
"""

import logging

from ..models import AuditLog

logger = logging.getLogger(__name__)


def _client_ip(request):
    if request is None:
        return None
    from apps.accounts.api.auth import _client_ip as client_ip

    return client_ip(request)


def record(actor, action, target=None, details=None, request=None, ip=None):
    """يسجّل فعلًا إداريًا ويعيد السجل."""
    label = ""
    if actor is not None and getattr(actor, "is_authenticated", False):
        label = actor.email or actor.phone or str(actor.pk)
    else:
        actor = None

    entry = AuditLog.objects.create(
        actor=actor,
        actor_label=label,
        action=action,
        target_type=type(target).__name__ if target is not None else "",
        target_id=str(target.pk) if target is not None else "",
        details=details or {},
        ip_address=ip or _client_ip(request),
    )
    logger.info("Audit: %s by %s on %s:%s", action, label, entry.target_type, entry.target_id)
    return entry


def list_entries(actor=None, action=None, target_type=None, target_id=None):
    """قراءة السجل للإدارة — الأحدث أولًا، بمرشِّحات اختيارية."""
    qs = AuditLog.objects.select_related("actor")
    if actor is not None:
        qs = qs.filter(actor_id=actor)
    if action:
        qs = qs.filter(action=action)
    if target_type:
        qs = qs.filter(target_type=target_type)
    if target_id:
        qs = qs.filter(target_id=str(target_id))
    return qs
