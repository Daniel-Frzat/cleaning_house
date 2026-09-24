"""
Postcode / State Consistency — Properties Domain

يمنع تخزين عنوان متناقض صامتًا: "Fitzroy, VIC, 2311" كان يُقبل بـ201،
و2311 نطاق نيو ساوث ويلز لا فيكتوريا.

⚠️ لماذا هذا مهم لا تجميليًا: المنطقة الزمنية للحجز تُشتق من `state`
   (apps/bookings/services/timezone.py)، وقاعدة ساعات العمل ٠٧:٠٠–١٩:٠٠
   تُطبَّق بها. ولاية خاطئة تعني نافذة عمل خاطئة بفارق قد يبلغ ٣ ساعات.

📌 النطاقات من Australia Post. هذا **فحص اتساق** لا تحقّق من وجود العنوان:
   يقول إن الرمز ينتمي لهذه الولاية، لا إن الشارع موجود. التحقق الحقيقي
   من العناوين يحتاج مزوّدًا خارجيًا — البند المفتوح #23، ولم يُحسم بعد.

⚠️ عند الشك نقبل لا نرفض: كل نطاق أُدرج بحدوده الرسمية الواسعة، والنطاقات
   المشتركة (ACT داخل NSW مثلًا) مُدرجة للولايتين. رفض عنوان صحيح أسوأ من
   قبول عنوان خاطئ هنا، لأن الأول يمنع مستخدمًا حقيقيًا من الحجز.
"""

# state -> [(lo, hi), ...] شاملة الطرفين
POSTCODE_RANGES = {
    # 2618: Wallaroo وجوارها — بلدات NSW على حدود كانبرا برمز من نطاق ACT
    "NSW": [(1000, 2599), (2618, 2618), (2619, 2899), (2921, 2999)],
    # ACT محاطة بنيو ساوث ويلز ونطاقاتها متداخلة معها تاريخيًا.
    # 2620 مشترك: Queanbeyan (NSW) و Hume/Tharwa (ACT).
    "ACT": [(200, 299), (2600, 2618), (2620, 2620), (2900, 2920)],
    "VIC": [(3000, 3999), (8000, 8999)],
    "QLD": [(4000, 4999), (9000, 9999)],
    "SA": [(5000, 5999)],
    # 6798/6799: Christmas Island و Cocos — أقاليم خارجية تُدار بريديًا مع WA
    "WA": [(6000, 6999)],
    "TAS": [(7000, 7999)],
    "NT": [(800, 999)],
}


def postcode_matches_state(postcode, state):
    """
    True إن كان الرمز البريدي ينتمي لهذه الولاية.

    ⚠️ الرمز غير الرقمي أو غير رباعي الخانات يُعاد True: شكل الرمز مسؤولية
       POSTCODE_VALIDATOR، ولا يجوز أن يبتلع هذا الفحص خطأً ليس خطأه —
       وإلا لظهر خطأ "الرمز لا يطابق الولاية" على رمز شكله خاطئ أصلًا.
    """
    if state not in POSTCODE_RANGES:
        # ولاية مجهولة: الـenum يرفضها قبل الوصول إلى هنا
        return True

    text = (postcode or "").strip()
    if not text.isdigit() or len(text) != 4:
        return True

    value = int(text)
    return any(lo <= value <= hi for lo, hi in POSTCODE_RANGES[state])


def states_for_postcode(postcode):
    """الولايات التي يقع فيها هذا الرمز — للرسالة الإرشادية."""
    text = (postcode or "").strip()
    if not text.isdigit() or len(text) != 4:
        return []

    value = int(text)
    return sorted(
        state
        for state, ranges in POSTCODE_RANGES.items()
        if any(lo <= value <= hi for lo, hi in ranges)
    )


def postcode_state_error(postcode, state):
    """
    رسالة الخطأ، أو None إن كان الاتساق سليمًا.

    📌 الرسالة تسمّي الولاية الصحيحة حين تكون معروفة: العميل أخطأ غالبًا
       في الولاية لا في الرمز (كتب اسم المدينة مكان الولاية مثلًا).
    """
    if postcode_matches_state(postcode, state):
        return None

    candidates = states_for_postcode(postcode)
    if candidates:
        return (
            f"Postcode {postcode} is not in {state}; "
            f"it belongs to {' or '.join(candidates)}."
        )
    return f"Postcode {postcode} is not a valid Australian postcode for {state}."
