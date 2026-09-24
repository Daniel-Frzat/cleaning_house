"""
Back-office — المستخدمون (ADMIN فقط).

    GET  /api/admin/users
    GET  /api/admin/users/{user_id}
    POST /api/admin/users/{user_id}/suspend
    POST /api/admin/users/{user_id}/reactivate

🔒 لا كلمة سر ولا hash ولا رموز: is_superuser يظهر كقيمة منطقية فقط.
🔒 حسابات الأدمن لا تُوقَف من هنا (409 admin_account) — إدارتها للـSuperuser.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from ninja import Query, Router, Schema
from pydantic import Field

from apps.accounts.authentication import AdminJWTAuth
from apps.audit.api.common import ErrorOut, PageQuery, error, forbidden, page
from apps.audit.services.backoffice import AdminRequiredError

from ..models import UserStatus
from ..roles import ConfirmedRole
from ..services import backoffice_users as svc

router = Router(tags=["Admin — Users"], auth=AdminJWTAuth())


class UserFilters(PageQuery):
    role: Optional[ConfirmedRole] = None
    status: Optional[UserStatus] = None
    is_contractor: Optional[bool] = None
    q: Optional[str] = Field(None, max_length=255)
    joined_from: Optional[date] = None
    joined_to: Optional[date] = None


class AdminUserOut(Schema):
    id: uuid.UUID
    phone: str
    email: Optional[str] = None
    email_verified: bool
    full_name: str
    role: str
    roles: list[str]
    is_contractor: bool
    status: str
    is_active: bool
    is_superuser: bool
    date_joined: datetime
    last_login: Optional[datetime] = None


class AdminUserListOut(Schema):
    count: int
    items: list[AdminUserOut]


class AdminUserDetailOut(AdminUserOut):
    contractor_status: str
    contractor_profile_id: Optional[uuid.UUID] = None
    bookings_count: int
    properties_count: int
    social_providers: list[str]
    must_change_password: bool
    updated_at: datetime


class SuspendIn(Schema):
    # pattern \S: سبب من فراغات فقط ليس سببًا
    reason: str = Field(..., min_length=1, max_length=1000, pattern=r"\S")


def _serialize(user):
    return {
        "id": user.id,
        "phone": user.phone,
        "email": user.email,
        "email_verified": user.email_verified,
        "full_name": user.full_name,
        "role": user.role,
        "roles": list(user.active_roles()),
        "is_contractor": user.is_contractor,
        "status": user.status,
        "is_active": user.is_active,
        "is_superuser": bool(user.is_superuser),
        "date_joined": user.date_joined,
        "last_login": user.last_login,
    }


def _serialize_detail(user):
    profile = getattr(user, "contractor_profile", None)
    return {
        **_serialize(user),
        "contractor_status": svc.contractor_status_of(user),
        "contractor_profile_id": profile.id if profile is not None else None,
        "bookings_count": getattr(user, "bookings_count", 0),
        "properties_count": getattr(user, "properties_count", 0),
        "social_providers": sorted(a.provider for a in user.social_accounts.all()),
        "must_change_password": user.must_change_password,
        "updated_at": user.updated_at,
    }


@router.get(
    "/users",
    response={200: AdminUserListOut, 403: ErrorOut},
    summary="List user accounts (admin only)",
)
def list_users(request, filters: UserFilters = Query(...)):
    try:
        qs = svc.list_users(
            request.user,
            role=filters.role,
            status=filters.status,
            is_contractor=filters.is_contractor,
            q=filters.q,
            joined_from=filters.joined_from,
            joined_to=filters.joined_to,
        )
    except AdminRequiredError as exc:
        return forbidden(exc)
    return 200, page(qs, filters, _serialize)


@router.get(
    "/users/{user_id}",
    response={200: AdminUserDetailOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve a user account (admin only)",
)
def retrieve_user(request, user_id: uuid.UUID):
    try:
        user = svc.get_user(request.user, user_id)
    except AdminRequiredError as exc:
        return forbidden(exc)
    except svc.UserNotFoundError as exc:
        return error(404, exc.code, str(exc))
    return 200, _serialize_detail(user)


def _mutation_errors(exc):
    if isinstance(exc, AdminRequiredError):
        return forbidden(exc)
    if isinstance(exc, svc.UserNotFoundError):
        return error(404, exc.code, str(exc))
    return error(409, exc.code, str(exc))


@router.post(
    "/users/{user_id}/suspend",
    response={200: AdminUserDetailOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    summary="Suspend a user account (admin only)",
    description=(
        "Sets the account to `SUSPENDED`, revokes every refresh token it holds "
        "and marks its contractor profile `UNAVAILABLE`. Existing access tokens "
        "stop working immediately. Administrator accounts are refused with "
        "`409 admin_account`. Recorded in the audit log."
    ),
)
def suspend_user(request, user_id: uuid.UUID, payload: SuspendIn):
    try:
        svc.suspend_user(request.user, user_id, payload.reason.strip(), request=request)
        user = svc.get_user(request.user, user_id)
    except (AdminRequiredError, svc.UserAdminError) as exc:
        return _mutation_errors(exc)
    return 200, _serialize_detail(user)


@router.post(
    "/users/{user_id}/reactivate",
    response={200: AdminUserDetailOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    summary="Reactivate a suspended user account (admin only)",
)
def reactivate_user(request, user_id: uuid.UUID):
    try:
        svc.reactivate_user(request.user, user_id, request=request)
        user = svc.get_user(request.user, user_id)
    except (AdminRequiredError, svc.UserAdminError) as exc:
        return _mutation_errors(exc)
    return 200, _serialize_detail(user)
