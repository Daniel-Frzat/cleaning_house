"""
Back-office — المهام (قراءة فقط، ADMIN).

🔒 storage_key يُكشف هنا للإدارة وحدها (تشخيص التخزين) إلى جانب الرابط
   الموقّع — ولا يُكشف أبدًا في مسارات العميل أو المقاول.

⚠️ JobLocation (موقع المقاول الحي) غير معروض هنا عمدًا: التتبع خدمة
   للعميل قبل الوصول، لا سجلًا يتصفّحه الموظفون.
⚠️ لا طفرات: لا حالة CANCELLED ولا إغلاق إداري — الإلغاء بند مفتوح (#12).
"""

from apps.audit.services.backoffice import assert_admin, filter_date_range

from ..models import Job
from .photos import list_photos_with_urls


class JobNotFoundError(Exception):
    code = "job_not_found"


def list_jobs(actor, status=None, contractor_id=None, created_from=None, created_to=None):
    """
    كل المهام — الأحدث أولًا.

    📌 contractor_id معرّف ملف المقاول المُسنَد (ContractorProfile.id).
    """
    assert_admin(actor)

    qs = Job.objects.select_related(
        "booking", "booking__assigned_contractor"
    ).order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    if contractor_id is not None:
        qs = qs.filter(booking__assigned_contractor_id=contractor_id)
    return filter_date_range(qs, "created_at", created_from, created_to)


def get_job(actor, job_id):
    assert_admin(actor)

    job = (
        Job.objects.select_related("booking", "booking__assigned_contractor")
        .filter(pk=job_id)
        .first()
    )
    if job is None:
        raise JobNotFoundError("Job not found.")
    return job


def photos_with_urls(job):
    """[(photo, signed_url)] — الرابط يُولَّد لحظيًا ولا يُخزَّن."""
    return list_photos_with_urls(job)
