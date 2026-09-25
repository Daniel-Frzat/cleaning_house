"""
python manage.py cleanup_notifications [--retry]

بديل لمهام Celery Beat على استضافة بلا عامل خلفي (Cron Job في Railway):
يحذف الإشعارات الأقدم من مدة الاحتفاظ والأجهزة الخاملة، ويعيد إرسال ما علق.
"""

from django.core.management.base import BaseCommand

from apps.notifications.services import notifications as svc


class Command(BaseCommand):
    help = "Delete expired notifications and stale device tokens; optionally retry pending pushes."

    def add_arguments(self, parser):
        parser.add_argument("--retry", action="store_true", help="Also retry pending pushes.")

    def handle(self, *args, **options):
        result = svc.cleanup()
        self.stdout.write(
            f"Deleted {result['notifications']} notification(s) and {result['devices']} device(s)."
        )
        if options["retry"]:
            self.stdout.write(f"Retried {svc.retry_pending()} pending push(es).")
