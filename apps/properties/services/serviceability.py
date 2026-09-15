"""
Property Serviceability — Properties Domain

يجيب عن سؤال واحد: هل يستطيع محرّك الإسناد أن يصل إلى هذا العقار أصلًا؟

⚠️ السبب الوحيد اليوم هو الإحداثيات. محرّك الإسناد
   (apps/bookings/services/dispatch.py) يرتّب المقاولين بالمسافة، ومن لا
   إحداثيات له يسقط من الترشيح **صامتًا**: لا خطأ، ولا عرض، ولا سبب ظاهر.
   الفشل لا يظهر إلا لاحقًا على هيئة NO_CONTRACTOR لا تنحلّ أبدًا.

📌 مشتقّ لا مخزَّن: يُحسب من العنوان لحظيًا. لا حقل في قاعدة البيانات
   يمكن أن يتقادم، ولا migration يلزم حين تُضاف الإحداثيات بـPATCH.

⚠️ ليس فحص تغطية جغرافية: لا يقول إن كان في المنطقة مقاولون، بل إن كان
   العقار قابلًا للقياس أصلًا. التغطية قرار منتج لم يُطلب بعد.
"""

# أسباب عدم القابلية للخدمة — ثابتة نصية يقرؤها العميل ولا يترجمها بنفسه
REASON_MISSING_COORDINATES = "missing_coordinates"

MISSING_COORDINATES_MESSAGE = (
    "This property has no GPS coordinates, so no cleaner can be matched to it. "
    "Set the location to make it bookable."
)


def missing_coordinates(prop):
    """
    True إن كان العقار بلا إحداثيات صالحة (أو بلا عنوان أصلًا).

    الحقلان يُفحصان معًا لا أحدهما: نصف إحداثية لا تُقاس بها مسافة،
    وهو بالضبط ما يفعله get_coordinates في محرّك المسافة.
    """
    address = getattr(prop, "address", None)
    if address is None:
        return True

    return address.latitude is None or address.longitude is None


def serviceability_warning(prop):
    """
    يعيد تحذيرًا {code, message} أو None إن كان العقار سليمًا.

    📌 تحذير لا خطأ: العقار يُنشأ ويُقرأ بنجاح في الحالتين. الغرض أن
       يكتشف العميل العطب لحظة الإنشاء بدل أن يكتشفه بحجز لا يُسنَد.
    """
    if missing_coordinates(prop):
        return {
            "code": REASON_MISSING_COORDINATES,
            "message": MISSING_COORDINATES_MESSAGE,
        }
    return None
