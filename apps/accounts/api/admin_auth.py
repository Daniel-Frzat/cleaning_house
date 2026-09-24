"""
Admin Auth API — دخول موقع لوحة التحكم.

    POST   /api/admin/auth/login               البريد/الهاتف + كلمة السر
    POST   /api/admin/auth/verify              رمز SMS → JWT (+ توكن جهاز موثوق)
    POST   /api/admin/auth/resend              إعادة إرسال الرمز
    GET    /api/admin/auth/me                  حساب الأدمن الحالي
    POST   /api/admin/auth/password            تغيير كلمة السر
    GET    /api/admin/auth/devices             الأجهزة الموثوقة
    DELETE /api/admin/auth/devices/{id}        إلغاء ثقة جهاز

    تجديد التوكن وتسجيل الخروج: /api/auth/token/refresh و /api/auth/logout.

إدارة الحسابات (Superuser حصرًا):
    GET    /api/admin/admins
    POST   /api/admin/admins
    GET    /api/admin/admins/{id}
    PATCH  /api/admin/admins/{id}
    POST   /api/admin/admins/{id}/reset-password
    POST   /api/admin/admins/{id}/revoke-sessions
"""

import uuid

from django.conf import settings
from ninja import Query, Router

from ..authentication import AdminJWTAuth, SuperuserJWTAuth
from ..services import admin_auth as svc
from ..services import otp as otp_service
from .admin_schemas import (
    AdminAccountCreatedOut,
    AdminAccountCreateIn,
    AdminAccountListOut,
    AdminAccountOut,
    AdminAccountPatch,
    AdminLoginIn,
    AdminLoginOut,
    AdminMeOut,
    AdminResendIn,
    AdminVerifyIn,
    AdminVerifyOut,
    PasswordChangeIn,
    TrustedDeviceOut,
)
from .auth import _client_ip
from .schemas import ErrorOut, TokenPairOut

router = Router(tags=["Admin — Auth"])
accounts_router = Router(tags=["Admin — Accounts"], auth=SuperuserJWTAuth())


def _error(status, code, detail, retry_after_seconds=None):
    body = {"code": code, "detail": detail}
    if retry_after_seconds is not None:
        body["retry_after_seconds"] = retry_after_seconds
    return status, body


def _auth_error(exc):
    return _error(
        exc.status, exc.code, str(exc), getattr(exc, "retry_after_seconds", None)
    )


def _otp_error(exc):
    """أخطاء إرسال/تحقق رمز SMS بالأكواد نفسها المستعملة في دخول العملاء."""
    if isinstance(exc, (otp_service.OTPResendCooldownError, otp_service.OTPRateLimitError)):
        return _error(429, exc.code, str(exc), exc.retry_after_seconds)
    if isinstance(exc, otp_service.OTPDeliveryError):
        return _error(503, exc.code, "Could not send the verification code. Try again later.")
    if isinstance(exc, otp_service.OTPMaxAttemptsError):
        return _error(429, exc.code, "Too many incorrect attempts. Start the login again.")
    if isinstance(exc, otp_service.OTPExpiredError):
        return _error(400, exc.code, "This code has expired.")
    if isinstance(exc, otp_service.OTPInvalidCodeError):
        return _error(400, exc.code, "Invalid verification code.")
    return _error(400, exc.code, "Verification failed.")


LOGIN_ERRORS = {400: ErrorOut, 401: ErrorOut, 403: ErrorOut, 409: ErrorOut, 429: ErrorOut, 503: ErrorOut}


# ------------------------------------------------------------
# الدخول
# ------------------------------------------------------------
@router.post(
    "/login",
    response={200: AdminLoginOut, **LOGIN_ERRORS},
    auth=None,
    summary="Admin login — step 1: email (or phone) and password",
    description=(
        "**Who may call:** anyone — public. Only ADMIN accounts can log in here; "
        "customers and contractors use `/api/auth/otp/*`, and admins cannot use "
        "those paths.\n\n"
        "On a correct password:\n"
        "* with a valid `device_token` from a previous login on this device → "
        "`state: \"authenticated\"` and `tokens` straight away;\n"
        "* otherwise → an SMS code is sent to the admin's registered phone and the "
        "response is `state: \"otp_required\"` with `challenge_id` and a masked "
        "`phone_hint`. Complete with `POST /api/admin/auth/verify`.\n\n"
        "**Errors:** `401 invalid_credentials` for an unknown account or a wrong "
        "password (identical, so accounts cannot be discovered); `429 "
        "account_locked` after too many wrong passwords (`retry_after_seconds`); "
        "`403 account_inactive`; `409 second_factor_unavailable` when the account "
        "has no valid phone; `429`/`503` when the SMS cannot be sent."
    ),
)
def admin_login(request, payload: AdminLoginIn):
    ip = _client_ip(request)
    try:
        result = svc.begin_login(payload.identifier, payload.password, payload.device_token, ip=ip)
    except svc.AdminAuthError as exc:
        return _auth_error(exc)
    except otp_service.OTPError as exc:
        return _otp_error(exc)

    if result["state"] == "authenticated":
        return 200, {"state": "authenticated", "tokens": svc.issue_admin_tokens(result["user"])}
    return 200, {
        "state": "otp_required",
        "challenge_id": result["challenge"].id,
        "phone_hint": result["phone_hint"],
        "expires_in_seconds": getattr(settings, "OTP_EXPIRY_SECONDS", 300),
    }


@router.post(
    "/verify",
    response={200: AdminVerifyOut, **LOGIN_ERRORS},
    auth=None,
    summary="Admin login — step 2: SMS code",
    description=(
        "**Who may call:** anyone holding a `challenge_id` from `/login`.\n\n"
        "Returns `access` and `refresh` tokens. With `remember_device: true` the "
        "response also carries a `device_token` (shown once): send it with the "
        "next `/login` on this device to skip the SMS code for "
        "`ADMIN_TRUSTED_DEVICE_DAYS` days. The password is always required.\n\n"
        "`must_change_password: true` means the password was set by a superuser: "
        "every other endpoint answers `403 password_change_required` until "
        "`POST /api/admin/auth/password` succeeds."
    ),
)
def admin_verify(request, payload: AdminVerifyIn):
    try:
        result = svc.complete_login(
            payload.challenge_id,
            payload.code,
            remember_device=payload.remember_device,
            device_label=payload.device_label or request.META.get("HTTP_USER_AGENT", "")[:255],
            ip=_client_ip(request),
        )
    except svc.AdminAuthError as exc:
        return _auth_error(exc)
    except otp_service.OTPError as exc:
        return _otp_error(exc)

    user = result["user"]
    return 200, {
        "tokens": svc.issue_admin_tokens(user),
        "device_token": result["device_token"],
        "device_expires_in_days": (
            getattr(settings, "ADMIN_TRUSTED_DEVICE_DAYS", 30) if result["device_token"] else None
        ),
        "must_change_password": user.must_change_password,
    }


@router.post(
    "/resend",
    response={200: AdminLoginOut, **LOGIN_ERRORS},
    auth=None,
    summary="Admin login — resend the SMS code",
    description="Sends a new code for an open challenge. Cooldown and hourly/daily limits apply.",
)
def admin_resend(request, payload: AdminResendIn):
    try:
        result = svc.resend_code(payload.challenge_id, ip=_client_ip(request))
    except svc.AdminAuthError as exc:
        return _auth_error(exc)
    except otp_service.OTPError as exc:
        return _otp_error(exc)
    return 200, {
        "state": "otp_required",
        "challenge_id": result["challenge"].id,
        "phone_hint": result["phone_hint"],
        "expires_in_seconds": getattr(settings, "OTP_EXPIRY_SECONDS", 300),
    }


# ------------------------------------------------------------
# الحساب الحالي
# ------------------------------------------------------------
def _serialize_admin(user):
    return {
        "id": user.id,
        "email": user.email,
        "phone": user.phone,
        "full_name": user.full_name,
        "status": user.status,
        "is_superuser": user.is_superuser,
        "must_change_password": user.must_change_password,
        "password_changed_at": user.password_changed_at,
        "last_login": user.last_login,
        "failed_login_attempts": user.failed_login_attempts,
        "locked_until": user.locked_until,
        "date_joined": user.date_joined,
    }


@router.get(
    "/me",
    response={200: AdminMeOut},
    auth=AdminJWTAuth(),
    summary="Current administrator",
    description="Allowed even while `must_change_password` is set.",
)
def admin_me(request):
    return 200, _serialize_admin(request.user)


@router.post(
    "/password",
    response={200: TokenPairOut, 400: ErrorOut, 422: ErrorOut},
    auth=AdminJWTAuth(),
    summary="Change own password",
    description=(
        "Requires the current password. The new one must pass the password "
        "policy (at least 12 characters, not common, not all digits, not "
        "similar to the account's details). Clears `must_change_password`.\n\n"
        "**Side effects:** every other session and every trusted device of this "
        "account is revoked; the response carries fresh tokens for this session."
    ),
)
def change_password(request, payload: PasswordChangeIn):
    try:
        tokens = svc.change_own_password(
            request.user, payload.current_password, payload.new_password, request=request
        )
    except svc.AdminAuthError as exc:
        return _auth_error(exc)
    return 200, tokens


@router.get(
    "/devices",
    response={200: list[TrustedDeviceOut]},
    auth=AdminJWTAuth(),
    summary="List own trusted devices",
)
def list_devices(request):
    return 200, list(svc.list_trusted_devices(request.user))


@router.delete(
    "/devices/{device_id}",
    response={204: None, 404: ErrorOut},
    auth=AdminJWTAuth(),
    summary="Revoke one of own trusted devices",
)
def revoke_device(request, device_id: uuid.UUID):
    if not svc.revoke_trusted_device(request.user, device_id, request=request):
        return _error(404, "device_not_found", "Device not found.")
    return 204, None


# ------------------------------------------------------------
# إدارة حسابات الأدمن — Superuser حصرًا
# ------------------------------------------------------------
@accounts_router.get(
    "",
    response={200: AdminAccountListOut},
    summary="List administrator accounts (superuser only)",
)
def list_admins(
    request,
    status: str = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    qs = svc.list_admins(request.user)
    if status:
        qs = qs.filter(status=status)
    return 200, {
        "count": qs.count(),
        "items": [_serialize_admin(u) for u in qs[offset : offset + limit]],
    }


@accounts_router.post(
    "",
    response={201: AdminAccountCreatedOut, 409: ErrorOut},
    summary="Create an administrator (superuser only)",
    description=(
        "Creates an ADMIN with a **temporary password returned once**. Give it "
        "to the new admin through a secure channel; they must change it on first "
        "login. `phone` receives the login SMS codes."
    ),
)
def create_admin(request, payload: AdminAccountCreateIn):
    try:
        admin, temporary = svc.create_admin(
            request.user, payload.email, payload.phone, payload.full_name,
            is_superuser=payload.is_superuser, request=request,
        )
    except svc.AdminAuthError as exc:
        return _auth_error(exc)
    return 201, {"admin": _serialize_admin(admin), "temporary_password": temporary}


@accounts_router.get(
    "/{admin_id}",
    response={200: AdminAccountOut, 404: ErrorOut},
    summary="Retrieve an administrator (superuser only)",
)
def retrieve_admin(request, admin_id: uuid.UUID):
    admin = svc.get_admin(request.user, admin_id)
    if admin is None:
        return _error(404, "admin_not_found", "Administrator not found.")
    return 200, _serialize_admin(admin)


@accounts_router.patch(
    "/{admin_id}",
    response={200: AdminAccountOut, 404: ErrorOut, 409: ErrorOut},
    summary="Update an administrator (superuser only)",
    description=(
        "Change name, email, phone, `status` (`ACTIVE`/`INACTIVE`/`SUSPENDED`) or "
        "`is_superuser`. A superuser cannot deactivate or demote themselves, and "
        "the last active superuser cannot be removed. Deactivating an account or "
        "changing its phone revokes all its sessions and trusted devices."
    ),
)
def update_admin(request, admin_id: uuid.UUID, payload: AdminAccountPatch):
    try:
        admin = svc.update_admin(
            request.user, admin_id, request=request, **payload.dict(exclude_unset=True)
        )
    except svc.AdminAuthError as exc:
        return _auth_error(exc)
    if admin is None:
        return _error(404, "admin_not_found", "Administrator not found.")
    return 200, _serialize_admin(admin)


@accounts_router.post(
    "/{admin_id}/reset-password",
    response={200: AdminAccountCreatedOut, 404: ErrorOut},
    summary="Reset an administrator's password (superuser only)",
    description=(
        "Sets a new temporary password (returned once), forces a change on next "
        "login, unlocks the account and revokes all its sessions and devices."
    ),
)
def reset_password(request, admin_id: uuid.UUID):
    admin, temporary = svc.reset_admin_password(request.user, admin_id, request=request)
    if admin is None:
        return _error(404, "admin_not_found", "Administrator not found.")
    return 200, {"admin": _serialize_admin(admin), "temporary_password": temporary}


@accounts_router.post(
    "/{admin_id}/revoke-sessions",
    response={200: AdminAccountOut, 404: ErrorOut},
    summary="Sign an administrator out everywhere (superuser only)",
)
def revoke_sessions(request, admin_id: uuid.UUID):
    admin = svc.revoke_admin_sessions(request.user, admin_id, request=request)
    if admin is None:
        return _error(404, "admin_not_found", "Administrator not found.")
    return 200, _serialize_admin(admin)
