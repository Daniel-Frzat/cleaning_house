"""
Jobs API — Jobs Domain (Change Set §36.3؛ Infra §7)

نقاط النهاية (محمية بـJWT):
    POST /api/contractor/jobs/{job_id}/start      إعلان بدء العمل (المقاول المُسنَد)
    POST /api/contractor/jobs/{job_id}/photos     رفع صورة (المقاول المُسنَد)
    POST /api/contractor/jobs/{job_id}/mark-done  إعلان الإنجاز (المقاول المُسنَد)
    GET  /api/bookings/{id}/job                   عرض المهمة (عميل/إدارة/مقاول)
    POST /api/bookings/{id}/job/confirm           تأكيد الإنجاز (عميل الحجز وحده)

📌 §36.3: العميل وحده يؤكّد الإنجاز — لا بديل إداري، ولا تأكيد تلقائي
   بمرور الوقت. لا توجد هنا (ولا في أي مهمة دورية) آلية زمنية للانتقال.

🔒 storage_key لا يُكشف للعميل ولا للمقاول: يُستبدل بـsigned_url دائمًا،
   ويظهر خامًا للإدارة وحدها.

🔒 404 موحّد لكل حالات التعذّر في مسار العرض — لا يكشف الرد وجود حجز
   أو مهمة لغير أصحابها.

⚠️ لا استعلام ORM في هذا الملف: كل شيء عبر طبقة الخدمة (راجع §43).
"""

from ninja import File, Router, UploadedFile
from ninja_jwt.authentication import JWTAuth

from apps.accounts.roles import ConfirmedRole

from ..services import jobs as jobs_svc
from ..services import photos as photos_svc
from .schemas import ErrorOut, JobOut, JobPhotoOut

# ------------------------------------------------------------
# Router للمقاول (يُركَّب على /contractor)
# ------------------------------------------------------------
contractor_router = Router(tags=["Contractor Jobs"], auth=JWTAuth())

# ------------------------------------------------------------
# Router لعرض المهمة عبر الحجز (يُركَّب على /bookings)
# ------------------------------------------------------------
booking_router = Router(tags=["Jobs"], auth=JWTAuth())


def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


def _not_found():
    return _error(404, "job_not_found", "No job found for this booking.")


def _serialize_photo(photo, signed_url, *, include_storage_key):
    return {
        "id": photo.id,
        "photo_type": photo.photo_type,
        "signed_url": signed_url,
        "storage_key": photo.storage_key if include_storage_key else None,
        "uploaded_at": photo.uploaded_at,
    }


def _serialize_job(job, photos_with_urls, *, include_storage_key,
                   include_access_notes=False):
    """
    🔒 include_access_notes: ملاحظات وصول العميل لا تُكشف إلا للمقاول
       المُسنَد. العميل يعرف ما كتبه بنفسه، والإدارة لا تحتاجه، والمقاول
       الذي لم يقبل بعد لا يصل إلى هذا الـendpoint أصلًا.

       الافتراضي False: أي مسار جديد يُضاف لاحقًا يبدأ محجوبًا ما لم
       يطلب الكشف صراحةً.
    """
    return {
        "id": job.id,
        "booking_id": job.booking_id,
        "status": job.status,
        "started_at": job.started_at,
        "access_notes": job.booking.access_notes if include_access_notes else None,
        "marked_done_at": job.marked_done_at,
        "confirmed_at": job.confirmed_at,
        "photos": [
            _serialize_photo(p, url, include_storage_key=include_storage_key)
            for p, url in photos_with_urls
        ],
        "created_at": job.created_at,
    }


# ------------------------------------------------------------
# POST /contractor/jobs/{job_id}/photos
# ------------------------------------------------------------
@contractor_router.post(
    "/jobs/{job_id}/photos",
    response={201: JobPhotoOut, 400: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    summary="Upload a before/after photo (assigned contractor only)",
    description=(
        "**Who may call:** the `CONTRACTOR` assigned to this job, and no one "
        "else.\n\n"
        "**Preconditions:** the job must still be in progress — photos are "
        "refused with `409` once it has been marked done. `photo_type` must be "
        "`BEFORE` or `AFTER`, and the file must not be empty.\n\n"
        "Multipart upload. The bytes are handed to the configured storage "
        "provider and are not written by this endpoint.\n\n"
        "**Side effects:** the photo counts towards the proof required by "
        "`mark-done`, which needs at least one `BEFORE` and one `AFTER` photo.\n\n"
        "The response carries a `signed_url` for viewing the photo. The raw "
        "`storage_key` is returned to administrators only and is `null` for "
        "everyone else.\n\n"
        "**Note:** no storage provider ships with this build, so uploads fail "
        "until one is configured."
    ),
    openapi_extra={
        "responses": {
            400: {"description": "`photo_type` is not `BEFORE`/`AFTER`, or the uploaded file is empty."},
            403: {"description": "The caller is not the contractor assigned to this job."},
            404: {"description": "No job with this id."},
            409: {"description": "The job is no longer accepting photos."},
        }
    },
)
def upload_photo(
    request,
    job_id: str,
    photo_type: str,
    file: UploadedFile = File(...),
):
    """
    🔒 المقاول المُسنَد وحده، وأثناء التنفيذ وحده.

    ⚠️ البايتات تُمرَّر إلى الـadapter ولا تُكتب هنا.
    """
    try:
        photo = photos_svc.upload_job_photo(
            request.user,
            job_id,
            photo_type=photo_type,
            file_bytes=file.read(),
            content_type=file.content_type,
        )
    except jobs_svc.JobPermissionError as exc:
        return _error(403, exc.code, str(exc))
    except jobs_svc.JobNotFoundError as exc:
        return _error(404, exc.code, "Job not found.")
    except photos_svc.JobNotAcceptingPhotosError as exc:
        # 409: تعارض مع حالة المورد الحالية
        return _error(409, exc.code, str(exc))
    except (photos_svc.InvalidPhotoTypeError, photos_svc.EmptyPhotoError) as exc:
        return _error(400, exc.code, str(exc))
    except jobs_svc.JobError as exc:
        return _error(400, exc.code, str(exc))

    signed_url = photos_svc.build_signed_url(photo.storage_key)
    is_admin = request.user.has_admin_access()

    return 201, _serialize_photo(photo, signed_url, include_storage_key=is_admin)


# ------------------------------------------------------------
# GET /bookings/{id}/job
# ------------------------------------------------------------
@booking_router.get(
    "/{booking_id}/job",
    response={200: JobOut, 404: ErrorOut},
    summary="Retrieve the job for a booking (owner customer, admin, or assigned contractor)",
    description=(
        "**Who may call:** the booking's own customer, the contractor assigned to "
        "it, or an `ADMIN`.\n\n"
        "**Preconditions:** a job exists only after a contractor has accepted the "
        "offer and the booking has been confirmed.\n\n"
        "Returns the job's status, its timestamps, and its photos with viewing "
        "URLs. The raw `storage_key` of each photo is returned to administrators "
        "only and is `null` for everyone else.\n\n"
        "**Side effects:** none — read-only.\n\n"
        "Every failure returns the same `404`: no such booking, no job yet, and "
        "not being entitled to see it are indistinguishable in the response."
    ),
    openapi_extra={
        "responses": {
            404: {
                "description": (
                    "No such booking, no job for it yet, or the caller is not "
                    "entitled to see it — deliberately indistinguishable."
                )
            }
        }
    },
)
def retrieve_job(request, booking_id: str):
    """
    🔒 404 موحّد: لا حجز، أو لا مهمة، أو ليست لك — لا تمييز بينها.
    """
    try:
        job = jobs_svc.get_job_by_booking_id(request.user, booking_id)
    except (jobs_svc.JobPermissionError, jobs_svc.JobNotFoundError):
        return _not_found()

    photos_with_urls = photos_svc.list_photos_with_urls(job)
    is_admin = request.user.has_admin_access()

    return 200, _serialize_job(
        job,
        photos_with_urls,
        include_storage_key=is_admin,
        # 🔒 العميل يعرف ما كتبه، والإدارة لا تحتاجه — المقاول المُسنَد وحده
        include_access_notes=jobs_svc.is_assigned_contractor(request.user, job),
    )


# ------------------------------------------------------------
# POST /contractor/jobs/{job_id}/mark-done
# ------------------------------------------------------------
@contractor_router.post(
    "/jobs/{job_id}/start",
    response={200: JobOut, 400: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    summary="Start a job (assigned contractor only)",
    description=(
        "**Who may call:** the `CONTRACTOR` assigned to this job, and no one "
        "else. No request body.\n\n"
        "**Preconditions:** the job must be `ASSIGNED` — that is, the offer was "
        "accepted but work has not begun.\n\n"
        "Moves the job to `IN_PROGRESS` and records `started_at`. This is the "
        "contractor's \"I have arrived / starting now\" action, and it is what "
        "lets a customer tell *accepted but not here yet* from *working right "
        "now* — the two were a single instant before this endpoint existed.\n\n"
        "**Side effects:** photo upload becomes available. A job that is only "
        "`ASSIGNED` refuses photos with `409`, because a BEFORE photo taken "
        "before arriving does not describe the property."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not the contractor assigned to this job."},
            404: {"description": "No job with this id."},
            409: {"description": "The job is not `ASSIGNED` — it has already started, or is further along."},
        }
    },
)
def start_job(request, job_id: str):
    """
    📌 ASSIGNED → IN_PROGRESS. الفعل الصريح الذي يفصل القبول عن البدء.
    """
    try:
        job = jobs_svc.get_job_for_transition(job_id)
        job = jobs_svc.start_job(job, request.user)
    except jobs_svc.JobNotFoundError:
        return _error(404, "job_not_found", "Job not found.")
    except jobs_svc.JobPermissionError as exc:
        return _error(403, exc.code, str(exc))
    except jobs_svc.InvalidJobStatusError as exc:
        # 409: تعارض مع حالة المورد الحالية
        return _error(409, exc.code, str(exc))
    except jobs_svc.JobError as exc:
        return _error(400, exc.code, str(exc))

    photos_with_urls = photos_svc.list_photos_with_urls(job)
    is_admin = request.user.has_admin_access()

    # المسار محصور بالمقاول المُسنَد (403 لغيره)، فالكشف هنا آمن
    return 200, _serialize_job(
        job, photos_with_urls, include_storage_key=is_admin,
        include_access_notes=True,
    )


@contractor_router.post(
    "/jobs/{job_id}/mark-done",
    response={200: JobOut, 400: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    summary="Mark a job done (assigned contractor only)",
    description=(
        "**Who may call:** the `CONTRACTOR` assigned to this job, and no one "
        "else.\n\n"
        "**Preconditions:** the job must be in progress, and **at least one "
        "`BEFORE` photo and one `AFTER` photo must already be uploaded** — "
        "without both, the request is refused with `400`.\n\n"
        "**Side effects:** the job moves to `AWAITING_CUSTOMER_CONFIRMATION` and "
        "stops accepting photos. It does **not** complete the job and does not "
        "release the payout: only the customer can complete it, via "
        "`POST /api/bookings/{booking_id}/job/confirm`. Nothing completes the job "
        "automatically if the customer never confirms."
    ),
    openapi_extra={
        "responses": {
            400: {"description": "The required `BEFORE` and `AFTER` photos are not both present."},
            403: {"description": "The caller is not the contractor assigned to this job."},
            404: {"description": "No job with this id."},
            409: {"description": "The job is not in a state that can be marked done."},
        }
    },
)
def mark_done(request, job_id: str):
    """
    ⚠️ يتطلب صورة BEFORE وصورة AFTER على الأقل — 400 بدونهما.
    ⚠️ لا يُكمل المهمة: الإكمال فعل العميل وحده (§36.3).
    """
    try:
        job = jobs_svc.get_job_for_transition(job_id)
        job = jobs_svc.mark_job_done(job, request.user)
    except jobs_svc.JobNotFoundError:
        return _error(404, "job_not_found", "Job not found.")
    except jobs_svc.JobPermissionError as exc:
        return _error(403, exc.code, str(exc))
    except jobs_svc.MissingProofPhotosError as exc:
        return _error(400, exc.code, str(exc))
    except jobs_svc.InvalidJobStatusError as exc:
        # 409: تعارض مع حالة المورد الحالية
        return _error(409, exc.code, str(exc))
    except jobs_svc.JobError as exc:
        return _error(400, exc.code, str(exc))

    photos_with_urls = photos_svc.list_photos_with_urls(job)
    is_admin = request.user.has_admin_access()

    # المسار محصور بالمقاول المُسنَد (403 لغيره)، فالكشف هنا آمن
    return 200, _serialize_job(
        job, photos_with_urls, include_storage_key=is_admin,
        include_access_notes=True,
    )


# ------------------------------------------------------------
# POST /bookings/{id}/job/confirm
# ------------------------------------------------------------
@booking_router.post(
    "/{booking_id}/job/confirm",
    response={200: JobOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    summary="Confirm job completion (the booking's own customer only)",
    description=(
        "**Who may call:** the booking's **own customer**, exclusively. Not an "
        "administrator, not another customer, and not the contractor — there is "
        "no administrative override, and no timeout that confirms on the "
        "customer's behalf.\n\n"
        "**Preconditions:** the contractor must have marked the job done, so the "
        "job is `AWAITING_CUSTOMER_CONFIRMATION`.\n\n"
        "**Side effects:** the job becomes `COMPLETED`, and the contractor's "
        "payout for this booking is released immediately afterwards. The payout "
        "runs after the confirmation is committed and is isolated from it: if the "
        "payout provider fails, the job stays validly completed and the payout is "
        "recorded as failed rather than returned as an error here.\n\n"
        "Photo `storage_key`s are never included in this response."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not the booking's own customer."},
            404: {"description": "No such booking, or no job for it."},
            409: {"description": "The job has not been marked done yet, or is already completed."},
        }
    },
)
def confirm_job(request, booking_id: str):
    """
    🔒 عميل الحجز بعينه — لا الإدارة ولا عميل آخر (§36.3).

    ⚠️ هذا هو الحدث الذي سيُطلق Payout مستقبلًا (§36.5) — ولا شيء منه
       يُنفَّذ الآن: الانتقال وحده.
    """
    try:
        job = jobs_svc.get_job_by_booking_for_customer(request.user, booking_id)
        job = jobs_svc.confirm_job_completion(job, request.user)
    except jobs_svc.JobNotFoundError:
        return _not_found()
    except jobs_svc.JobPermissionError as exc:
        return _error(403, exc.code, str(exc))
    except jobs_svc.InvalidJobStatusError as exc:
        return _error(409, exc.code, str(exc))
    except jobs_svc.JobError as exc:
        return _error(409, exc.code, str(exc))

    photos_with_urls = photos_svc.list_photos_with_urls(job)

    return 200, _serialize_job(job, photos_with_urls, include_storage_key=False)
