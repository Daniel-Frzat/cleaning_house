"""
Scheduled Visit — Booking Domain (موعد الزيارة)

تطبيع موعد الزيارة والتحقق منه: تحويله إلى UTC، وفرض ساعات العمل العامة،
وفرض أن يكون في المستقبل.

📌 ساعات العمل ثابتة وعامة (07:00–19:00 بالتوقيت المحلي المحسوب). لا
   يوجد هنا — ولا يجوز أن يوجد — أي فحص لتوفّر مقاول بعينه: الإسناد
   يقع بعد الحجز لا قبله (§36.1)، فلا مقاول معروف أصلًا لحظة التحقق.

⚠️ حدود ساعات العمل تُقارن بالتوقيت المحلي للعنوان، لا بتوقيت الخادم.
   لحظة UTC واحدة تقع داخل الدوام في بيرث وخارجه في سيدني.

⚠️ الحد الأعلى شامل: 19:00 بالضبط مقبولة كنقطة بداية زيارة. لا مدة
   مخزَّنة في هذه المرحلة، فلا معنى لحساب وقت انتهاء.
"""

import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone as dj_timezone

# ساعات العمل العامة بالتوقيت المحلي المحسوب
BUSINESS_HOURS_START = datetime.time(7, 0)
BUSINESS_HOURS_END = datetime.time(19, 0)


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

    local_time = local.timetz().replace(tzinfo=None)
    if not (BUSINESS_HOURS_START <= local_time <= BUSINESS_HOURS_END):
        raise OutsideBusinessHoursError(
            f"The scheduled visit must fall between "
            f"{BUSINESS_HOURS_START.strftime('%H:%M')} and "
            f"{BUSINESS_HOURS_END.strftime('%H:%M')} "
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
    """
    tz = ZoneInfo(timezone_name)
    local = (now or dj_timezone.now()).astimezone(tz)
    local_time = local.timetz().replace(tzinfo=None)
    if not (BUSINESS_HOURS_START <= local_time <= BUSINESS_HOURS_END):
        raise OutsideBusinessHoursError(
            f"Cleaners can be requested between "
            f"{BUSINESS_HOURS_START.strftime('%H:%M')} and "
            f"{BUSINESS_HOURS_END.strftime('%H:%M')} local time ({timezone_name}); "
            f"it is {local.strftime('%H:%M')} there now."
        )
