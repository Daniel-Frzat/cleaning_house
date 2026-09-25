import uuid
from datetime import datetime
from typing import Optional

from ninja import Schema
from pydantic import Field


class ErrorOut(Schema):
    code: str
    detail: str


class DeviceIn(Schema):
    token: str = Field(..., min_length=10, max_length=512)
    platform: str = Field(..., description="IOS | ANDROID | WEB")
    app_version: str = Field("", max_length=32)


class DeviceOut(Schema):
    id: uuid.UUID
    platform: str
    app_version: str = ""
    created_at: datetime
    last_seen_at: datetime


class DeviceUnregisterIn(Schema):
    token: str = Field(..., min_length=10, max_length=512)


class NotificationOut(Schema):
    id: uuid.UUID
    type: str
    audience: str
    title: str
    body: str
    data: dict = {}
    priority: str
    read: bool
    read_at: Optional[datetime] = None
    created_at: datetime


class NotificationListOut(Schema):
    count: int
    unread: int
    items: list[NotificationOut]


class UnreadCountOut(Schema):
    unread: int


class MarkAllOut(Schema):
    marked: int


class BroadcastIn(Schema):
    target: str = Field(..., description="CUSTOMERS | CONTRACTORS | ALL_USERS")
    title: str = Field(..., min_length=1, max_length=120)
    body: str = Field(..., min_length=1, max_length=500)


class BroadcastOut(Schema):
    id: uuid.UUID
    target: str
    title: str
    body: str
    recipients_count: int
    created_by_id: Optional[uuid.UUID] = None
    created_at: datetime
    delivery: dict = {}


class BroadcastListOut(Schema):
    count: int
    items: list[BroadcastOut]


class AdminNotificationOut(NotificationOut):
    push_status: str
    push_attempts: int
    pushed_at: Optional[datetime] = None
    push_error: str = ""


class AdminNotificationListOut(Schema):
    count: int
    items: list[AdminNotificationOut]
