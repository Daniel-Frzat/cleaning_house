"""
أمر إداري: يبذر الخدمات الثلاث الأساسية إن لم تكن موجودة.

📌 سبب الوجود: الكتالوج يبدأ فارغًا، فـGET /api/services يعيد [] ولا يجد
   تطبيق الموبايل ما يعرضه. هذا الأمر ينشئ الخدمات الثلاث المتوقَّعة
   بمعرّفات ثابتة، فتصير الـids نفسها في كل بيئة (تطوير/staging/إنتاج)
   وتستطيع الواجهة تثبيت خريطة الأيقونات مرة واحدة.

⚠️ الأسعار قيم ابتدائية قابلة للتعديل من الإدارة في أي وقت
   (PATCH /api/admin/services/{id}) — لا تُعتبر قرار تسعير.

⚠️ Idempotent: تشغيله مرارًا لا يكرّر ولا يدهس تعديلات الإدارة. الصف
   الموجود يُترك كما هو تمامًا، ويُبلَّغ عنه بـ"exists". لتحديث الأسعار
   أو الأسماء استخدم المسارات الإدارية، لا هذا الأمر.

الاستخدام:
    python manage.py seed_services
    python manage.py seed_services --dry-run
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.services.models import ServiceType

# 🔒 معرّفات ثابتة مقصودة: تبقى نفسها عبر البيئات، فخريطة الأيقونات في
#    الواجهة تُبنى مرة واحدة ولا تنكسر عند إعادة النشر أو تغيير الاسم.
SEED_SERVICES = [
    {
        "id": "a1b2c3d4-0001-4000-8000-000000000001",
        "name": "Regular Cleaning",
        "description": "Routine home clean: dusting, vacuuming, mopping, "
                       "kitchen and bathroom surfaces.",
        "room_price": Decimal("35.00"),
        "base_price": Decimal("60.00"),
    },
    {
        "id": "a1b2c3d4-0002-4000-8000-000000000002",
        "name": "Deep Cleaning",
        "description": "Thorough clean including inside appliances, skirting "
                       "boards, window tracks and detailed bathroom work.",
        "room_price": Decimal("55.00"),
        "base_price": Decimal("120.00"),
    },
    {
        "id": "a1b2c3d4-0003-4000-8000-000000000003",
        "name": "End of Lease Cleaning",
        "description": "Bond-back clean to end-of-tenancy standard, including "
                       "oven, carpets and full property detail.",
        "room_price": Decimal("70.00"),
        "base_price": Decimal("180.00"),
    },
]


class Command(BaseCommand):
    help = "Create the three baseline service types with stable ids (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would happen without writing anything.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry = options["dry_run"]
        created = existing = 0

        for spec in SEED_SERVICES:
            # التعرّف بالمعرّف أولًا ثم بالاسم: لو أنشأت الإدارة الخدمة
            # يدويًا بمعرّف آخر، لا نُنشئ نسخة ثانية بالاسم نفسه (الاسم
            # فريد على مستوى قاعدة البيانات وكان الإنشاء سيفشل أصلًا).
            found = (
                ServiceType.objects.filter(pk=spec["id"]).first()
                or ServiceType.objects.filter(name=spec["name"]).first()
            )

            if found is not None:
                existing += 1
                self.stdout.write(
                    "  exists   %-38s %s" % (found.id, found.name)
                )
                continue

            created += 1
            if dry:
                self.stdout.write(
                    self.style.WARNING(
                        "  would create %-34s %s" % (spec["id"], spec["name"])
                    )
                )
                continue

            service = ServiceType(
                id=spec["id"],
                name=spec["name"],
                description=spec["description"],
                room_price=spec["room_price"],
                base_price=spec["base_price"],
                is_active=True,
            )
            service.full_clean()
            service.save()
            self.stdout.write(
                self.style.SUCCESS("  created  %-38s %s" % (service.id, service.name))
            )

        self.stdout.write("")
        if dry:
            self.stdout.write(
                "Dry run: %d would be created, %d already exist. Nothing written."
                % (created, existing)
            )
            transaction.set_rollback(True)
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Done: %d created, %d already existed." % (created, existing)
                )
            )
