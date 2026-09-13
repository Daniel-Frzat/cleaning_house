"""
Timezone Resolution — Booking Domain (موعد الزيارة)

يحوّل ولاية أسترالية إلى اسم منطقة زمنية IANA. المنطقة تُشتق من عنوان
العقار وقت إنشاء الحجز، وتُخزَّن على الحجز نصًا للعرض.

⚠️ لا حساب توقيت صيفي يدويًا هنا إطلاقًا: أسماء IANA وحدها تُعاد،
   وzoneinfo القياسية هي التي تعرف متى يبدأ التوقيت الصيفي وينتهي.
   ولايتان لا تطبّقان التوقيت الصيفي (QLD، NT) — وهذا أيضًا معروف
   لقاعدة بيانات المناطق، لا نرمّزه نحن.

📌 الخريطة ثابتة ومقصودة الحرفية: لكل ولاية مدينة مرجعية واحدة.
   Australia/Sydney و Australia/Melbourne و Australia/Hobart تتطابق
   قواعدها حاليًا، لكنها تبقى مداخل منفصلة — لو تباعدت القواعد مستقبلًا
   (كما حدث تاريخيًا بين الولايات) لا يحتاج الأمر إلا تصحيح سطر واحد.
"""

# الولاية → منطقة IANA المرجعية
#   NSW/VIC/TAS/ACT : AEST/AEDT (توقيت صيفي)
#   QLD             : AEST بلا توقيت صيفي
#   SA              : ACST/ACDT (توقيت صيفي)
#   NT              : ACST بلا توقيت صيفي
#   WA              : AWST
STATE_TIMEZONES = {
    "NSW": "Australia/Sydney",
    "VIC": "Australia/Melbourne",
    "TAS": "Australia/Hobart",
    "ACT": "Australia/Sydney",
    "QLD": "Australia/Brisbane",
    "SA": "Australia/Adelaide",
    "NT": "Australia/Darwin",
    "WA": "Australia/Perth",
}

# يُستخدم حين تكون الولاية غير معروفة أو فارغة. الحجز لا يُرفض لأجل
# ذلك — الحقل للعرض فقط — لكن التقويم يبقى شرق أستراليا حيث أغلب السوق.
DEFAULT_TIMEZONE = "Australia/Sydney"


def get_timezone_for_state(state: str) -> str:
    """
    يعيد اسم منطقة IANA الموافق للولاية.

    يتجاهل الفراغات وحالة الأحرف. الولاية المجهولة أو الفارغة تعيد
    DEFAULT_TIMEZONE بدل أن ترفع استثناءً: الحقل للعرض، وإسقاط إنشاء
    حجز صالح لأجل عنوان ناقص ليس سلوكًا مقبولًا.
    """
    if not state:
        return DEFAULT_TIMEZONE

    return STATE_TIMEZONES.get(state.strip().upper(), DEFAULT_TIMEZONE)
