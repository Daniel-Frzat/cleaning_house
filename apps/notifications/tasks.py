"""مهام الإشعارات الخلفية — تعمل حين يوجد عامل Celery و Beat."""

from celery import shared_task

from .services import notifications as svc


@shared_task(name="notifications.deliver")
def deliver_notifications(notification_ids):
    for notification_id in notification_ids:
        svc.deliver(notification_id)
    return len(notification_ids)


@shared_task(name="notifications.retry_pending")
def retry_pending_notifications():
    return svc.retry_pending()


@shared_task(name="notifications.cleanup")
def cleanup_notifications():
    return svc.cleanup()
