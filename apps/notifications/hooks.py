"""
نقطة الربط الوحيدة بين النطاقات والإشعارات.

    from apps.notifications.hooks import emit_on_commit
    emit_on_commit("job_started", job)

📌 الحدث يُبنى بعد نجاح المعاملة وحدها (transaction.on_commit): فعل تراجع
   لا يُرسل إشعارًا كاذبًا، وفشل الإشعار لا يمس فعلًا نجح.

⚠️ وحدة المهام (apps/jobs/services/jobs.py) تستورد هذا داخل الدوال لا على
   مستوى الملف — حارس معماري في tests/test_jobs_completion.py.
"""

import logging

from django.db import transaction

logger = logging.getLogger(__name__)


def emit_on_commit(event, *args):
    def run():
        try:
            from .services import events

            getattr(events, event)(*args)
        except Exception:  # noqa: BLE001 — الإشعار لا يُسقط فعلًا نجح
            logger.exception("Notification event failed (event=%s)", event)

    transaction.on_commit(run, robust=True)
