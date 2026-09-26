"""
Back-office — المهام (ADMIN فقط، قراءة).

    GET /api/admin/jobs
    GET /api/admin/jobs/{job_id}
"""

import uuid
from datetime import datetime
from typing import Optional

from ninja import Query, Router, Schema

from apps.accounts.authentication import AdminJWTAuth
from apps.audit.api.common import CreatedRangeQuery, ErrorOut, error, forbidden, page
from apps.audit.services.backoffice import AdminRequiredError
from apps.bookings.api.admin import ContractorSummaryOut, serialize_contractor

from ..models import JobStatus
from ..services import admin as svc

router = Router(tags=["Admin — Jobs"], auth=AdminJWTAuth())


class JobFilters(CreatedRangeQuery):
    status: Optional[JobStatus] = None
    # معرّف ملف المقاول (ContractorProfile.id)
    contractor_id: Optional[uuid.UUID] = None


class AdminJobOut(Schema):
    id: uuid.UUID
    booking_id: uuid.UUID
    public_reference: str
    customer_id: uuid.UUID
    contractor: Optional[ContractorSummaryOut] = None
    status: str
    arrived_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    marked_done_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class AdminJobListOut(Schema):
    count: int
    items: list[AdminJobOut]


class AdminJobPhotoOut(Schema):
    id: uuid.UUID
    photo_type: str
    url: str
    # 🔒 للإدارة وحدها — مرجع التخزين الخام
    storage_key: str
    uploaded_by_id: uuid.UUID
    uploaded_at: datetime


class AdminJobDetailOut(AdminJobOut):
    photos: list[AdminJobPhotoOut]


def _serialize(job):
    booking = job.booking
    return {
        "id": job.id,
        "booking_id": booking.id,
        "public_reference": booking.public_reference,
        "customer_id": booking.customer_id,
        "contractor": serialize_contractor(booking.assigned_contractor),
        "status": job.status,
        "arrived_at": job.arrived_at,
        "started_at": job.started_at,
        "marked_done_at": job.marked_done_at,
        "confirmed_at": job.confirmed_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


@router.get(
    "/jobs",
    response={200: AdminJobListOut, 403: ErrorOut},
    summary="List jobs (admin only)",
)
def list_jobs(request, filters: JobFilters = Query(...)):
    try:
        qs = svc.list_jobs(
            request.user,
            status=filters.status,
            contractor_id=filters.contractor_id,
            created_from=filters.created_from,
            created_to=filters.created_to,
        )
    except AdminRequiredError as exc:
        return forbidden(exc)
    return 200, page(qs, filters, _serialize)


@router.get(
    "/jobs/{job_id}",
    response={200: AdminJobDetailOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve a job with its photos (admin only)",
    description=(
        "Photos carry a short-lived signed `url` **and** the raw `storage_key` "
        "(administrators only). The contractor's live location is deliberately "
        "not exposed here."
    ),
)
def retrieve_job(request, job_id: uuid.UUID):
    try:
        job = svc.get_job(request.user, job_id)
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.JobNotFoundError as exc:
        return error(404, exc.code, str(exc))

    photos = [
        {
            "id": photo.id,
            "photo_type": photo.photo_type,
            "url": url,
            "storage_key": photo.storage_key,
            "uploaded_by_id": photo.uploaded_by_id,
            "uploaded_at": photo.uploaded_at,
        }
        for photo, url in svc.photos_with_urls(job)
    ]
    return 200, {**_serialize(job), "photos": photos}
