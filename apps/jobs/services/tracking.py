"""
Job Location Tracking — Job Execution Domain

تتبع موقع المقاول أثناء توجّهه إلى العقار. وحدة منفصلة عن jobs.py بنفس
منطق فصل photos.py: مسؤولية مستقلة لها قواعدها الزمنية الخاصة.

📌 الموقع الحيّ **ليس** ContractorProfile.latitude/longitude. ذانك عنوان
   العمل الثابت الذي يرتّب به Dispatch المرشَّحين؛ الكتابة فوقهما بموقع
   متحرّك تُفسد الإسناد صامتًا. راجع JobLocation في models.py.

🔒 الصلاحية طرفان لا أكثر:
     الكتابة — المقاول المُسنَد لهذه المهمة وحده.
     القراءة — العميل مالك الحجز، أو الإدارة، أو المقاول نفسه.
   المقاول الذي عُرض عليه الحجز ولم يقبل لا يكتب ولا يقرأ — نفس منطق
   حجب access_notes عن غير المُسنَد.

⚠️ النافذة ASSIGNED وحدها (Job.is_tracking_window_open). بعد إعلان البدء
   يكون العامل داخل المنزل، فيُرفض التحديث ويتوقف العرض.

⚠️ خارج النطاق عمدًا:
   - لا مسار (path history): صف واحد يُكتب فوقه. الأرشيف يحتاج سياسة
     حذف غير موجودة، ولا يُطلب منه شيء اليوم.
   - لا ETA ولا مسافة متبقية: كلاهما يحتاج Routing Engine — قرار مفتوح
     (Infra §8)، و haversine مسافة خط مستقيم لا زمن وصول.
   - لا إشعار "اقترب العامل": يحتاج Push provider غير محسوم.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..models import Job, JobLocation
from .jobs import (
    JobError,
    JobNotFoundError,
    JobPermissionError,
    assert_can_view_job,
    get_job_for_contractor,
)

logger = logging.getLogger(__name__)

# هامش انحراف ساعات الأجهزة المقبول قبل رفض نقطة "من المستقبل".
CLOCK_SKEW_TOLERANCE_SECONDS = 60


class TrackingError(JobError):
    """أصل أخطاء التتبع."""

    code = "tracking_error"


class TrackingWindowClosedError(TrackingError):
    """
    المهمة خارج نافذة التتبع.

    409 لا 400: الطلب سليم شكلًا، والرفض بسبب حالة المورد الحالية —
    نفس منطق job_not_accepting_photos.
    """

    code = "tracking_window_closed"


class InvalidLocationError(TrackingError):
    """
    النقطة نفسها غير مقبولة (وقت من المستقبل مثلًا).

    400 لا 409: العيب في محتوى الطلب لا في حالة المورد.
    """

    code = "invalid_location"


# ------------------------------------------------------------
# الكتابة — المقاول المُسنَد وحده
# ------------------------------------------------------------
@transaction.atomic
def report_location(user, job_id, latitude, longitude, recorded_at, accuracy_m=None):
    """
    يسجّل آخر موقع للمقاول المُسنَد.

    ⚠️ يستبدل النقطة السابقة ولا يضيف إليها: update_or_create على علاقة
       واحد-لواحد، فلا يتراكم مسار.

    🔒 get_job_for_contractor يفرض المقاول المُسنَد حصرًا — لا الإدارة
       ولا العميل، لأن هذا مسار كتابة والكتابة فعل المقاول وحده.

    📌 recorded_at يصل من الجهاز ويُخزَّن كما هو، لكن received_at
       (ختم الخادم) هو مرجع حساب التقادم. ساعة جهاز مضبوطة خطأً لا
       تجعل نقطة قديمة تبدو حيّة.

    ⚠️ نقطة "من المستقبل" تُرفض: ساعة متقدّمة كانت ستُبقي is_stale=False
       إلى الأبد لو اعتُمد وقت الجهاز، والقبول الصامت يخفي جهازًا
       معطوبًا. السماح بهامش دقيقة واحدة لانحراف الساعات الطبيعي.
    """
    job = get_job_for_contractor(user, job_id)

    if not job.is_tracking_window_open():
        raise TrackingWindowClosedError(
            f"Job is {job.status} and no longer accepts location updates."
        )

    if recorded_at > timezone.now() + timedelta(seconds=CLOCK_SKEW_TOLERANCE_SECONDS):
        raise InvalidLocationError(
            "recorded_at is in the future; check the device clock."
        )

    location, created = JobLocation.objects.update_or_create(
        job=job,
        defaults={
            "latitude": latitude,
            "longitude": longitude,
            "accuracy_m": accuracy_m,
            "recorded_at": recorded_at,
        },
    )
    # full_clean بعد الكتابة لا قبلها: update_or_create يبني الكائن
    # داخليًا، والتحقق هنا يحرس حدود الإحداثيات على مستوى النموذج.
    location.full_clean()

    logger.info(
        "Job location reported (job_id=%s, contractor_user_id=%s, created=%s)",
        job.id,
        user.id,
        created,
    )

    return location


# ------------------------------------------------------------
# القراءة — العميل المالك أو الإدارة أو المقاول المُسنَد
# ------------------------------------------------------------
def get_tracking_for_job(user, job):
    """
    يعيد لقطة التتبع الكاملة: المهمة، والعقار، وآخر موقع إن وُجد.

    🔒 assert_can_view_job هي نفس بوابة عرض المهمة — فمن يرى المهمة يرى
       موقع من يؤديها، ولا أحد غيرهم.

    📌 يعيد قاموسًا لا نموذجًا: اللقطة تجمع ثلاثة مصادر (المهمة والعقار
       والموقع)، ولا كيان واحد يمثّلها.

    ⚠️ غياب الموقع ليس خطأً: يُعاد location=None. المقاول قد يكون لم
       يرسل بعد، أو أوقف مشاركة الموقع. الواجهة ترسم العقار وتعرض
       "في الطريق — الموقع غير متاح بعد".

    ⚠️ خارج النافذة يُعاد tracking_active=False وlocation=None حتى لو
       كانت نقطة مخزَّنة: النافذة تحكم العرض كما تحكم الكتابة، وإلا
       بقي آخر موقع ظاهرًا بعد انتهاء الخدمة.
    """
    assert_can_view_job(user, job)

    tracking_active = job.is_tracking_window_open()
    location = None

    if tracking_active:
        location = JobLocation.objects.filter(job=job).first()

    address = getattr(job.booking.property, "address", None)

    return {
        "job": job,
        "tracking_active": tracking_active,
        "location": location,
        "property_address": address,
    }


def get_tracking_by_job_id(user, job_id):
    """
    نفس اللقطة لكن عبر معرّف المهمة — يستعمله مسار المقاول بعد الكتابة.
    """
    job = (
        Job.objects.select_related(
            "booking",
            "booking__assigned_contractor",
            "booking__property",
            "booking__property__address",
            "location",
        )
        .filter(pk=job_id)
        .first()
    )
    if job is None:
        raise JobNotFoundError("Job not found.")

    return get_tracking_for_job(user, job)


def get_tracking_by_booking_id(user, booking_id):
    """
    يجلب المهمة عبر معرّف الحجز ثم لقطة تتبعها.

    📌 الجلب هنا لا في طبقة الـAPI (§43). حجز غير موجود، ومهمة غير
       موجودة، وحجز ليس لك — كلها JobNotFoundError/JobPermissionError
       فتوحّدها طبقة الـAPI في 404 واحد.
    """
    job = (
        Job.objects.select_related(
            "booking",
            "booking__assigned_contractor",
            "booking__property",
            "booking__property__address",
            "location",
        )
        .filter(booking_id=booking_id)
        .first()
    )
    if job is None:
        raise JobNotFoundError("No job found for this booking.")

    return get_tracking_for_job(user, job)


__all__ = [
    "TrackingError",
    "TrackingWindowClosedError",
    "InvalidLocationError",
    "JobPermissionError",
    "JobNotFoundError",
    "report_location",
    "get_tracking_for_job",
    "get_tracking_by_job_id",
    "get_tracking_by_booking_id",
]
