"""
Jobs API — Jobs Domain (Change Set §36.3؛ Infra §7)

نقاط النهاية (محمية بـJWT):
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


def _serialize_job(job, photos_with_urls, *, include_storage_key):
    return {
        "id": job.id,
        "booking_id": job.booking_id,
        "status": job.status,
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
    is_admin = request.user.role == ConfirmedRole.ADMIN

    return 201, _serialize_photo(photo, signed_url, include_storage_key=is_admin)


# ------------------------------------------------------------
# GET /bookings/{id}/job
# ------------------------------------------------------------
@booking_router.get(
    "/{booking_id}/job",
    response={200: JobOut, 404: ErrorOut},
    summary="Retrieve the job for a booking (owner customer, admin, or assigned contractor)",
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
    is_admin = request.user.role == ConfirmedRole.ADMIN

    return 200, _serialize_job(
        job, photos_with_urls, include_storage_key=is_admin
    )


# ------------------------------------------------------------
# POST /contractor/jobs/{job_id}/mark-done
# ------------------------------------------------------------
@contractor_router.post(
    "/jobs/{job_id}/mark-done",
    response={200: JobOut, 400: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    summary="Mark a job done (assigned contractor only)",
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
    is_admin = request.user.role == ConfirmedRole.ADMIN

    return 200, _serialize_job(job, photos_with_urls, include_storage_key=is_admin)


# ------------------------------------------------------------
# POST /bookings/{id}/job/confirm
# ------------------------------------------------------------
@booking_router.post(
    "/{booking_id}/job/confirm",
    response={200: JobOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    summary="Confirm job completion (the booking's own customer only)",
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
