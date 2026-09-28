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
    TestNotificationIn,
    TestNotificationOut,
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


@router.post(
    "/users/{user_id}/test-notification",
    response={200: TestNotificationOut, 404: ErrorOut},
    summary="Send a test push to one user and return the result (admin only)",
    description=(
        "Sends immediately (not after commit) and returns the real `push_status`: "
        "`SENT`, `PARTIAL`, `NO_DEVICE` (the app has not registered a device), or "
        "`PENDING`/`FAILED` with `push_error` (check the Firebase credentials). "
        "`priority: HIGH` uses the Android `offers` channel, `NORMAL` uses `general`. "
        "The notification is also stored in the user's inbox with `type: test`. "
        "Recorded in the audit log."
    ),
)
def send_test_notification(request, user_id: uuid.UUID, payload: TestNotificationIn):
    from ..services import notifications as nsvc

    try:
        notification, devices = nsvc.send_test_notification(
            request.user, user_id, priority=payload.priority, title=payload.title,
            body=payload.body, request=request,
        )
    except nsvc.NotificationTargetNotFoundError as exc:
        return 404, {"code": exc.code, "detail": str(exc)}
    return 200, {
        **serialize(notification),
        "push_status": notification.push_status,
        "push_attempts": notification.push_attempts,
        "pushed_at": notification.pushed_at,
        "push_error": notification.push_error,
        "devices": devices,
    }


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
