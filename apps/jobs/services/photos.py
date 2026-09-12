"""
Job Photo Service — Jobs Domain (Change Set §36.3؛ Infra §7)

رفع صور قبل/بعد عبر مزوّد التخزين المُعرَّف في الإعدادات.

⚠️ الـadapter وحده يتعامل مع البايتات: هذه الطبقة تمرّرها ولا تكتبها ولا
   تفسّرها، وتخزّن المرجع المبهم العائد منه فقط (Infra §7).

🔒 الرفع مقصور على المقاول المُسنَد، وعلى مهمة قيد التنفيذ وحدها.
"""

import logging

from django.db import transaction

from ..adapters import get_storage_adapter
from ..models import JobPhoto, PhotoType
from .jobs import JobError, get_job_for_contractor

logger = logging.getLogger(__name__)


class PhotoError(JobError):
    """أصل أخطاء الصور."""

    code = "photo_error"


class JobNotAcceptingPhotosError(PhotoError):
    """
    المهمة لم تعد تقبل صورًا.

    بعد إعلان الإنجاز أو اكتمال المهمة تتجمّد الأدلة: إضافة صورة لاحقًا
    تغيّر سجلًا استُند إليه في تأكيد العميل.
    """

    code = "job_not_accepting_photos"


class InvalidPhotoTypeError(PhotoError):
    code = "invalid_photo_type"


class EmptyPhotoError(PhotoError):
    """ملف فارغ ليس صورة."""

    code = "empty_photo"


@transaction.atomic
def upload_job_photo(user, job_id, photo_type, file_bytes, content_type):
    """
    يرفع صورة لمهمة عبر الـadapter ويسجّل مرجعها.

    🔒 الصلاحية: المقاول المُسنَد لهذه المهمة حصرًا (get_job_for_contractor).
    🔒 الحالة: IN_PROGRESS فقط.
    """
    job = get_job_for_contractor(user, job_id)

    if photo_type not in PhotoType.values:
        raise InvalidPhotoTypeError(f"Invalid photo type: {photo_type}.")

    if not job.accepts_photos():
        raise JobNotAcceptingPhotosError(
            f"Job is {job.status} and no longer accepts photos."
        )

    if not file_bytes:
        raise EmptyPhotoError("Uploaded file is empty.")

    adapter = get_storage_adapter()
    result = adapter.upload(
        file_bytes=file_bytes,
        content_type=content_type or "application/octet-stream",
        path_hint=f"jobs/{job.id}/{photo_type.lower()}",
    )

    photo = JobPhoto(
        job=job,
        photo_type=photo_type,
        storage_key=result.storage_key,
        uploaded_by=user,
    )
    photo.full_clean()
    photo.save()

    logger.info(
        "Job photo uploaded (photo_id=%s, job_id=%s, type=%s, by=%s)",
        photo.id,
        job.id,
        photo_type,
        user.id,
    )
    return photo


def build_signed_url(storage_key):
    """
    🔒 يحوّل المرجع المبهم إلى رابط مؤقّت — لا يُكشف المرجع نفسه للعميل.
    """
    return get_storage_adapter().get_signed_url(storage_key)


def list_photos_with_urls(job):
    """
    يعيد [(photo, signed_url)] لكل صور المهمة.

    الرابط يُولَّد لحظيًا ولا يُخزَّن: الروابط المؤقّتة تنتهي، وتخزينها
    يصنع قيمة تكذب بعد انتهائها.
    """
    adapter = get_storage_adapter()
    return [
        (photo, adapter.get_signed_url(photo.storage_key))
        for photo in job.photos.all()
    ]
