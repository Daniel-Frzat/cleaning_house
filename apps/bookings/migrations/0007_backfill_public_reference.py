"""
يملأ public_reference للحجوزات القائمة قبل إضافة الحقل.

⚠️ لا تستورد من apps.bookings.models: هجرات البيانات تعمل على النموذج
   التاريخي (apps.get_model)، واستيراد النموذج الحيّ يكسر الهجرة لحظة
   تغيّر الحقول لاحقًا. المولّد مكرَّر هنا عمدًا لنفس السبب — هجرة
   البيانات لقطة مجمَّدة في الزمن ولا تتبع تطوّر الكود.

📌 التوليد صف-صف لا دفعة واحدة: التفرد يجب أن يُفحص أثناء التوليد،
   والعدد المتوقع هنا صغير (الحجوزات السابقة لإضافة الحقل).
"""

import secrets

from django.db import migrations

PUBLIC_REFERENCE_PREFIX = "CLN"
PUBLIC_REFERENCE_ALPHABET = "0123456789ACDEFGHJKLMNPQRTUVWXY"
PUBLIC_REFERENCE_LENGTH = 6


def _generate(existing):
    """يولّد مرجعًا غير مستخدَم. `existing` مجموعة تُحدَّث في مكانها."""
    while True:
        suffix = "".join(
            secrets.choice(PUBLIC_REFERENCE_ALPHABET)
            for _ in range(PUBLIC_REFERENCE_LENGTH)
        )
        reference = f"{PUBLIC_REFERENCE_PREFIX}-{suffix}"
        if reference not in existing:
            existing.add(reference)
            return reference


def backfill(apps, schema_editor):
    Booking = apps.get_model("bookings", "Booking")

    existing = set(
        Booking.objects.exclude(public_reference=None).values_list(
            "public_reference", flat=True
        )
    )

    for booking in Booking.objects.filter(public_reference=None).iterator():
        booking.public_reference = _generate(existing)
        # save العادي هنا: النموذج التاريخي لا يحمل save() المخصص الذي
        # يولّد المرجع، فالقيمة المضبوطة أعلاه هي ما يُكتب.
        booking.save(update_fields=["public_reference"])


def noop_reverse(apps, schema_editor):
    """
    التراجع لا يمسح المراجع.

    📌 الحقل يعود nullable في الهجرة السابقة، فالتراجع لا يحتاج تفريغه.
       ومسحه كان سيُتلف مراجع رآها العملاء فعلًا إن أُعيد التطبيق.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0006_booking_public_reference"),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
