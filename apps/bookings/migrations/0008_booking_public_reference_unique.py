"""
يجعل public_reference فريدًا وغير فارغ بعد ملء الصفوف القائمة.

📌 الخطوة الثالثة من ثلاث: 0006 أضاف الحقل nullable، و0007 ملأ الصفوف
   السابقة، وهذه تُحكم القيد. الفصل إلزامي: إضافة حقل فريد وغير فارغ
   دفعةً واحدة تفشل على أي جدول غير فارغ.

⚠️ مكتوبة يدويًا لا بـmakemigrations: الأداة تسأل تفاعليًا عن قيمة
   افتراضية للصفوف القائمة لأنها لا تعرف أن 0007 ملأتها بالفعل.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0007_backfill_public_reference"),
    ]

    operations = [
        migrations.AlterField(
            model_name="booking",
            name="public_reference",
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text=(
                    "Human-readable booking number (e.g. CLN-7F3K9Q); display only."
                ),
                max_length=16,
                unique=True,
            ),
        ),
    ]
