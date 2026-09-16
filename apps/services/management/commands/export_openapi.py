"""
Export the OpenAPI schema to a file.

⚠️ سبب وجوده: نُسخة openapi.json قديمة كلّفت فريق الموبايل حذف ميزة تعمل
   فعلًا، لأن المخطط الذي بين أيديهم لم يكن فيه dispatch_status ولا
   started_at. المخطط يُولَّد من الكود الحيّ، فتصديره بأمر واحد يمنع أن
   يُنسخ يدويًا من بيئة متأخّرة.

الاستعمال:
    python manage.py export_openapi                 # إلى openapi.json
    python manage.py export_openapi --output x.json
    python manage.py export_openapi --check         # يفشل إن كان الملف قديمًا
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

DEFAULT_OUTPUT = "openapi.json"


class Command(BaseCommand):
    help = "Export the OpenAPI schema generated from the running code."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default=DEFAULT_OUTPUT,
            help=f"Destination file (default: {DEFAULT_OUTPUT}).",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help=(
                "Do not write; exit non-zero if the file is missing or "
                "differs from the current code. For CI."
            ),
        )

    def handle(self, *args, **options):
        # يُستورد هنا لا في الأعلى: تحميل الـURLs يتطلّب تطبيقات جاهزة
        from config.urls import api

        schema = json.dumps(
            api.get_openapi_schema(), indent=2, default=str, ensure_ascii=False
        )
        path = Path(options["output"])

        if options["check"]:
            if not path.exists():
                raise CommandError(
                    f"{path} does not exist. Run: manage.py export_openapi"
                )
            if path.read_text(encoding="utf-8").strip() != schema.strip():
                raise CommandError(
                    f"{path} is out of date with the code. "
                    "Run: manage.py export_openapi"
                )
            self.stdout.write(self.style.SUCCESS(f"{path} is up to date."))
            return

        path.write_text(schema, encoding="utf-8")

        paths = api.get_openapi_schema()["paths"]
        endpoints = sum(
            len([m for m in methods if m in
                 ("get", "post", "patch", "put", "delete")])
            for methods in paths.values()
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {path} ({endpoints} endpoints, "
                f"{len(paths)} paths)."
            )
        )
