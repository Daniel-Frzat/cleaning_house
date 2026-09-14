"""
أمر إداري: يطبع كتالوج الخدمات مباشرةً من قاعدة البيانات.

📌 سبب الوجود: فرق الواجهة تحتاج service_type_id الحقيقي (UUID) لبناء
   خريطة الأيقونات ولإرساله في POST /api/bookings، لكن GET /api/services
   يتطلب JWT — وتسجيل الدخول معطّل بالإنتاج ما لم يُختر مزوّد SMS أو
   دخول اجتماعي. هذا الأمر يتجاوز الـAPI والمصادقة كليًا.

⚠️ أداة قراءة فقط: لا ينشئ ولا يعدّل ولا يحذف أي صف.

الاستخدام:
    python manage.py list_services            # الخدمات النشطة، جدول
    python manage.py list_services --all      # يشمل المعطَّلة
    python manage.py list_services --json     # مخرجات صالحة للّصق بالكود
"""

import json

from django.core.management.base import BaseCommand

from apps.services.models import ServiceType


class Command(BaseCommand):
    help = "Print the service catalog (id, name, description) straight from the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Include inactive services (hidden from GET /api/services).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit JSON instead of a table.",
        )

    def handle(self, *args, **options):
        queryset = ServiceType.objects.all()
        if not options["all"]:
            queryset = queryset.filter(is_active=True)

        services = list(queryset)

        if options["json"]:
            payload = [
                {
                    "id": str(s.id),
                    "name": s.name,
                    "description": s.description,
                    "is_active": s.is_active,
                }
                for s in services
            ]
            self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        if not services:
            self.stdout.write(
                self.style.WARNING(
                    "The catalog is EMPTY - no service types exist in this database.\n"
                    "GET /api/services would return []. Services are created by an "
                    "admin via POST /api/admin/services (or the Django admin site)."
                )
            )
            return

        self.stdout.write("")
        self.stdout.write(
            "%-38s  %-28s  %-7s  %s" % ("SERVICE_TYPE_ID", "NAME", "ACTIVE", "DESCRIPTION")
        )
        self.stdout.write("-" * 110)
        for s in services:
            self.stdout.write(
                "%-38s  %-28s  %-7s  %s"
                % (s.id, s.name, s.is_active, (s.description or "")[:36])
            )

        self.stdout.write("")
        self.stdout.write(
            "%d service(s). Send the SERVICE_TYPE_ID verbatim as "
            "service_selections[].service_type_id in POST /api/bookings." % len(services)
        )
