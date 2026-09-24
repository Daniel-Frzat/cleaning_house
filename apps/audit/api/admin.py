"""
Back-office — لوحة المؤشرات وسجل التدقيق.

    GET /api/admin/dashboard/summary   ADMIN
    GET /api/admin/audit-log           SUPERUSER

🔒 السجل للقراءة فقط — لا تعديل ولا حذف عبر أي مسار.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from ninja import Query, Router, Schema
from pydantic import Field

from apps.accounts.authentication import AdminJWTAuth, SuperuserJWTAuth

from ..services import audit as audit_svc
from ..services import dashboard as dashboard_svc
from ..services.backoffice import AdminRequiredError, SuperuserRequiredError, assert_superuser
from .common import ErrorOut, PageQuery, error, forbidden, page

dashboard_router = Router(tags=["Admin — Dashboard"], auth=AdminJWTAuth())
audit_router = Router(tags=["Admin — Audit log"], auth=SuperuserJWTAuth())


# ------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------
class BookingCountsOut(Schema):
    by_status: dict[str, int]
    by_dispatch_status: dict[str, int]
    created_today: int
    created_last_7_days: int


class PendingVerificationsCountOut(Schema):
    business_registrations: int
    insurance_documents: int
    total: int


class PaymentCountsOut(Schema):
    needs_reconciliation: int
    failed_last_30_days: int


class PayoutCountsOut(Schema):
    pending: int
    failed: int
    needs_reconciliation: int


class RevenueOut(Schema):
    today: Decimal
    last_7_days: Decimal
    last_30_days: Decimal


class DashboardSummaryOut(Schema):
    generated_at: datetime
    bookings: BookingCountsOut
    pending_verifications: PendingVerificationsCountOut
    open_support_requests: int
    payments: PaymentCountsOut
    payouts: PayoutCountsOut
    # مجموع دفعات العملاء الناجحة (AUD) — إجمالي لا صافي، لا عمولة في النظام
    gross_revenue: RevenueOut
    contractors_available_now: int
    total_customers: int
    total_contractors: int


@dashboard_router.get(
    "/dashboard/summary",
    response={200: DashboardSummaryOut, 403: ErrorOut},
    summary="Back-office dashboard counters (admin only)",
    description=(
        "Counts across bookings, verifications, support, payments and payouts. "
        "`gross_revenue` sums `SUCCEEDED` customer payments by `paid_at` for today "
        "(platform local day), the last 7 and the last 30 days."
    ),
)
def dashboard_summary(request):
    try:
        return 200, dashboard_svc.dashboard_summary(request.user)
    except AdminRequiredError as exc:
        return forbidden(exc)


# ------------------------------------------------------------
# Audit log
# ------------------------------------------------------------
class AuditFilters(PageQuery):
    actor_id: Optional[uuid.UUID] = None
    action: Optional[str] = Field(None, max_length=64)
    target_type: Optional[str] = Field(None, max_length=64)
    target_id: Optional[str] = Field(None, max_length=64)
    # "from"/"to" كلمتان محجوزتان في Python — alias يحفظ اسم الاستعلام
    date_from: Optional[date] = Field(None, alias="from")
    date_to: Optional[date] = Field(None, alias="to")


class AuditEntryOut(Schema):
    id: uuid.UUID
    actor_id: Optional[uuid.UUID] = None
    actor_label: str
    action: str
    target_type: str
    # حقل مخرجات لا معرّف مسار: نص حر بطبيعته (UUID أو pk رقمي كـPricingConfig=1)
    target_id: Optional[str] = None
    details: Any = None
    ip_address: Optional[str] = None
    created_at: datetime


class AuditEntryListOut(Schema):
    count: int
    items: list[AuditEntryOut]


def _serialize_entry(entry):
    return {
        "id": entry.id,
        "actor_id": entry.actor_id,
        "actor_label": entry.actor_label,
        "action": entry.action,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "details": entry.details,
        "ip_address": entry.ip_address,
        "created_at": entry.created_at,
    }


@audit_router.get(
    "/audit-log",
    response={200: AuditEntryListOut, 403: ErrorOut},
    summary="Read the admin audit log (superuser only)",
    description=(
        "Append-only record of every administrative mutation, newest first. "
        "Filters: `actor_id`, `action` (e.g. `user.suspend`), `target_type` "
        "(model name, e.g. `Payment`), `target_id`, and an inclusive `from`/`to` "
        "date range."
    ),
)
def list_audit_log(request, filters: AuditFilters = Query(...)):
    try:
        assert_superuser(request.user)
    except SuperuserRequiredError as exc:
        return error(403, exc.code, str(exc))
    except AdminRequiredError as exc:
        return forbidden(exc)

    qs = audit_svc.list_entries(
        actor=filters.actor_id,
        action=filters.action,
        target_type=filters.target_type,
        target_id=filters.target_id,
        date_from=filters.date_from,
        date_to=filters.date_to,
    )
    return 200, page(qs, filters, _serialize_entry)
