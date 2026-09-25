"""
Notification Service — الحفظ في القائمة، والإرسال المنبثق، والأجهزة.

📌 مسار كل إشعار:
     notify()  → صف Notification (القائمة) داخل معاملة المستدعي
               → بعد الـcommit: deliver() عبر Firebase لكل أجهزة المستخدم
   التوكن الميت يُحذف، والخطأ المؤقت يُعاد بمهمة دورية حتى
   NOTIFICATIONS_MAX_PUSH_ATTEMPTS محاولة.

📌 وضع الإرسال (NOTIFICATIONS_DELIVERY):
     "inline" — بعد الـcommit داخل الطلب نفسه (الافتراضي: لا يحتاج عامل
                Celery ولا Redis).
     "celery" — مهمة خلفية، حين يعمل عامل Celery على الاستضافة.

🔒 فشل الإرسال لا يُسقط أي عملية عمل: كل الاستثناءات تُبتلع وتُسجَّل.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from adapters.push_notification import PushMessage, get_push_adapter

from ..models import Audience, DeviceToken, Notification, Priority, PushStatus

logger = logging.getLogger(__name__)


class NotificationError(Exception):
    code = "notification_error"


class NotificationNotFoundError(NotificationError):
    code = "notification_not_found"


# ------------------------------------------------------------
# الأجهزة
# ------------------------------------------------------------
@transaction.atomic
def register_device(user, token, platform, app_version=""):
    """
    يسجّل توكن الجهاز أو يحدّثه. التوكن فريد: إن كان لحساب آخر يُنقل لهذا
    الحساب (الجهاز نفسه دخل بحساب مختلف).
    """
    device, created = DeviceToken.objects.update_or_create(
        token=token,
        defaults={"user": user, "platform": platform, "app_version": (app_version or "")[:32]},
    )
    return device, created


def unregister_device(user, token):
    """يحذف توكن هذا المستخدم (تسجيل الخروج). Idempotent."""
    deleted, _ = DeviceToken.objects.filter(user=user, token=token).delete()
    return bool(deleted)


# ------------------------------------------------------------
# الإنشاء والإرسال
# ------------------------------------------------------------
def notify(user, type, audience, title, body, data=None, priority=Priority.NORMAL, broadcast=None):
    """
    يحفظ الإشعار في قائمة المستخدم ويجدول إرساله بعد الـcommit.

    ⚠️ لا يُستدعى مباشرة من منطق العمل: النطاقات تستدعي أحداث
       services/events.py عبر hooks.emit_on_commit.
    """
    if user is None:
        return None
    notification = Notification.objects.create(
        user=user,
        audience=audience,
        type=type,
        title=title[:120],
        body=body[:500],
        data={k: str(v) for k, v in (data or {}).items() if v is not None},
        priority=priority,
        broadcast=broadcast,
    )
    schedule_delivery([notification.id])
    return notification


def schedule_delivery(notification_ids):
    ids = [str(i) for i in notification_ids]

    def run():
        mode = getattr(settings, "NOTIFICATIONS_DELIVERY", "inline")
        if mode == "celery":
            try:
                from ..tasks import deliver_notifications

                deliver_notifications.delay(ids)
                return
            except Exception:  # noqa: BLE001 — لا وسيط؟ يبقى PENDING للمهمة الدورية
                logger.exception("Could not enqueue push delivery; will retry later")
                return
        for notification_id in ids:
            deliver(notification_id)

    transaction.on_commit(run, robust=True)


def _message_for(notification):
    channels = getattr(settings, "NOTIFICATION_ANDROID_CHANNELS", {})
    return PushMessage(
        title=notification.title,
        body=notification.body,
        data={
            **notification.data,
            "type": notification.type,
            "audience": notification.audience,
            "notification_id": str(notification.id),
        },
        priority="high" if notification.priority == Priority.HIGH else "normal",
        android_channel_id=channels.get(notification.priority, ""),
    )


def deliver(notification_id):
    """
    يرسل إشعارًا واحدًا لكل أجهزة صاحبه ويحدّث حالة الإرسال.

    لا يرفع استثناءات: النتيجة تُسجَّل على الصف.
    """
    try:
        notification = Notification.objects.select_related("user").get(pk=notification_id)
    except Notification.DoesNotExist:
        return None
    if notification.push_status in (PushStatus.SENT, PushStatus.NO_DEVICE, PushStatus.PARTIAL):
        return notification

    tokens = list(notification.user.device_tokens.values_list("token", flat=True))
    notification.push_attempts += 1
    now = timezone.now()

    if not tokens:
        notification.push_status = PushStatus.NO_DEVICE
        notification.push_error = ""
    else:
        try:
            result = get_push_adapter().send(tokens, _message_for(notification))
        except Exception as exc:  # noqa: BLE001 — مزوّد غير مُهيّأ أو غير متاح
            logger.exception("Push provider unavailable (notification_id=%s)", notification.id)
            notification.push_status = _pending_or_failed(notification)
            notification.push_error = f"provider: {type(exc).__name__}"[:255]
        else:
            if result.invalid:
                DeviceToken.objects.filter(token__in=result.invalid).delete()
            if result.delivered and not result.failed:
                notification.push_status = PushStatus.SENT
                notification.push_error = ""
            elif result.delivered:
                notification.push_status = PushStatus.PARTIAL
                notification.push_error = "; ".join(sorted(set(result.failed.values())))[:255]
            elif result.failed:
                notification.push_status = _pending_or_failed(notification)
                notification.push_error = "; ".join(sorted(set(result.failed.values())))[:255]
            else:  # كل التوكنات ميتة وحُذفت
                notification.push_status = PushStatus.NO_DEVICE
                notification.push_error = ""
            if result.delivered:
                notification.pushed_at = now

    notification.save(update_fields=["push_status", "push_attempts", "push_error", "pushed_at"])
    return notification


def _pending_or_failed(notification):
    """خطأ مؤقت: يبقى PENDING لإعادة المحاولة حتى الحد الأقصى، ثم FAILED."""
    limit = getattr(settings, "NOTIFICATIONS_MAX_PUSH_ATTEMPTS", 5)
    return PushStatus.FAILED if notification.push_attempts >= limit else PushStatus.PENDING


def retry_pending(max_age_hours=24):
    """يعيد إرسال ما بقي PENDING (خطأ مؤقت أو وسيط غير متاح). يعيد العدد."""
    cutoff = timezone.now() - timezone.timedelta(hours=max_age_hours)
    grace = timezone.now() - timezone.timedelta(minutes=2)
    ids = list(
        Notification.objects.filter(
            push_status=PushStatus.PENDING, created_at__gte=cutoff, created_at__lt=grace
        ).values_list("id", flat=True)[:500]
    )
    for notification_id in ids:
        deliver(notification_id)
    return len(ids)


def cleanup():
    """
    يحذف إشعارات أقدم من NOTIFICATIONS_RETENTION_DAYS (90 — قرار PO)، وتوكنات
    أجهزة لم تظهر منذ NOTIFICATIONS_STALE_DEVICE_DAYS (توصية Firebase: 270).
    """
    now = timezone.now()
    retention = getattr(settings, "NOTIFICATIONS_RETENTION_DAYS", 90)
    stale = getattr(settings, "NOTIFICATIONS_STALE_DEVICE_DAYS", 270)
    notifications, _ = Notification.objects.filter(
        created_at__lt=now - timezone.timedelta(days=retention)
    ).delete()
    devices, _ = DeviceToken.objects.filter(
        last_seen_at__lt=now - timezone.timedelta(days=stale)
    ).delete()
    return {"notifications": notifications, "devices": devices}


# ------------------------------------------------------------
# القائمة داخل التطبيق
# ------------------------------------------------------------
def _for_user(user, audience=None):
    qs = Notification.objects.filter(user=user)
    if audience in (Audience.CUSTOMER, Audience.CONTRACTOR):
        # إشعارات الصفة المطلوبة + الإشعارات العامة لكل الصفات
        qs = qs.filter(audience__in=(audience, Audience.ALL))
    return qs


def list_notifications(user, unread_only=False, audience=None):
    qs = _for_user(user, audience)
    if unread_only:
        qs = qs.filter(read_at__isnull=True)
    return qs.order_by("-created_at")


def unread_count(user, audience=None):
    return _for_user(user, audience).filter(read_at__isnull=True).count()


def mark_read(user, notification_id):
    notification = Notification.objects.filter(pk=notification_id, user=user).first()
    if notification is None:
        raise NotificationNotFoundError("Notification not found.")
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    return notification


def mark_all_read(user, audience=None):
    return _for_user(user, audience).filter(read_at__isnull=True).update(read_at=timezone.now())
