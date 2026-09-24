"""
Contractor Admin API — Contractors Domain

نقاط النهاية (محمية بـJWT، وكلها ADMIN فقط):
    GET /api/admin/contractors        قائمة كل ملفات المقاولين
    GET /api/admin/contractors/{id}   قراءة أي ملف بمعرّفه

🔒 سياسة الحالة (موثّقة، ومقصودة الاختلاف عن المسارات الذاتية):
   الرفض هنا 403 وليس 404 — نفس قرار apps/services. المسارات الذاتية
   للمقاول لا تقبل معرّفًا أصلًا، فلا يوجد ما يُكشف؛ وهنا المورد إداري
   ومعروف الوجود للإدارة.

⚠️ قراءة فقط: الإدارة لا تعدّل ملف المقاول ولا جاهزيته من هنا. تعديل
   الجاهزية فعل يخص المقاول وحده (وإتاحته للإدارة قاعدة عمل غير واردة).

⚠️ لا يوجد هنا أي منطق Dispatch/مطابقة/مسافة — هذه المرحلة تخزين وقراءة.
"""

import uuid

from django.core.exceptions import ValidationError
from ninja import Router
from apps.accounts.authentication import ActiveUserJWTAuth, AdminJWTAuth

from ..services import profile as svc
from ..services import verification as vsvc
from .profile import (
    serialize,
    serialize_business_registration,
    serialize_insurance_document,
)
from .schemas import (
    BusinessRegistrationOut,
    ContractorProfileOut,
    ContractorVerificationsOut,
    ErrorOut,
    InsuranceDocumentOut,
    PendingVerificationsOut,
    ReviewPatch,
)

router = Router(tags=["Admin — Contractors"], auth=ActiveUserJWTAuth())


def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


# ------------------------------------------------------------
# GET /admin/contractors
# ------------------------------------------------------------
@router.get(
    "/contractors",
    response={200: list[ContractorProfileOut], 403: ErrorOut},
    summary="List all contractor profiles (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "Returns every contractor profile, available and unavailable alike — "
        "filtering by availability belongs to dispatch, not to an administrative "
        "listing.\n\n"
        "**Side effects:** none — read-only. Administrators cannot edit a "
        "contractor's profile or availability through this API at all."
    ),
    openapi_extra={"responses": {403: {"description": "The caller is not an `ADMIN`."}}},
)
def list_contractors(request):
    """
    يعيد كل الملفات — المتاح وغير المتاح معًا. الترشيح بالجاهزية شأن
    Dispatch لاحقًا، لا قائمة الإدارة.
    """
    try:
        profiles = svc.list_profiles(request.user)
    except (svc.AdminRoleRequiredError, svc.ContractorProfilePermissionError) as exc:
        return _error(403, exc.code, str(exc))

    return 200, [serialize(p) for p in profiles]


# ------------------------------------------------------------
# GET /admin/contractors/{id}
# ------------------------------------------------------------
@router.get(
    "/contractors/{profile_id}",
    response={200: ContractorProfileOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve any contractor profile (admin only)",
    description=(
        "**Who may call:** `ADMIN` only. This is the only way to read a "
        "contractor profile by id; contractors themselves use the self-service "
        "`GET /api/contractor/profile`.\n\n"
        "**Side effects:** none — read-only.\n\n"
        "A non-administrator receives `403` rather than `404` here: unlike the "
        "customer-owned resources, the existence of a contractor record is not "
        "something this endpoint needs to conceal."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not an `ADMIN`."},
            404: {"description": "No contractor profile with this id."},
        }
    },
)
def retrieve_contractor(request, profile_id: uuid.UUID):
    try:
        profile = svc.get_profile_by_id(request.user, profile_id)
    except (svc.AdminRoleRequiredError, svc.ContractorProfilePermissionError) as exc:
        return _error(403, exc.code, str(exc))
    except svc.ContractorProfileNotFoundError:
        return _error(
            404, "contractor_profile_not_found", "Contractor profile not found."
        )

    return 200, serialize(profile)


# ------------------------------------------------------------
# GET /admin/contractors/{id}/verifications
# ------------------------------------------------------------
@router.get(
    "/contractors/{profile_id}/verifications",
    auth=AdminJWTAuth(),
    response={200: ContractorVerificationsOut, 403: ErrorOut, 404: ErrorOut},
    summary="Full verification history of one contractor (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "Every business registration and insurance document this contractor "
        "ever submitted, in every state (pending, verified, rejected), newest "
        "first. **Side effects:** none — read-only."
    ),
)
def contractor_verifications(request, profile_id: uuid.UUID):
    try:
        history = vsvc.list_verifications_for_profile(request.user, profile_id)
    except (vsvc.AdminRoleRequiredError, vsvc.ContractorProfilePermissionError) as exc:
        return _error(403, "admin_required", str(exc))
    except vsvc.ContractorProfileNotFoundError:
        return _error(
            404, "contractor_profile_not_found", "Contractor profile not found."
        )

    return 200, {
        "contractor_id": history["profile"].id,
        "eligible": vsvc.is_contractor_eligible(history["profile"]),
        "business_registrations": [
            serialize_business_registration(r) for r in history["business_registrations"]
        ],
        "insurance_documents": [
            serialize_insurance_document(d) for d in history["insurance_documents"]
        ],
    }


# ============================================================
# مراجعة التحقق — ADMIN فقط (Change Set §6، Infra §5)
# ============================================================
# ⚠️ مراجعة بشرية بحتة: لا استدعاء لأي سجل حكومي أو خدمة تحقق خارجية،
#    والـadapter التجريدي business_registry يبقى دون استخدام.
#
# ⚠️ لا جدولة إعادة تحقق ولا مهمة خلفية لانتهاء الصلاحية (بند مفتوح).


def _review_error(exc):
    """
    يحوّل أخطاء المراجعة إلى ردود HTTP.

    🔒 الرفض بلا سبب يعيد 400 كما تنص المواصفة صراحةً — وهو استثناء
       مقصود من عرف هذا المشروع (422 لأخطاء التحقق).
    """
    if isinstance(exc, vsvc.MissingRejectionReasonError):
        return _error(400, exc.code, str(exc))
    if isinstance(exc, vsvc.InvalidReviewStatusError):
        return _error(422, exc.code, str(exc))
    if isinstance(exc, vsvc.SelfReviewError):
        return _error(403, exc.code, str(exc))
    if isinstance(exc, (vsvc.AlreadyReviewedError, vsvc.ExpiredDocumentError)):
        return _error(409, exc.code, str(exc))
    return None


# ------------------------------------------------------------
# GET /admin/verifications/pending
# ------------------------------------------------------------
@router.get(
    "/verifications/pending",
    response={200: PendingVerificationsOut, 403: ErrorOut},
    summary="List all pending verifications (admin only)",
    description=(
        "**Who may call:** `ADMIN` only.\n\n"
        "The review queue: every business registration and insurance document "
        "still awaiting a decision, across all contractors. The two kinds are "
        "returned in **separate lists** (`business_registrations` and "
        "`insurance_documents`) because they are different entities with "
        "different fields.\n\n"
        "**Side effects:** none — read-only."
    ),
    openapi_extra={"responses": {403: {"description": "The caller is not an `ADMIN`."}}},
)
def list_pending_verifications(request):
    """
    النوعان منفصلان في الرد — كيانان بحقول مختلفة، لا قائمة هجينة.
    """
    try:
        pending = vsvc.list_pending(request.user)
    except (vsvc.AdminRoleRequiredError, vsvc.ContractorProfilePermissionError) as exc:
        return _error(403, exc.code, str(exc))

    return 200, {
        "business_registrations": [
            serialize_business_registration(r)
            for r in pending["business_registrations"]
        ],
        "insurance_documents": [
            serialize_insurance_document(d) for d in pending["insurance_documents"]
        ],
    }


# ------------------------------------------------------------
# PATCH /admin/business-registration/{id}
# ------------------------------------------------------------
@router.patch(
    "/business-registration/{registration_id}",
    response={
        200: BusinessRegistrationOut,
        400: ErrorOut,
        403: ErrorOut,
        404: ErrorOut,
        409: ErrorOut,
        422: ErrorOut,
    },
    summary="Approve or reject a business registration (admin only)",
    description=(
        "**Who may call:** `ADMIN` only. This is a **human decision**: no "
        "government registry or external verification service is consulted.\n\n"
        "**Preconditions:** rejecting requires a `rejection_reason`; a rejection "
        "without one is refused with `400` so that a contractor is never rejected "
        "silently.\n\n"
        "**Side effects:** the decision, the reviewing administrator and the "
        "review time are recorded on the submission. Approval contributes to the "
        "contractor's eligibility, which is computed from the current state of "
        "their approved registration and insurance rather than stored as a flag."
    ),
    openapi_extra={
        "responses": {
            400: {"description": "A rejection was submitted without a `rejection_reason`."},
            403: {"description": "The caller is not an `ADMIN`."},
            404: {"description": "No business registration with this id."},
            422: {"description": "`status` is not a valid review decision, or validation failed."},
        }
    },
)
def review_business_registration(request, registration_id: uuid.UUID, payload: ReviewPatch):
    """
    🔒 الرفض يتطلب سببًا — بدونه 400، لا قبول صامت.
    """
    try:
        registration = vsvc.review_business_registration(
            request.user,
            registration_id,
            status=payload.status.value,
            rejection_reason=payload.rejection_reason,
            request=request,
        )
    except (vsvc.AdminRoleRequiredError, vsvc.ContractorProfilePermissionError) as exc:
        return _error(403, exc.code, str(exc))
    except vsvc.VerificationNotFoundError:
        return _error(404, "verification_not_found", "Business registration not found.")
    except (
        vsvc.MissingRejectionReasonError,
        vsvc.InvalidReviewStatusError,
        vsvc.SelfReviewError,
        vsvc.AlreadyReviewedError,
        vsvc.ExpiredDocumentError,
    ) as exc:
        return _review_error(exc)
    except ValidationError as exc:
        return _error(422, "validation_error", "; ".join(exc.messages))

    return 200, serialize_business_registration(registration)


# ------------------------------------------------------------
# PATCH /admin/insurance/{id}
# ------------------------------------------------------------
@router.patch(
    "/insurance/{document_id}",
    response={
        200: InsuranceDocumentOut,
        400: ErrorOut,
        403: ErrorOut,
        404: ErrorOut,
        409: ErrorOut,
        422: ErrorOut,
    },
    summary="Approve or reject an insurance document (admin only)",
    description=(
        "**Who may call:** `ADMIN` only. This is a **human decision**: no insurer "
        "or external verification service is consulted.\n\n"
        "**Preconditions:** rejecting requires a `rejection_reason`; a rejection "
        "without one is refused with `400`.\n\n"
        "**Side effects:** the decision, the reviewing administrator and the "
        "review time are recorded on the document. Approval contributes to the "
        "contractor's eligibility. Note that nothing re-checks the expiry date "
        "afterwards — there is no scheduled re-verification."
    ),
    openapi_extra={
        "responses": {
            400: {"description": "A rejection was submitted without a `rejection_reason`."},
            403: {"description": "The caller is not an `ADMIN`."},
            404: {"description": "No insurance document with this id."},
            422: {"description": "`status` is not a valid review decision, or validation failed."},
        }
    },
)
def review_insurance_document(request, document_id: uuid.UUID, payload: ReviewPatch):
    """🔒 نفس القاعدة: الرفض بلا سبب يعيد 400."""
    try:
        document = vsvc.review_insurance_document(
            request.user,
            document_id,
            status=payload.status.value,
            rejection_reason=payload.rejection_reason,
            request=request,
        )
    except (vsvc.AdminRoleRequiredError, vsvc.ContractorProfilePermissionError) as exc:
        return _error(403, exc.code, str(exc))
    except vsvc.VerificationNotFoundError:
        return _error(404, "verification_not_found", "Insurance document not found.")
    except (
        vsvc.MissingRejectionReasonError,
        vsvc.InvalidReviewStatusError,
        vsvc.SelfReviewError,
        vsvc.AlreadyReviewedError,
        vsvc.ExpiredDocumentError,
    ) as exc:
        return _review_error(exc)
    except ValidationError as exc:
        return _error(422, "validation_error", "; ".join(exc.messages))

    return 200, serialize_insurance_document(document)
