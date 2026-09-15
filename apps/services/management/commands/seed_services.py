"""
أمر إداري: يبذر الخدمات الأساسية والإضافات إن لم تكن موجودة.

📌 سبب الوجود: الكتالوج يبدأ فارغًا، فـGET /api/services يعيد [] ولا يجد
   تطبيق الموبايل ما يعرضه. هذا الأمر ينشئ الخدمات المتوقَّعة بمعرّفات
   ثابتة، فتصير الـids نفسها في كل بيئة (تطوير/staging/إنتاج) وتستطيع
   الواجهة تثبيت خريطة الأيقونات مرة واحدة.

📌 سبعة صفوف: ثلاث خدمات أساسية (بسعر غرفة + رسم أساسي)، وأربع إضافات
   (رسم ثابت وحده). الباكند لا يميّز بينها — كلها ServiceType.

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
    # ------------------------------------------------------------
    # الإضافات (add-ons)
    # ------------------------------------------------------------
    # 📌 لا يوجد مفهوم "إضافة" في النطاق: كل ما يُحجز هو ServiceType.
    #    الإضافة إذن خدمة كاملة برسم ثابت — room_price=0 و base_price هو
    #    الرسم. المعادلة (room_price × room_count) + base_price تعطي الرسم
    #    الثابت مهما كان room_count، فخطأ في الواجهة لا يشوّه السعر.
    #
    # ⚠️ تُرسَل مع room_count=0 (مسموح صراحةً — "الرسم الأساسي وحده").
    #
    # ⚠️ لا شيء في الباكند يميّزها عن الخدمات الأساسية: تظهر في
    #    GET /api/services مع البقية، والتمييز البصري (قسم "Add-ons"
    #    منفصل) قرار واجهة يُبنى على معرّفاتها الثابتة أدناه.
    {
        "id": "a1b2c3d4-0101-4000-8000-000000000101",
        "name": "Inside Oven Clean",
        "description": "Degrease and detail the oven interior, racks and door "
                       "glass. Flat fee, not per room.",
        "room_price": Decimal("0.00"),
        "base_price": Decimal("45.00"),
    },
    {
        "id": "a1b2c3d4-0102-4000-8000-000000000102",
        "name": "Interior Windows",
        "description": "Interior window panes, sills and tracks throughout the "
                       "property. Flat fee, not per room.",
        "room_price": Decimal("0.00"),
        "base_price": Decimal("55.00"),
    },
    {
        "id": "a1b2c3d4-0103-4000-8000-000000000103",
        "name": "Carpet Steam Clean",
        "description": "Hot-water extraction for carpeted areas. Flat fee, "
                       "not per room.",
        "room_price": Decimal("0.00"),
        "base_price": Decimal("90.00"),
    },
    {
        "id": "a1b2c3d4-0104-4000-8000-000000000104",
        "name": "Balcony Clean",
        "description": "Sweep, mop and wipe down balcony surfaces and railings. "
                       "Flat fee, not per room.",
        "room_price": Decimal("0.00"),
        "base_price": Decimal("35.00"),
    },
]

# معرّفات الإضافات وحدها — تُصدَّر للاختبارات وللواجهة. ليست تصنيفًا
# يعرفه الباكند، بل قائمة مرجعية لمن يريد عرضها في قسم منفصل.
ADDON_IDS = frozenset(s["id"] for s in SEED_SERVICES if s["room_price"] == 0)


class Command(BaseCommand):
    help = "Create the baseline service types with stable ids (idempotent)."

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
