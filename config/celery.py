"""
Celery App — Phase 0 (Infra only)

⚠️ لا توجد هنا أي Tasks بمنطق أعمال فعلي.
المهام الحقيقية (Dispatch expiry, Escrow checks, Notifications,
Invoice triggers, Guarantee window, Insurance expiry) ستُضاف كل
واحدة ضمن الـDomain الخاص بها في مرحلتها المحددة (راجع قسم 20
من المرجع المعماري).
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