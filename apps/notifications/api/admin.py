"""
Notifications — لوحة التحكم.

    POST /api/admin/broadcasts                    رسالة عامة (عملاء / مقاولون / الكل)
    GET  /api/admin/broadcasts                    السجل مع إحصاءات الوصول
    GET  /api/admin/broadcasts/{id}
    GET  /api/admin/users/{user_id}/notifications ما وصل لمستخدم وحالة إرساله (للدعم)
"""

import uuid

from ninja import Query, Router

from apps.accounts.authentication import AdminJWTAuth

from ..services import broadcasts as bsvc
from .notifications import serialize
from .schemas import (
    AdminNotificationListOut,
    BroadcastIn,
    BroadcastListOut,
    BroadcastOut,
    ErrorOut,
)

router = Router(tags=["Admin — Notifications"], auth=AdminJWTAuth())


def _broadcast(b, with_stats=False):
    return {
        "id": b.id,
        "target": b.target,
        "title": b.title,
        "body": b.body,
        "recipients_count": b.recipients_count,
        "created_by_id": b.created_by_id,
        "created_at": b.created_at,
        "delivery": bsvc.broadcast_stats(b) if with_stats else {},
    }


@router.post(
    "/broadcasts",
    response={201: BroadcastOut, 422: ErrorOut},
    summary="Send a broadcast notification",
    description=(
        "Sends an announcement to every active account in `target` "
        "(`CUSTOMERS`, `CONTRACTORS` or `ALL_USERS`; admins never receive it). "
        "Each recipient gets it in their notification list and as a push. "
        "Audited."
    ),
)
def create_broadcast(request, payload: BroadcastIn):
    try:
        broadcast = bsvc.create_broadcast(
            request.user, payload.target.upper(), payload.title, payload.body, request=request
        )
    except bsvc.BroadcastError as exc:
        return 422, {"code": exc.code, "detail": str(exc)}
    return 201, _broadcast(broadcast)


@router.get("/broadcasts", response={200: BroadcastListOut}, summary="List broadcasts")
def list_broadcasts(request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    qs = bsvc.list_broadcasts()
    return 200, {"count": qs.count(), "items": [_broadcast(b) for b in qs[offset : offset + limit]]}


@router.get(
    "/broadcasts/{broadcast_id}",
    response={200: BroadcastOut, 404: ErrorOut},
    summary="Broadcast detail with delivery stats",
)
def retrieve_broadcast(request, broadcast_id: uuid.UUID):
    broadcast = bsvc.list_broadcasts().filter(pk=broadcast_id).first()
    if broadcast is None:
        return 404, {"code": "broadcast_not_found", "detail": "Broadcast not found."}
    return 200, _broadcast(broadcast, with_stats=True)


@router.get(
    "/users/{user_id}/notifications",
    response={200: AdminNotificationListOut},
    summary="A user's notifications with push delivery status",
)
def user_notifications(
    request, user_id: uuid.UUID, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    from ..services.notifications import list_notifications

    from apps.accounts.models import User

    user = User(pk=user_id)
    qs = list_notifications(user)
    return 200, {
        "count": qs.count(),
        "items": [
            {
                **serialize(n),
                "push_status": n.push_status,
                "push_attempts": n.push_attempts,
                "pushed_at": n.pushed_at,
                "push_error": n.push_error,
            }
            for n in qs[offset : offset + limit]
        ],
    }
