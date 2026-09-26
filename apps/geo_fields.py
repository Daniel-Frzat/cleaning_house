"""
أنواع الإحداثيات ودقة GPS المشتركة بين مخططات الـAPI.

📌 التقريب لا الرفض: GPS الجهاز يعيد double بـ12–15 خانة عشرية
   (-34.928500123456)، وقاعدة البيانات تخزّن 6 خانات (~11 سم). رفض القيمة
   بـ422 كان يكسر كل التقاط موقع من التطبيق — والخانات الزائدة ضجيج لا
   معلومة. فنقرّب إلى دقة التخزين قبل فحص الحدود، ويبقى الرفض للقيمة
   المستحيلة فقط (خط عرض 91، نص، NaN).
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated

from pydantic import BeforeValidator, Field


def _rounded(places):
    step = Decimal(1).scaleb(-places)

    def round_value(value):
        if value is None or isinstance(value, bool):
            return value
        try:
            # float عبر str: Decimal(0.1) يحمل ذيل التمثيل الثنائي
            number = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
            if not number.is_finite():
                return value
            return number.quantize(step, rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError):
            return value  # يرفضه تحقق pydantic برسالته المعتادة

    return BeforeValidator(round_value)


# فحص شكلي لا جغرافي — لا نقيّد بحدود أستراليا (قرب الحدود البحرية أو خطأ
# بسيط في GPS كان سيُرفض بلا مبرر).
Latitude = Annotated[Decimal, _rounded(6), Field(ge=-90, le=90, max_digits=9, decimal_places=6)]
Longitude = Annotated[Decimal, _rounded(6), Field(ge=-180, le=180, max_digits=9, decimal_places=6)]
# دقة القراءة بالأمتار — عمود accuracy_meters (7, 2)
AccuracyMeters = Annotated[Decimal, _rounded(2), Field(ge=0, max_digits=7, decimal_places=2)]
