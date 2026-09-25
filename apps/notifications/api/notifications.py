"""
Notifications API — التطبيق (عملاء ومقاولون).

    POST   /api/devices                          تسجيل/تحديث توكن FCM
    POST   /api/devices/unregister               إلغاؤه (تسجيل الخروج)
    GET    /api/notifications                    القائمة
    GET    /api/notifications/unread-count       عدد غير المقروء (أيقونة الجرس)
    POST   /api/notifications/{id}/read          تعليم كمقروء
    POST   /api/notifications/read-all           تعليم الكل كمقروء

📌 audience (اختياري): CUSTOMER أو CONTRACTOR — لحساب بالصفتين يعرض
   التطبيق قائمة الوضع الحالي. الرسائل العامة (ALL) تظهر في الوضعين.
"""

import uuid

from ninja import Query, Router

from apps.accounts.authentication import ActiveUserJWTAuth

from ..models import DevicePlatform
from ..services import notifications as svc
from .schemas import (
    DeviceIn,
    DeviceOut,
    DeviceUnregisterIn,
    ErrorOut,
    MarkAllOut,
    NotificationListOut,
    NotificationOut,
    UnreadCountOut,
)

devices_router = Router(tags=["Notifications"], auth=ActiveUserJWTAuth())
router = Router(tags=["Notifications"], auth=ActiveUserJWTAuth())


def serialize(n):
    return {
        "id": n.id,
        "type": n.type,
        "audience": n.audience,
        "title": n.title,
        "body": n.body,
        "data": n.data,
        "priority": n.priority,
        "read": n.read_at is not None,
        "read_at": n.read_at,
        "created_at": n.created_at,
    }


@devices_router.post(
    "",
    response={200: DeviceOut, 201: DeviceOut, 422: ErrorOut},
    summary="Register this device for push notifications",
    description=(
        "Call after login and whenever Firebase issues a new token "
        "(`onTokenRefresh`). Idempotent: the same token is updated, and a "
        "token previously registered by another account moves to this one."
    ),
)
def register_device(request, payload: DeviceIn):
    platform = payload.platform.upper()
    if platform not in DevicePlatform.values:
        return 422, {"code": "invalid_platform", "detail": "platform must be IOS, ANDROID or WEB."}
    device, created = svc.register_device(request.user, payload.token, platform, payload.app_version)
    return (201 if created else 200), device


@devices_router.post(
    "/unregister",
    response={204: None},
    summary="Stop push notifications on this device",
    description="Call on logout, before discarding the tokens. Idempotent.",
)
def unregister_device(request, payload: DeviceUnregisterIn):
    svc.unregister_device(request.user, payload.token)
    return 204, None


@router.get(
    "",
    response={200: NotificationListOut},
    summary="List my notifications",
    description=(
        "Newest first, kept for 90 days. `unread_only=true` filters; "
        "`audience=CUSTOMER|CONTRACTOR` shows one mode of a dual-role account "
        "(broadcasts appear in both). Paged with `limit`/`offset`."
    ),
)
def list_notifications(
    request,
    unread_only: bool = False,
    audience: str = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    qs = svc.list_notifications(request.user, unread_only=unread_only, audience=audience)
    return 200, {
        "count": qs.count(),
        "unread": svc.unread_count(request.user, audience),
        "items": [serialize(n) for n in qs[offset : offset + limit]],
    }


@router.get("/unread-count", response={200: UnreadCountOut}, summary="Unread notifications count")
def unread_count(request, audience: str = None):
    return 200, {"unread": svc.unread_count(request.user, audience)}


@router.post("/read-all", response={200: MarkAllOut}, summary="Mark all my notifications as read")
def read_all(request, audience: str = None):
    return 200, {"marked": svc.mark_all_read(request.user, audience)}


@router.post(
    "/{notification_id}/read",
    response={200: NotificationOut, 404: ErrorOut},
    summary="Mark one notification as read",
)
def read_one(request, notification_id: uuid.UUID):
    try:
        return 200, serialize(svc.mark_read(request.user, notification_id))
    except svc.NotificationNotFoundError as exc:
        return 404, {"code": exc.code, "detail": str(exc)}
