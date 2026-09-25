"""
python manage.py run_periodic_tasks

كل المهام الدورية في تشغيل واحد — لخدمة Railway Cron (cleaning_house-cron،
كل 5 دقائق)، بديلًا عن Celery Beat + Worker + Redis.

  1) انتهاء عروض الإسناد + التتابع للمقاول التالي + استعادة الحجوزات العالقة
  2) إصلاح ما بعد التأكيد (شحن/إسناد/مهمة/دفع للمقاول لم يحدث)
  3) إعادة إرسال الإشعارات العالقة
  4) تنظيف الإشعارات القديمة والأجهزة الخاملة

📌 كل مهمة معزولة: فشل واحدة يُسجَّل ولا يمنع البقية. رمز الخروج 1 إن فشلت
   أي مهمة، فيظهر التشغيل فاشلًا في Railway.
📌 كل المهام idempotent — تشغيلها مرتين أو تداخل تشغيلين آمن.
"""

import logging
import time

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


def _tasks():
    from apps.bookings.tasks import expire_pending_offers, repair_confirmed_bookings
    from apps.notifications.services import notifications as notifications_svc

    return [
        ("expire_pending_offers", expire_pending_offers),
        ("repair_confirmed_bookings", repair_confirmed_bookings),
        ("retry_pending_notifications", notifications_svc.retry_pending),
        ("cleanup_notifications", notifications_svc.cleanup),
    ]


class Command(BaseCommand):
    help = "Run every periodic task once (for a scheduler such as Railway Cron)."

    def handle(self, *args, **options):
        failures = []
        for name, task in _tasks():
            started = time.monotonic()
            try:
                result = task()
            except Exception:  # noqa: BLE001 — مهمة واحدة لا توقف البقية
                logger.exception("Periodic task failed: %s", name)
                failures.append(name)
                self.stderr.write(f"{name}: FAILED")
                continue
            self.stdout.write(f"{name}: {result} ({time.monotonic() - started:.2f}s)")
        if failures:
            raise CommandError(f"Failed tasks: {', '.join(failures)}")
