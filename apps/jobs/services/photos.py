"""
Job Photo Service — Jobs Domain (Change Set §36.3؛ Infra §7)

رفع صور قبل/بعد عبر مزوّد التخزين المُعرَّف في الإعدادات.

⚠️ الـadapter وحده يتعامل مع البايتات: هذه الطبقة تمرّرها ولا تكتبها ولا
   تفسّرها، وتخزّن المرجع المبهم العائد منه فقط (Infra §7).

🔒 الرفع مقصور على المقاول المُسنَد، وعلى مهمة قيد التنفيذ وحدها.
"""

import logging

from django.conf import settings
from django.db import transaction

from ..adapters import get_storage_adapter
from ..adapters.base import StorageUnavailableError
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


class PhotoTooLargeError(PhotoError):
    code = "photo_too_large"


class UnsupportedPhotoFormatError(PhotoError):
    """
    ليس صورة JPEG/PNG/WebP/HEIC.

    🔒 يُفحص محتوى الملف نفسه لا content_type المُرسَل (يتحكم فيه العميل):
       ملف HTML أو SVG باسم صورة كان سيُخزَّن ويُقدَّم عبر رابط موقَّع.
    """

    code = "unsupported_photo_format"


class TooManyPhotosError(PhotoError):
    code = "too_many_photos"


class PhotoStorageUnavailableError(PhotoError):
    """The photo could not be stored right now; try again."""

    code = "photo_storage_unavailable"


class PhotoNotFoundError(PhotoError):
    """No photo with this id on this job."""

    code = "photo_not_found"


def max_photo_bytes():
    return getattr(settings, "JOB_PHOTO_MAX_BYTES", 10 * 1024 * 1024)


def sniff_image_type(data):
    """
    نوع الصورة من بايتاتها الأولى (magic bytes)، أو None.

    يعيد content_type قانونيًا يُخزَّن بدل ما أرسله العميل.
    """
    head = bytes(data[:16])
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    # HEIC/HEIF (كاميرا iPhone): صندوق ftyp عند الإزاحة 4
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"hevc", b"heif", b"mif1", b"msf1"):
        return "image/heic"
    return None


@transaction.atomic
def upload_job_photo(user, job_id, photo_type, file_bytes, content_type):
    """
    يرفع صورة لمهمة عبر الـadapter ويسجّل مرجعها.

    🔒 الصلاحية: المقاول المُسنَد لهذه المهمة حصرًا (get_job_for_contractor).
    🔒 الحالة: IN_PROGRESS فقط — بعد قفل الصف، فلا تُضاف صورة بعد mark-done.
    🔒 المحتوى: صورة حقيقية (magic bytes)، ضمن الحجم والعدد المسموحين.
    """
    job = get_job_for_contractor(user, job_id, lock=True)

    if photo_type not in PhotoType.values:
        raise InvalidPhotoTypeError(f"Invalid photo type: {photo_type}.")

    if not job.accepts_photos():
        raise JobNotAcceptingPhotosError(
            f"Job is {job.status} and no longer accepts photos."
        )

    if not file_bytes:
        raise EmptyPhotoError("Uploaded file is empty.")

    if len(file_bytes) > max_photo_bytes():
        raise PhotoTooLargeError(
            f"Photo exceeds the {max_photo_bytes() // (1024 * 1024)} MB limit."
        )

    detected_type = sniff_image_type(file_bytes)
    if detected_type is None:
        raise UnsupportedPhotoFormatError("Only JPEG, PNG, WebP or HEIC photos are accepted.")

    limit = getattr(settings, "JOB_PHOTO_MAX_PER_JOB", 30)
    if limit and job.photos.count() >= limit:
        raise TooManyPhotosError(f"A job can have at most {limit} photos.")

    adapter = get_storage_adapter()
    try:
        result = adapter.upload(
            file_bytes=file_bytes,
            # النوع المكتشف من المحتوى لا ما أرسله العميل
            content_type=detected_type,
            path_hint=f"jobs/{job.id}/{photo_type.lower()}",
        )
    except StorageUnavailableError as exc:
        # لا صف بلا ملف: الرفض قبل إنشاء JobPhoto
        raise PhotoStorageUnavailableError("The photo could not be stored. Please try again.") from exc

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


@transaction.atomic
def delete_job_photo(user, job_id, photo_id):
    """
    يزيل صورة رفعها المقاول بالخطأ أو يريد استبدالها (تقرير التطبيق B21).

    🔒 نفس بوابة الرفع: المقاول المُسنَد، والمهمة IN_PROGRESS بعد قفل الصف.
       بعد mark-done الصور دليلٌ يؤكد عليه العميل فلا تُمس.
    📌 لا فحص لـ"صورة قبل وبعد" هنا: الشرط يُفرض عند mark-done، فحذف آخر
       صورة BEFORE مسموح ثم يجب رفع غيرها قبل الإنجاز.
    ⚠️ حذف الملف من التخزين بعد نجاح المعاملة فقط، وفشله لا يُفشل الطلب:
       السجل هو مصدر الحقيقة، والملف اليتيم أهون من صورة معروضة بلا ملف.
    """
    job = get_job_for_contractor(user, job_id, lock=True)

    if not job.accepts_photos():
        raise JobNotAcceptingPhotosError(
            f"Job is {job.status} and its photos can no longer be changed."
        )

    photo = job.photos.filter(pk=photo_id).first()
    if photo is None:
        raise PhotoNotFoundError("Photo not found.")

    storage_key = photo.storage_key
    photo.delete()

    def _delete_file():
        try:
            get_storage_adapter().delete(storage_key)
        except Exception:
            logger.exception("Storage delete failed (job_id=%s, photo_id=%s)", job.id, photo_id)

    transaction.on_commit(_delete_file)

    logger.info("Job photo deleted (photo_id=%s, job_id=%s, by=%s)", photo_id, job.id, user.id)


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
