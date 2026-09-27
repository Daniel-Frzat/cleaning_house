"""
Scheduled Visit — Booking Domain (موعد الزيارة)

تطبيع موعد الزيارة والتحقق منه: تحويله إلى UTC، وفرض ساعات الخدمة إن
فُعّلت، وفرض أن يكون في المستقبل.

📌 ساعات الخدمة يضبطها الداشبورد (PricingConfig.service_hours_*، قرار PO —
   2026-09-27)، ومعطّلة افتراضيًا: الخدمة متاحة على مدار الساعة. لا يوجد
   هنا — ولا يجوز أن يوجد — أي فحص لتوفّر مقاول بعينه: الإسناد يقع بعد
   الحجز لا قبله (§36.1).

⚠️ الحدود تُقارن بالتوقيت المحلي للعنوان، لا بتوقيت الخادم. لحظة UTC
   واحدة تقع داخل الدوام في بيرث وخارجه في سيدني.

⚠️ الحد الأعلى شامل: وقت الإغلاق بالضبط مقبول كنقطة بداية زيارة.
"""

import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone as dj_timezone


class SchedulingError(Exception):
    """أصل أخطاء موعد الزيارة."""

    code = "scheduling_error"


class MissingScheduledAtError(SchedulingError):
    """لا موعد زيارة في الطلب."""

    code = "scheduled_at_required"


class ScheduledAtInPastError(SchedulingError):
    """الموعد في الماضي."""

    code = "scheduled_at_in_past"


class TimezoneMismatchError(SchedulingError):
    """منطقة زمنية من العميل تخالف المنطقة المشتقة من العنوان."""

    code = "timezone_not_allowed"


class ScheduledTooSoonError(SchedulingError):
    """الموعد أقرب من المهلة الدنيا (BOOKING_MIN_LEAD_MINUTES)."""

    code = "scheduled_at_too_soon"


class OutsideBusinessHoursError(SchedulingError):
    """الموعد خارج ساعات العمل العامة."""

    code = "outside_business_hours"


def normalize_scheduled_at(scheduled_at, timezone_name):
    """
    يحوّل الموعد إلى UTC بعد التحقق منه.

    Args:
        scheduled_at: datetime واعٍ بالتوقيت أو ساذج (naive).
        timezone_name: اسم IANA المحسوب من عنوان العقار.

    Returns:
        datetime واعٍ بتوقيت UTC.

    Raises:
        MissingScheduledAtError, ScheduledAtInPastError, OutsideBusinessHoursError

    📌 قاعدة الـnaive: الموعد بلا توقيت يُفسَّر بتوقيت المنطقة المحسوبة
       من العنوان — لا بتوقيت الخادم. العميل يكتب "الثلاثاء 9 صباحًا"
       قاصدًا التاسعة عند عقاره.
    """
    if scheduled_at is None:
        raise MissingScheduledAtError("A scheduled visit date and time is required.")

    tz = ZoneInfo(timezone_name)

    if dj_timezone.is_naive(scheduled_at):
        # بلا توقيت صريح → التوقيت المحلي للعنوان
        local = scheduled_at.replace(tzinfo=tz)
    else:
        # بتوقيت صريح → يُحترم كما وصل، ويُقرأ محليًا لفحص الدوام
        local = scheduled_at.astimezone(tz)

    utc_value = local.astimezone(datetime.timezone.utc)

    # 🔒 المستقبل أولًا: موعد ماضٍ داخل الدوام يبقى مرفوضًا
    now = dj_timezone.now()
    if utc_value <= now:
        raise ScheduledAtInPastError("The scheduled visit must be in the future.")

    # 📌 مهلة دنيا: العرض يعيش حتى 60 دقيقة والمقاول يحتاج وقتًا للوصول،
    #    فموعد بعد دقيقتين لا يمكن خدمته (قرار PO — 2026-09-24: ساعتان).
    lead = getattr(settings, "BOOKING_MIN_LEAD_MINUTES", 0)
    if lead and utc_value < now + datetime.timedelta(minutes=lead):
        raise ScheduledTooSoonError(
            f"The scheduled visit must be at least {lead} minutes from now."
        )

    window = current_service_window()
    if not _within(local, window):
        raise OutsideBusinessHoursError(
            f"The scheduled visit must fall between {_label(window)} "
            f"local time ({timezone_name}); got {local.strftime('%H:%M')}."
        )

    return utc_value


def to_local(utc_value, timezone_name):
    """
    يعيد تمثيل الموعد بالتوقيت المحلي — للعرض وحده.

    يعيد None إن لم يكن هناك موعد (صفوف سابقة للحقل).
    """
    if utc_value is None:
        return None

    return utc_value.astimezone(ZoneInfo(timezone_name or "UTC"))


def assert_open_now(timezone_name, now=None):
    """
    الطلب الفوري (بلا scheduled_at): ساعات العمل نفسها 07:00–19:00 بتوقيت
    العقار تسري على لحظة الطلب (قرار PO — 2026-09-26). لا مهلة دنيا.

    ساعات الخدمة المعطّلة (الافتراضي) = لا فحص.
    """
    window = current_service_window()
    local = (now or dj_timezone.now()).astimezone(ZoneInfo(timezone_name))
    if not _within(local, window):
        raise OutsideBusinessHoursError(
            f"Cleaners can be requested between {_label(window)} local time "
            f"({timezone_name}); it is {local.strftime('%H:%M')} there now."
        )


def current_service_window():
    """
    (بداية، نهاية) من إعداد الداشبورد، أو None حين تكون الساعات معطّلة
    (الافتراضي: متاح على مدار الساعة).
    """
    from apps.services.services.travel_pricing import get_active_config

    config = get_active_config()
    if not config.service_hours_enabled:
        return None
    return config.service_hours_start, config.service_hours_end


def _within(local, window):
    if window is None:
        return True
    start, end = window
    local_time = local.timetz().replace(tzinfo=None)
    if start < end:
        return start <= local_time <= end
    # نافذة تعبر منتصف الليل (20:00 → 02:00)
    return local_time >= start or local_time <= end


def _label(window):
    start, end = window
    return f"{start.strftime('%H:%M')} and {end.strftime('%H:%M')}"


def service_hours(timezone_name, now=None):
    """
    ساعات الخدمة كما يطبّقها الخادم — ليعرضها التطبيق ويمنع الإرسال خارجها
    قبل أن يرسل (تقرير التطبيق B18)، بدل أن يكتشفها من 400 بعد الإرسال.

    🔒 مصدر واحد: نفس الثوابت ونفس المفتاح اللذين يفحص بهما assert_open_now
       و normalize_scheduled_at، فلا يختلف ما يُعرض عمّا يُفرض.
    """
    tz = ZoneInfo(timezone_name)
    local = (now or dj_timezone.now()).astimezone(tz)
    window = current_service_window()
    is_open = _within(local, window)

    next_open_at = None
    if not is_open:
        start = window[0]
        opening = local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
        if opening <= local:  # الافتتاح التالي غدًا
            opening = (local + datetime.timedelta(days=1)).replace(
                hour=start.hour, minute=start.minute, second=0, microsecond=0
            )
        next_open_at = opening.astimezone(datetime.timezone.utc)

    return {
        "timezone": timezone_name,
        "enabled": window is not None,
        "opens_at": window[0].strftime("%H:%M") if window else None,
        "closes_at": window[1].strftime("%H:%M") if window else None,
        "is_open_now": is_open,
        "next_open_at": next_open_at,
        "min_lead_minutes": getattr(settings, "BOOKING_MIN_LEAD_MINUTES", 0),
    }
