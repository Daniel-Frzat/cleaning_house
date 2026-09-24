"""
Auth API — Identity Domain (Phase 1)

نقاط النهاية:
    POST /api/auth/otp/request        طلب رمز تحقق
    POST /api/auth/otp/verify         تحقق + إصدار JWT
    POST /api/auth/social/{provider}  دخول اجتماعي (Apple/Google) + إصدار JWT
    POST /api/auth/token/refresh      تجديد التوكن (تدوير refresh)
    POST /api/auth/logout             تسجيل خروج (إبطال refresh)
    GET  /api/auth/me                 بيانات المستخدم الحالي (محمي بـJWT)

⚠️ لا يوجد تسجيل دخول بكلمة مرور — نموذج المصادقة المعتمد هو
   OTP + Apple + Google + JWT فقط (Change Set — قسم 21).

🔒 الاستجابات لا تكشف:
   - وجود/عدم وجود حساب لرقم معيّن (تفادي user enumeration)
   - أي تفصيل داخلي عن OTPVerification أو SocialAccount.raw_profile
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from ninja import Router
from ninja.errors import HttpError
from apps.accounts.authentication import ActiveUserJWTAuth

from adapters.social_auth import SocialAuthError
from apps.contractors.services.verification import get_contractor_status

from ..models import SocialProvider
from ..roles import ConfirmedRole

# أسماء أوضاع العرض — ليست أدوارًا ولا تُخزَّن. الفرونت يحفظ اختياره
# محليًا ويتحقق عند الدخول أن الوضع ما زال ضمن available_modes.
MODE_CUSTOMER = "CUSTOMER"
MODE_CONTRACTOR = "CONTRACTOR"
from ..services import identity as identity_service
from ..services import otp as otp_service
from ..services import social as social_service
from ..services import tokens as token_service
from ..services.tokens import issue_tokens_for_user
from .schemas import (
    AuthOut,
    ErrorOut,
    OTPRequestIn,
    OTPRequestOut,
    OTPVerifyIn,
    RefreshIn,
    SocialLoginIn,
    TokenPairOut,
    UserOut,
    UserProfilePatch,
)

router = Router(tags=["Auth"])


def _error(status, code, detail, retry_after_seconds=None):
    """شكل خطأ موحّد عبر كل نقاط النهاية."""
    payload = {"code": code, "detail": detail}
    if retry_after_seconds is not None:
        payload["retry_after_seconds"] = retry_after_seconds
    return status, payload


def _client_ip(request):
    """
    IP العميل الحقيقي لحدود طلب OTP.

    خلف بروكسي، REMOTE_ADDR هو البروكسي نفسه. كل بروكسي موثوق يضيف عنوان
    من اتصل به إلى آخر X-Forwarded-For، فنأخذ العنصر رقم N من النهاية حيث
    N = NUM_TRUSTED_PROXIES. ما قبله أضافه العميل وقد يكون مزوّرًا.
    """
    trusted = getattr(settings, "NUM_TRUSTED_PROXIES", 0)
    if trusted > 0:
        forwarded = [
            p.strip()
            for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")
            if p.strip()
        ]
        if len(forwarded) >= trusted:
            return forwarded[-trusted]
    return request.META.get("REMOTE_ADDR") or None


def _serialize_user(user):
    """
    شكل المستخدم الموحَّد — مصدر واحد لـ/auth/me ولرد الدخول معًا.

    🔒 قائمة حقول بيضاء صريحة: ما لا يُبنى هنا لا يظهر في أي رد. لا
       is_staff ولا is_superuser ولا password ولا last_login.

    📌 roles و contractor_status و available_modes كلها **مشتقّة لحظيًا**
       من الحساب ووثائقه — لا حقل مخزَّن يمثّلها، فلا تتقادم.
    """
    roles = user.active_roles()
    contractor_status = get_contractor_status(user)

    # الأوضاع المتاحة: الوضع يُشتق من الصلاحية لا العكس.
    # ⚠️ وضع العامل يُتاح بمجرد وجود الصلاحية — حتى قبل الاعتماد — حتى
    #    يرى المستخدم شاشة "طلبك قيد المراجعة" بدل أن تختفي عنه كليًا.
    #    الاعتماد شرط قبول العمل لا شرط رؤية الواجهة.
    available_modes = []
    if ConfirmedRole.CUSTOMER in roles:
        available_modes.append(MODE_CUSTOMER)
    if ConfirmedRole.CONTRACTOR in roles:
        available_modes.append(MODE_CONTRACTOR)

    return {
        "id": user.id,
        "phone": user.phone,
        "role": user.role,
        "status": user.status,
        "full_name": user.full_name,
        "email": user.email,
        "roles": list(roles),
        "contractor_status": contractor_status,
        "available_modes": available_modes,
    }


def _auth_response(user):
    return {
        "tokens": issue_tokens_for_user(user),
        "user": _serialize_user(user),
    }


# ------------------------------------------------------------
# POST /auth/otp/request
# ------------------------------------------------------------
@router.post(
    "/otp/request",
    response={200: OTPRequestOut, 429: ErrorOut, 400: ErrorOut, 503: ErrorOut},
    auth=None,
    summary="Request an OTP code",
    description=(
        "**Who may call:** anyone — this endpoint is public and needs no token.\n\n"
        "Sends a one-time password by SMS to the given phone number and returns "
        "how long it stays valid.\n\n"
        "**Side effects:** an OTP is generated and handed to the configured SMS "
        "provider. The response never contains the code, and it is deliberately "
        "identical whether or not an account exists for the number, so it cannot "
        "be used to discover registered users.\n\n"
        "**Limits:** one code per number per cooldown period, a daily cap per "
        "number, and an hourly cap per client IP.\n\n"
        "**Test numbers:** numbers listed in the server's `OTP_TEST_NUMBERS` "
        "receive no SMS and accept their fixed configured code. Every other "
        "number is unaffected.\n\n"
        "**Note:** no SMS provider ships with this build, so delivery to real "
        "numbers fails with `503` until one is configured."
    ),
    openapi_extra={
        "responses": {
            400: {"description": "The phone number is not a valid international number."},
            429: {
                "description": (
                    "Too many codes requested — cooldown (`otp_resend_cooldown`) "
                    "or daily/hourly cap (`otp_rate_limited`). "
                    "`retry_after_seconds` says how long to wait."
                )
            },
            503: {"description": "The SMS provider is unavailable or not configured."},
        }
    },
)
def request_otp(request, payload: OTPRequestIn):
    """
    يرسل رمز تحقق إلى الرقم عبر SMS Adapter المُعرَّف في الإعدادات.

    🔒 الاستجابة لا تحتوي الرمز إطلاقًا، ولا تكشف ما إذا كان الرقم مسجّلًا.

    الحد من التكرار يستخدم نفس OTP_RESEND_COOLDOWN_SECONDS المعتمد في
    خدمة OTP — لا سياسة منفصلة هنا.
    """
    try:
        otp_service.generate_and_send(payload.phone, ip=_client_ip(request))
    except otp_service.OTPResendCooldownError as exc:
        return _error(
            429,
            exc.code,
            "Please wait before requesting another code.",
            retry_after_seconds=exc.retry_after_seconds,
        )
    except otp_service.OTPRateLimitError as exc:
        return _error(
            429,
            exc.code,
            "Too many verification codes requested.",
            retry_after_seconds=exc.retry_after_seconds,
        )
    except otp_service.OTPInvalidPhoneError as exc:
        return _error(400, exc.code, "Invalid phone number.")
    except otp_service.OTPDeliveryError as exc:
        return _error(503, exc.code, "Could not send verification code. Try again later.")
    except otp_service.OTPError as exc:
        return _error(400, exc.code, "Could not send verification code.")

    return 200, {
        "detail": "Verification code sent.",
        "expires_in_seconds": getattr(settings, "OTP_EXPIRY_SECONDS", 300),
    }


# ------------------------------------------------------------
# POST /auth/otp/verify
# ------------------------------------------------------------
@router.post(
    "/otp/verify",
    response={200: AuthOut, 400: ErrorOut, 403: ErrorOut, 429: ErrorOut},
    auth=None,
    summary="Verify an OTP code and issue JWT (customers and contractors)",
    description=(
        "**Who may call:** anyone — this endpoint is public and needs no token.\n\n"
        "**Preconditions:** a code must have been requested for this phone number "
        "via `POST /api/auth/otp/request` and must not have expired.\n\n"
        "On success returns an `access` and a `refresh` token together with the "
        "user's id, phone, role and status.\n\n"
        "**Side effects:** the first successful verification for an unknown phone "
        "number **creates the account** with role `CUSTOMER`. The code is consumed "
        "and cannot be reused."
    ),
    openapi_extra={
        "responses": {
            400: {"description": "The code is invalid, expired, or verification failed."},
            403: {"description": "The account exists but is not active."},
            429: {"description": "Too many incorrect attempts for this code."},
        }
    },
)
def verify_otp(request, payload: OTPVerifyIn):
    """
    عند نجاح التحقق يُصدر access + refresh.

    يُنشئ الحساب عند أول تحقق ناجح إن لم يكن موجودًا (role=CUSTOMER)،
    بنفس قاعدة الدخول الاجتماعي.
    """
    try:
        otp = otp_service.verify(payload.phone, payload.code)
    except otp_service.OTPMaxAttemptsError as exc:
        return _error(429, exc.code, "Too many incorrect attempts.")
    except otp_service.OTPExpiredError as exc:
        return _error(400, exc.code, "This verification code has expired.")
    except otp_service.OTPInvalidCodeError as exc:
        return _error(400, exc.code, "Invalid verification code.")
    except otp_service.OTPError as exc:
        return _error(400, exc.code, "Verification failed.")

    try:
        # otp.phone هو الشكل القانوني للرقم — نفسه الذي يُخزَّن على الحساب
        user, _created = identity_service.get_or_create_user_by_phone(otp.phone)
    except identity_service.InactiveUserError as exc:
        return _error(403, exc.code, "This account is not active.")

    # 🔒 رسالة SMS وحدها لا تكفي لصلاحيات الإدارة — الأدمن يدخل من
    #    /api/admin/auth/login (كلمة سر + رمز).
    if user.has_admin_access():
        return _error(403, "admin_login_required", "Administrators must use the admin login.")

    return 200, _auth_response(user)


# ------------------------------------------------------------
# POST /auth/social/{provider}
# ------------------------------------------------------------
@router.post(
    "/social/{provider}",
    response={200: AuthOut, 400: ErrorOut, 401: ErrorOut, 403: ErrorOut},
    auth=None,
    summary="Log in with Apple or Google",
    description=(
        "**Who may call:** anyone — this endpoint is public and needs no token.\n\n"
        "Exchanges a provider token for Cleaning House JWTs. `provider` is either "
        "`APPLE` or `GOOGLE` and is matched case-insensitively.\n\n"
        "**Side effects:** the first successful login for an unknown provider "
        "identity **creates the account** and links the social identity to it. "
        "The response never echoes the provider's raw profile.\n\n"
        "**Note:** no social-auth provider ships with this build, so token "
        "verification fails until one is configured."
    ),
    openapi_extra={
        "responses": {
            400: {"description": "Unsupported provider, or the social login could not be completed."},
            401: {"description": "The provider token is invalid or was rejected by the provider."},
            403: {"description": "The account exists but is not active."},
        }
    },
)
def social_login(request, provider: str, payload: SocialLoginIn):
    """
    يتحقق من توكن المزوّد عبر SocialAuthAdapter ثم يصدر JWT.

    🔒 الاستجابة لا تتضمن raw_profile ولا أي بيانات خام من المزوّد.
    """
    normalized = provider.upper()
    if normalized not in SocialProvider.values:
        return _error(400, "unsupported_provider", f"Unsupported provider: {provider}")

    try:
        user, _social, _created = social_service.login_with_provider(
            normalized, payload.token
        )
    except SocialAuthError as exc:
        return _error(401, getattr(exc, "code", "social_auth_failed"), "Invalid provider token.")
    except social_service.InactiveUserError as exc:
        return _error(403, exc.code, "This account is not active.")
    except social_service.SocialLoginError as exc:
        return _error(400, exc.code, "Social login failed.")

    # 🔒 نفس قاعدة OTP: لا دخول إدارة عبر مزوّد اجتماعي
    if user.has_admin_access():
        return _error(403, "admin_login_required", "Administrators must use the admin login.")

    return 200, _auth_response(user)


# ------------------------------------------------------------
# POST /auth/token/refresh
# ------------------------------------------------------------
@router.post(
    "/token/refresh",
    response={200: TokenPairOut, 401: ErrorOut},
    auth=None,
    summary="Refresh the access token",
    description=(
        "**Who may call:** anyone holding a valid `refresh` token — no access "
        "token is needed, since this is how an expired one is replaced.\n\n"
        "Returns a new `access` **and a new `refresh`** token. The submitted "
        "refresh token is revoked immediately (rotation): store the new one "
        "and discard the old. Reusing an old refresh token returns `401`.\n\n"
        "Claims (role, status) are rebuilt from the account's current state. "
        "A suspended or deactivated account cannot refresh."
    ),
    openapi_extra={
        "responses": {
            401: {
                "description": (
                    "The refresh token is invalid, expired or already used, or "
                    "the account is no longer active. Log in again."
                )
            },
        }
    },
)
def refresh_token(request, payload: RefreshIn):
    try:
        tokens = token_service.refresh_tokens(payload.refresh)
    except token_service.TokenRefreshError as exc:
        return _error(401, exc.code, str(exc))
    return 200, tokens


# ------------------------------------------------------------
# POST /auth/logout
# ------------------------------------------------------------
@router.post(
    "/logout",
    response={204: None},
    auth=None,
    summary="Log out",
    description=(
        "**Who may call:** anyone holding a `refresh` token — no access token "
        "is needed, so logout works even after the access token expired.\n\n"
        "Revokes the refresh token so it can no longer be used. The current "
        "access token stays valid until its short lifetime ends; the client "
        "should delete both tokens locally.\n\n"
        "Idempotent: an already revoked, expired or malformed token also "
        "returns `204`."
    ),
)
def logout(request, payload: RefreshIn):
    token_service.revoke_refresh_token(payload.refresh)
    return 204, None


# ------------------------------------------------------------
# GET /auth/me
# ------------------------------------------------------------
@router.get(
    "/me",
    response={200: UserOut},
    auth=ActiveUserJWTAuth(),
    summary="Current authenticated user",
    description=(
        "**Who may call:** any authenticated user, in any role.\n\n"
        "Returns the id, phone, role and status of the token's owner. The role is "
        "also carried in the JWT claims, but this endpoint is the authoritative "
        "source: a role changed after the token was issued is reflected here "
        "first.\n\n"
        "**Side effects:** none — read-only."
    ),
)
def me(request):
    """
    بيانات المستخدم الحالي — المصدر الموثوق للدور عند التحقق من الصلاحيات.

    الدور موجود أيضًا ضمن claims الـJWT، لكن هذه النقطة تبقى المرجع الأحدث
    (الدور قد يتغير بعد إصدار التوكن).
    """
    return 200, _serialize_user(request.user)


# ------------------------------------------------------------
# PATCH /auth/me
# ------------------------------------------------------------
@router.patch(
    "/me",
    response={200: UserOut, 409: ErrorOut, 422: ErrorOut},
    auth=ActiveUserJWTAuth(),
    summary="Update own name and email",
    description=(
        "**Who may call:** any authenticated user, on their **own** account "
        "only — the account is taken from the token and no user id is "
        "accepted.\n\n"
        "Partial update: only the fields present in the body are changed. "
        "Sending `email` as an empty string clears it.\n\n"
        "**Only `full_name` and `email` can be changed here.** `phone` is the "
        "login identifier and would need OTP verification of the new number; "
        "`role` and `status` are privilege fields managed by an "
        "administrator. Neither is accepted, and sending one is rejected "
        "rather than ignored.\n\n"
        "**Side effects:** none beyond persisting the two fields. Email is "
        "unique across accounts. Changing the email marks it unverified, so it "
        "is never used to link an Apple/Google login to this account."
    ),
    openapi_extra={
        "responses": {
            409: {"description": "That email address is already used by another account."},
            422: {"description": "The submitted values failed validation, or a field that cannot be changed here was sent."},
        }
    },
)
def update_me(request, payload: UserProfilePatch):
    """
    تعديل ذاتي — الاسم والبريد فقط.

    🔒 الحساب يُشتق من التوكن: لا معرّف يُقبل من العميل، فلا مجال لتعديل
       ملف مستخدم آخر.
    """
    fields = payload.dict(exclude_unset=True)

    try:
        user = identity_service.update_own_profile(request.user, **fields)
    except identity_service.EmailAlreadyUsedError as exc:
        # 409: تعارض مع حالة قائمة (حساب آخر يملك البريد)
        return _error(409, exc.code, str(exc))
    except ValidationError as exc:
        detail = (
            "; ".join(f"{f}: {' '.join(m)}" for f, m in exc.message_dict.items())
            if hasattr(exc, "message_dict")
            else "; ".join(exc.messages)
        )
        return _error(422, "validation_error", detail)
    except identity_service.ProfileUpdateError as exc:
        return _error(422, exc.code, str(exc))

    return 200, _serialize_user(user)
