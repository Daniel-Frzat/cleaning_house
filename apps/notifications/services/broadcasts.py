"""
الرسائل العامة من الإدارة (قرار PO — 2026-09-25).

كل مستلم يحصل على Notification في قائمته ثم إشعارًا منبثقًا. المستلمون:
الحسابات النشطة فقط، ولا تصل للأدمن.

⚠️ وضع "inline" يرسل للجميع داخل الطلب — مقبول لأعداد صغيرة. مع جمهور
   كبير فعّل NOTIFICATIONS_DELIVERY=celery (عامل Celery + Redis).
"""

from django.db import transaction
from django.db.models import Q

from apps.accounts.models import User, UserStatus
from apps.accounts.roles import ConfirmedRole

from ..models import Audience, Broadcast, Notification
from .notifications import schedule_delivery

AUDIENCE_FOR_TARGET = {
    Broadcast.Target.CUSTOMERS: Audience.CUSTOMER,
    Broadcast.Target.CONTRACTORS: Audience.CONTRACTOR,
    Broadcast.Target.ALL_USERS: Audience.ALL,
}


class BroadcastError(Exception):
    code = "broadcast_invalid"


def recipients_for(target):
    active = User.objects.filter(is_active=True, status=UserStatus.ACTIVE).exclude(
        role=ConfirmedRole.ADMIN
    )
    contractor = Q(role=ConfirmedRole.CONTRACTOR) | Q(is_contractor=True)
    if target == Broadcast.Target.CUSTOMERS:
        return active.filter(role=ConfirmedRole.CUSTOMER)
    if target == Broadcast.Target.CONTRACTORS:
        return active.filter(contractor)
    if target == Broadcast.Target.ALL_USERS:
        return active
    raise BroadcastError("Unknown target.")


@transaction.atomic
def create_broadcast(actor, target, title, body, request=None):
    from apps.audit.services.audit import record

    title, body = (title or "").strip(), (body or "").strip()
    if not title or not body:
        raise BroadcastError("Title and body are required.")
    if target not in Broadcast.Target.values:
        raise BroadcastError("Unknown target.")

    broadcast = Broadcast.objects.create(created_by=actor, target=target, title=title, body=body)
    audience = AUDIENCE_FOR_TARGET[target]
    rows = [
        Notification(
            user_id=user_id,
            audience=audience,
            type="broadcast",
            title=title[:120],
            body=body[:500],
            data={"broadcast_id": str(broadcast.id)},
            broadcast=broadcast,
        )
        for user_id in recipients_for(target).values_list("id", flat=True)
    ]
    Notification.objects.bulk_create(rows, batch_size=1000)
    broadcast.recipients_count = len(rows)
    broadcast.save(update_fields=["recipients_count"])
    record(
        actor, "broadcast.create", target=broadcast,
        details={"target": target, "title": title, "recipients": len(rows)}, request=request,
    )
    schedule_delivery([row.id for row in rows])
    return broadcast


def list_broadcasts():
    return Broadcast.objects.select_related("created_by")


def broadcast_stats(broadcast):
    from django.db.models import Count

    rows = broadcast.notifications.values("push_status").annotate(n=Count("id"))
    stats = {row["push_status"]: row["n"] for row in rows}
    stats["read"] = broadcast.notifications.filter(read_at__isnull=False).count()
    return stats
