"""
Celery App.

المهام تعيش في tasks.py داخل كل Domain وتُكتشف تلقائيًا، وجدولها الدوري
في CELERY_BEAT_SCHEDULE (config/settings/base.py):
  - bookings.expire_pending_offers   كل دقيقة — انتهاء العروض + التتابع
                                     + استعادة الحجوزات العالقة
  - bookings.repair_confirmed_bookings كل 5 دقائق — مهمة/شحن/دفع للمقاول
                                     لم تُنفَّذ بعد تأكيد الحجز
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("cleaning_house")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    """Health-check task فقط — للتأكد من أن Celery+Redis يعملان بشكل صحيح."""
    print(f"Request: {self.request!r}")