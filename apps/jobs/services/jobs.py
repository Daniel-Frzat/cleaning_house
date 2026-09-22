"""
Job Service — Jobs Domain (Change Set §20، §36.3)

إنشاء المهمة وقراءتها وانتقالات حالتها. كل الوصول يمر من هنا — طبقة الـAPI
لا تستدعي `.objects` مباشرة.

📌 دورة الحياة المكتملة (§36.3):
   ASSIGNED → (المقاول يبدأ) → IN_PROGRESS →
   (المقاول يعلن الإنجاز) → AWAITING_CUSTOMER_CONFIRMATION
              → (العميل يؤكّد) → COMPLETED

⚠️ لا إلغاء هنا: سياسة الإلغاء بند مفتوح (§18 — Important #12). لا تُضف
   حالة ولا دالة إلغاء في هذه المرحلة.

⚠️ لا تأكيد تلقائي بمرور الوقت: §36.3 يحسم أن العميل وحده يؤكّد — لا
   مهلة، ولا مهمة دورية، ولا بديل إداري. أي آلية زمنية هنا تخالف النص.

📌 الدفع للمقاول (Payout — §36.5): تأكيد العميل هو المُحفِّز الوحيد.
   يُطلق عبر transaction.on_commit بعد تثبيت الانتقال — نفس نمط hook
   الشحن (§43) وhook إنشاء المهمة (§45): فشل الدفع لا يجوز أن يُلغي
   تأكيدًا صحيحًا، ولا يُغيّر حالة المهمة.
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
    ينشئ مهمة بحالة ASSIGNED لحجز مؤكَّد.

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

    job = Job(booking=booking, status=JobStatus.ASSIGNED)
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


def is_assigned_contractor(user, job):
    """
    🔒 هل هذا المستخدم هو المقاول المُسنَد لهذه المهمة تحديدًا؟

    تُستعمل لقرار الكشف لا المنع: ملاحظات وصول العميل تُعاد لهذا المقاول
    وحده. مصدر الحقيقة واحد مع assert_can_view_job أدناه، فلا يتفرّع
    منطق الملكية في طبقة الـAPI.

    ⚠️ الإدارة ليست المقاول المُسنَد: has_admin_access لا يمنح هذا الحق.
    """
    if user is None or not user.is_authenticated:
        return False
    return _assigned_contractor_user_id(job.booking) == user.id


def assert_can_view_job(user, job):
    """
    🔒 من يرى المهمة: العميل المالك، أو الإدارة، أو المقاول المُسنَد.

    أي شخص آخر يُرفض. التمييز بين "لا صلاحية" و"غير موجود" يتركه المستدعي
    لطبقة الـAPI — التي توحّدهما في 404.
    """
    if user is None or not user.is_authenticated:
        raise JobPermissionError("Authentication required.")

    booking = job.booking

    is_admin = user.has_admin_access()
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

    if not user.has_contractor_access():
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


def list_jobs_for_contractor(user, status=None):
    """List assigned jobs so a contractor can recover them on any device."""
    if user is None or not user.is_authenticated:
        raise JobPermissionError("Authentication required.")
    if not user.has_contractor_access():
        raise JobPermissionError("Only contractors can view jobs.")

    queryset = (
        Job.objects.filter(booking__assigned_contractor__user=user)
        .select_related(
            "booking",
            "booking__property",
            "booking__property__address",
            "booking__assigned_contractor",
            "booking__payout",
        )
        .prefetch_related("booking__service_selections__service_type", "photos")
    )
    if status:
        queryset = queryset.filter(status=status)
    return queryset.order_by("-created_at")


# ------------------------------------------------------------
# انتقالات الحالة (§36.3)
# ------------------------------------------------------------
def _photo_types_present(job):
    """أنواع الصور الموجودة فعلًا لهذه المهمة."""
    return set(job.photos.values_list("photo_type", flat=True))


@transaction.atomic
def start_job(job, contractor_user):
    """
    يعلن المقاول المُسنَد بدء العمل: ASSIGNED → IN_PROGRESS.

    🔒 المقاول المُسنَد وحده. 🔒 من ASSIGNED وحدها.

    📌 هذه هي اللحظة التي لم تكن موجودة: القبول كان ينشئ المهمة جاهزة
       للتنفيذ فورًا، فلم يستطع العميل التمييز بين "قَبِل ولم يصل" و"يعمل
       الآن". البدء فعل صريح يفصل بينهما.

    ⚠️ لا يقبل الصور قبله: صورة "قبل" تُلتقط عند الموقع لا قبل الوصول.
    """
    if contractor_user is None or not contractor_user.is_authenticated:
        raise JobPermissionError("Authentication required.")

    if not contractor_user.has_contractor_access():
        raise JobPermissionError("Only contractors can start a job.")

    if _assigned_contractor_user_id(job.booking) != contractor_user.id:
        logger.warning(
            "Start-job denied (user_id=%s, job_id=%s)", contractor_user.id, job.id
        )
        raise JobPermissionError("This job is not assigned to you.")

    if job.status != JobStatus.ASSIGNED:
        raise InvalidJobStatusError(
            f"Job is {job.status} and cannot be started."
        )

    job.status = JobStatus.IN_PROGRESS
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at", "updated_at"])

    logger.info("Job started (job_id=%s, by=%s)", job.id, contractor_user.id)
    return job


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

    if not contractor_user.has_contractor_access():
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

    📌 يُطلق دفع المقاول (§36.5) بعد تثبيت المعاملة — لا داخلها. الدالة
       نفسها مسؤولة عن الانتقال وحده، والدفع أثر جانبي معزول.
    """
    if customer_user is None or not customer_user.is_authenticated:
        raise JobPermissionError("Authentication required.")

    if not customer_user.has_customer_access():
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

    # 📌 دفع المقاول فورًا بعد تثبيت التأكيد (§36.5) — بعد المعاملة لا
    #    داخلها: فشل المزوّد لا يجوز أن يُلغي تأكيدًا صحيحًا.
    # robust=True: طبقة حماية من الإطار فوق try/except الداخلي (Django 5.0+).
    transaction.on_commit(
        lambda: _release_payout_after_commit(job.booking), robust=True
    )

    return job


def _release_payout_after_commit(booking):
    """
    يُطلق دفع المقاول بعد تثبيت تأكيد العميل (§36.5).

    الاستثناءات تُبتلع وتُسجَّل: المهمة مؤكَّدة فعلًا، وخطأ في نطاق الدفع
    يجب ألا يتحول إلى 500 على طلب تأكيد ناجح. الدفعة الفاشلة تبقى مسجَّلة
    بحالة FAILED، والمهمة والحجز كما هما (سياسة مفتوحة — راجع
    payouts/services).
    """
    from apps.payouts.services.payouts import release_payout_for_booking

    try:
        release_payout_for_booking(booking)
    except Exception:  # noqa: BLE001 — نسجّل ولا نُسقط طلبًا ناجحًا
        logger.exception(
            "Automatic payout failed after job confirmation (booking_id=%s)",
            booking.id,
        )


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
