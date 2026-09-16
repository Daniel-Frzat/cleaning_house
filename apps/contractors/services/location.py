"""
Contractor Current Location — Contractors Domain (§8)

موقع المقاول الحالي من هاتفه: مدخل الإسناد الفوري.

📌 ثلاثة مفاهيم موقع منفصلة — راجع ContractorCurrentLocation في models.py.
   هذا واحد منها: "أين هو الآن" لا "أين مقرّه" ولا "أين هو في طريقه لمهمة".

🔒 لا يُكشف لأحد: لا للعملاء ولا لمقاولين آخرين. الإسناد يقرأه داخليًا،
   والعميل لا يرى موقع عامل إلا بعد إسناده لحجزه وعبر jobs.JobLocation.

⚠️ الموقع القديم ليس صالحًا للإسناد: السعر يُحسب على مسافته، ومسافة
   محسوبة من نقطة عمرها ساعة قد تكون خاطئة تمامًا.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..models import ContractorCurrentLocation
from .profile import (
    ContractorProfileError,
    ContractorProfileNotFoundError,
    ContractorProfilePermissionError,
    InvalidContractorRoleError,  # noqa: F401 — يُعاد تصديره لطبقة الـAPI
    get_own_profile,
)

logger = logging.getLogger(__name__)

# هامش انحراف ساعات الأجهزة المقبول قبل رفض نقطة "من المستقبل".
CLOCK_SKEW_TOLERANCE_SECONDS = 60


class LocationError(ContractorProfileError):
    """أصل أخطاء الموقع الحالي."""

    code = "location_error"


class InvalidLocationError(LocationError):
    """
    النقطة نفسها غير مقبولة (وقت من المستقبل مثلًا).

    400 لا 409: العيب في محتوى الطلب لا في حالة المورد.
    """

    code = "invalid_location"


@transaction.atomic
def report_current_location(
    user, latitude, longitude, recorded_at, accuracy_meters=None
):
    """
    يسجّل آخر موقع معلوم للمقاول.

    🔒 get_own_profile يفرض المقاول نفسه: لا يُقبل معرّف من العميل، فلا
       سبيل لتحديث موقع مقاول آخر.

    ⚠️ يستبدل النقطة السابقة ولا يضيف إليها — لا أرشيف تحركات (§23).

    ⚠️ نقطة "من المستقبل" تُرفض: ساعة متقدّمة كانت ستُبقي الموقع "حديثًا"
       إلى الأبد، والقبول الصامت يخفي جهازًا معطوبًا.
    """
    profile = get_own_profile(user)

    if recorded_at > timezone.now() + timedelta(seconds=CLOCK_SKEW_TOLERANCE_SECONDS):
        raise InvalidLocationError(
            "recorded_at is in the future; check the device clock."
        )

    location, created = ContractorCurrentLocation.objects.update_or_create(
        contractor=profile,
        defaults={
            "latitude": latitude,
            "longitude": longitude,
            "accuracy_meters": accuracy_meters,
            "recorded_at": recorded_at,
        },
    )
    # full_clean بعد الكتابة: update_or_create يبني الكائن داخليًا،
    # والتحقق هنا يحرس حدود الإحداثيات على مستوى النموذج.
    location.full_clean()

    logger.info(
        "Contractor location reported (contractor_id=%s, created=%s)",
        profile.id,
        created,
    )

    return location


def get_own_current_location(user):
    """
    يعيد موقع المقاول الحالي، أو None إن لم يُبلّغ بعد.

    📌 الغياب ليس خطأً: التطبيق قد يكون لم يرسل بعد أو أوقف المشاركة.
    """
    profile = get_own_profile(user)
    return ContractorCurrentLocation.objects.filter(contractor=profile).first()


def get_fresh_location(profile, now=None):
    """
    موقع المقاول إن كان حديثًا بما يكفي للإسناد، وإلا None.

    🔒 دالة داخلية للإسناد — لا تمرّ عبر أي مسار API. الموقع الحالي لا
       يُكشف لأحد.

    ⚠️ الموقع المتقادم يُعامل كغياب موقع تمامًا: كلاهما يعني أننا لا نعرف
       أين هو، والمسافة المجهولة ليست صفرًا ولا افتراضًا.
    """
    location = getattr(profile, "current_location", None)
    if location is None:
        return None

    return location if location.is_fresh(now) else None


__all__ = [
    "LocationError",
    "InvalidLocationError",
    "ContractorProfilePermissionError",
    "ContractorProfileNotFoundError",
    "InvalidContractorRoleError",
    "report_current_location",
    "get_own_current_location",
    "get_fresh_location",
]
