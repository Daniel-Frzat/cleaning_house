"""
Job Service — Jobs Domain (Change Set §20، §36.3)

إنشاء المهمة وقراءتها وانتقالات حالتها. كل الوصول يمر من هنا — طبقة الـAPI
لا تستدعي `.objects` مباشرة.

📌 دورة الحياة المكتملة (§36.3):
   IN_PROGRESS → (المقاول يعلن الإنجاز) → AWAITING_CUSTOMER_CONFIRMATION
              → (العميل يؤكّد) → COMPLETED

⚠️ لا إلغاء هنا: سياسة الإلغاء بند مفتوح (§18 — Important #12). لا تُضف
   حالة ولا دالة إلغاء في هذه المرحلة.

⚠️ لا تأكيد تلقائي بمرور الوقت: §36.3 يحسم أن العميل وحده يؤكّد — لا
   مهلة، ولا مهمة دورية، ولا بديل إداري. أي آلية زمنية هنا تخالف النص.

⚠️ الدفع للمقاول (Payout — §36.5) مؤجَّل صراحةً: confirm_job_completion
   تنفّذ الانتقال وحده ولا تُطلق أي أثر جانبي.
"""

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.roles import ConfirmedRole
from apps.bookings.models import Booking, BookingStatus

from ..models import Job, JobStatus, PhotoType

logger = logging.getLogger(__name__)


class JobError(Exception):
    """أصل أخطاء نطاق تنفيذ المهام."""

    code = "job_error"


class JobPermissionError(JobError):
    """لا صلاحية للوصول إلى هذه المهمة."""

    code = "job_forbidden"


class JobNotFoundError(JobError):
    code = "job_not_found"


class BookingNotConfirmedError(JobError):
    """لا مهمة قبل تأكيد الحجز."""

    code = "booking_not_confirmed"


class JobAlreadyExistsError(JobError):
    """حجز واحد = مهمة واحدة."""

    code = "job_already_exists"


class InvalidJobStatusError(JobError):
    """الانتقال غير مسموح من الحالة الحالية."""

    code = "invalid_job_status"


class MissingProofPhotosError(JobError):
    """
    لا إعلان إنجاز بلا دليل مصوَّر.

    صورة "قبل" وصورة "بعد" على الأقل: العميل يؤكّد بناءً على دليل، وإعلان
    الإنجاز بلا صور يجعل التأكيد قرارًا بلا أساس.
    """

    code = "missing_proof_photos"


@transaction.atomic
def create_job_for_booking(booking):
    """
    ينشئ مهمة بحالة IN_PROGRESS لحجز مؤكَّد.

    ⚠️ الحجز يجب أن يكون CONFIRMED: لا مهمة لحجز PENDING (لا مقاول
       مُسنَدًا بعد، فلا من ينفّذ).

    🔒 دفاع في العمق: فحص هنا + قيد OneToOne في قاعدة البيانات.
    """
    if booking.status != BookingStatus.CONFIRMED:
        raise BookingNotConfirmedError(
            f"Booking must be CONFIRMED before a job starts (currently {booking.status})."
        )

    if Job.objects.filter(booking=booking).exists():
        raise JobAlreadyExistsError("This booking already has a job.")

    job = Job(booking=booking, status=JobStatus.IN_PROGRESS)
    job.full_clean()

    try:
        job.save()
    except IntegrityError as exc:
        raise JobAlreadyExistsError("This booking already has a job.") from exc

    logger.info("Job created (job_id=%s, booking_id=%s)", job.id, booking.id)
    return job


def _assigned_contractor_user_id(booking):
    """معرّف مستخدم المقاول المُسنَد، أو None."""
    profile = booking.assigned_contractor
    return profile.user_id if profile is not None else None


def assert_can_view_job(user, job):
    """
    🔒 من يرى المهمة: العميل المالك، أو الإدارة، أو المقاول المُسنَد.

    أي شخص آخر يُرفض. التمييز بين "لا صلاحية" و"غير موجود" يتركه المستدعي
    لطبقة الـAPI — التي توحّدهما في 404.
    """
    if user is None or not user.is_authenticated:
        raise JobPermissionError("Authentication required.")

    booking = job.booking

    is_admin = user.role == ConfirmedRole.ADMIN
    is_owner = booking.customer_id == user.id
    is_assigned = _assigned_contractor_user_id(booking) == user.id

    if not (is_admin or is_owner or is_assigned):
        logger.warning(
            "Job access denied (user_id=%s, job_id=%s)", user.id, job.id
        )
        raise JobPermissionError("You do not have access to this job.")


def get_job_by_booking_id(user, booking_id):
    """
    يعيد مهمة حجز بمعرّف الحجز، بعد فحص الصلاحية.

    🔒 جلب الحجز يعيش هنا لا في طبقة الـAPI: الحجز غير الموجود والحجز بلا
       مهمة يرفعان الخطأ نفسه، فلا تملك الواجهة ما تميّز به بينهما.
    """
    booking = (
        Booking.objects.filter(pk=booking_id)
        .select_related("assigned_contractor")
        .first()
    )
    if booking is None:
        raise JobNotFoundError("No job found for this booking.")

    job = (
        Job.objects.filter(booking=booking)
        .select_related("booking", "booking__assigned_contractor")
        .prefetch_related("photos")
        .first()
    )
    if job is None:
        raise JobNotFoundError("No job found for this booking.")

    assert_can_view_job(user, job)
    return job


def get_job_for_contractor(user, job_id):
    """
    يعيد مهمة يملك هذا المقاول حق العمل عليها.

    🔒 المقاول المُسنَد وحده — لا الإدارة ولا العميل: هذه الدالة تخدم
       مسارات الكتابة (رفع الصور)، والكتابة فعل المقاول المُسنَد حصرًا.
    """
    if user is None or not user.is_authenticated:
        raise JobPermissionError("Authentication required.")

    if user.role != ConfirmedRole.CONTRACTOR:
        raise JobPermissionError("Only contractors can act on jobs.")

    job = (
        Job.objects.filter(pk=job_id)
        .select_related("booking", "booking__assigned_contractor")
        .first()
    )
    if job is None:
        raise JobNotFoundError("Job not found.")

    if _assigned_contractor_user_id(job.booking) != user.id:
        logger.warning(
            "Job write access denied (user_id=%s, job_id=%s)", user.id, job.id
        )
        raise JobPermissionError("This job is not assigned to you.")

    return job


# ------------------------------------------------------------
# انتقالات الحالة (§36.3)
# ------------------------------------------------------------
def _photo_types_present(job):
    """أنواع الصور الموجودة فعلًا لهذه المهمة."""
    return set(job.photos.values_list("photo_type", flat=True))


@transaction.atomic
def mark_job_done(job, contractor_user):
    """
    يعلن المقاول المُسنَد إنجاز العمل (§36.3).

    🔒 المقاول المُسنَد وحده. 🔒 من IN_PROGRESS وحدها.

    ⚠️ يتطلب دليلًا مصوَّرًا: صورة BEFORE وصورة AFTER على الأقل. الرفض
       هنا ليس شكليًا — العميل سيؤكّد بناءً على هذه الصور.

    ⚠️ لا يُكمل المهمة: الحالة تصبح AWAITING_CUSTOMER_CONFIRMATION،
       والإكمال فعل العميل وحده.
    """
    if contractor_user is None or not contractor_user.is_authenticated:
        raise JobPermissionError("Authentication required.")

    if contractor_user.role != ConfirmedRole.CONTRACTOR:
        raise JobPermissionError("Only contractors can mark a job done.")

    if _assigned_contractor_user_id(job.booking) != contractor_user.id:
        logger.warning(
            "Mark-done denied (user_id=%s, job_id=%s)", contractor_user.id, job.id
        )
        raise JobPermissionError("This job is not assigned to you.")

    if job.status != JobStatus.IN_PROGRESS:
        raise InvalidJobStatusError(
            f"Job is {job.status} and cannot be marked done."
        )

    present = _photo_types_present(job)
    missing = {PhotoType.BEFORE, PhotoType.AFTER} - present
    if missing:
        raise MissingProofPhotosError(
            "At least one BEFORE photo and one AFTER photo are required before "
            f"marking the job done (missing: {', '.join(sorted(missing))})."
        )

    job.status = JobStatus.AWAITING_CUSTOMER_CONFIRMATION
    job.marked_done_at = timezone.now()
    job.save(update_fields=["status", "marked_done_at", "updated_at"])

    logger.info(
        "Job marked done (job_id=%s, by=%s)", job.id, contractor_user.id
    )
    return job


@transaction.atomic
def confirm_job_completion(job, customer_user):
    """
    يؤكّد العميل إنجاز العمل، فتكتمل المهمة (§36.3).

    🔒 عميل الحجز نفسه حصرًا — لا الإدارة، ولا أي مستخدم آخر مهما كان
       دوره. §36.3 يحسم أن التحقق فعل العميل وحده.

    ⚠️ لا مهلة ولا تأكيد تلقائي: لا شيء في هذه الدالة (ولا في أي مهمة
       دورية) ينقل الحالة بمرور الوقت.

    ⚠️ لا أثر جانبي: الدفع للمقاول (§36.5) مؤجَّل، ولا إشعارات. هذه
       الدالة مسؤولة عن الانتقال وحده — وهذا مقصود، لا نقص.
    """
    if customer_user is None or not customer_user.is_authenticated:
        raise JobPermissionError("Authentication required.")

    if customer_user.role != ConfirmedRole.CUSTOMER:
        raise JobPermissionError("Only the booking's customer can confirm a job.")

    # 🔒 العميل صاحب الحجز بعينه — لا يكفي أن يكون دوره CUSTOMER
    if job.booking.customer_id != customer_user.id:
        logger.warning(
            "Job confirmation denied (user_id=%s, job_id=%s)",
            customer_user.id,
            job.id,
        )
        raise JobPermissionError("Only the booking's own customer can confirm it.")

    if job.status != JobStatus.AWAITING_CUSTOMER_CONFIRMATION:
        raise InvalidJobStatusError(
            f"Job is {job.status} and cannot be confirmed."
        )

    job.status = JobStatus.COMPLETED
    job.confirmed_at = timezone.now()
    job.save(update_fields=["status", "confirmed_at", "updated_at"])

    logger.info(
        "Job completion confirmed (job_id=%s, by=%s)", job.id, customer_user.id
    )
    return job


def get_job_by_booking_for_customer(customer_user, booking_id):
    """
    يجلب مهمة حجز لغرض التأكيد — الجلب في طبقة الخدمة لا الـAPI.

    لا يفحص الملكية هنا: confirm_job_completion هي نقطة الإنفاذ، وهذا
    الفاصل يجعل رسالة الرفض (403) مميَّزة عن "غير موجود" (404).
    """
    job = (
        Job.objects.filter(booking_id=booking_id)
        .select_related("booking", "booking__assigned_contractor")
        .first()
    )
    if job is None:
        raise JobNotFoundError("No job found for this booking.")

    return job


def get_job_for_transition(job_id):
    """يجلب مهمة بمعرّفها دون فحص صلاحية — الفحص في دالة الانتقال."""
    job = (
        Job.objects.filter(pk=job_id)
        .select_related("booking", "booking__assigned_contractor")
        .prefetch_related("photos")
        .first()
    )
    if job is None:
        raise JobNotFoundError("Job not found.")

    return job
