"""
الأسئلة الافتراضية لشاشة "Tell us about your home" — كما في تصميم التطبيق.

📌 بلا أيقونات: الأيقونات تُرفع من لوحة التحكم. وكل سؤال هنا قابل للتعديل
   والحذف من اللوحة؛ get_or_create بالمفتاح فلا يُعاد إنشاء ما حُذف إلا
   بإعادة تشغيل هذه الهجرة على قاعدة جديدة.
"""

from django.db import migrations

DEFAULTS = [
    # key, label, input_type, min, max, default
    ("bedrooms", "Bedrooms", "COUNTER", 0, 20, 0),
    ("bathrooms", "Bathrooms", "COUNTER", 0, 20, 0),
    ("living-areas", "Living areas", "COUNTER", 0, 20, 0),
    ("floors", "Floors", "COUNTER", 1, 10, 1),
    ("pets", "Any pets at home?", "YES_NO", 0, 20, 0),
]


def seed(apps, schema_editor):
    HomeDetailField = apps.get_model("services", "HomeDetailField")
    for order, (key, label, input_type, low, high, default) in enumerate(DEFAULTS):
        HomeDetailField.objects.get_or_create(
            key=key,
            defaults={
                "label": label,
                "input_type": input_type,
                "min_value": low,
                "max_value": high,
                "default_value": default,
                "sort_order": order,
            },
        )


def unseed(apps, schema_editor):
    HomeDetailField = apps.get_model("services", "HomeDetailField")
    HomeDetailField.objects.filter(key__in=[row[0] for row in DEFAULTS]).delete()


class Migration(migrations.Migration):
    dependencies = [("services", "0005_catalogicon_homedetailfield_homedetailoption")]

    operations = [migrations.RunPython(seed, unseed)]
