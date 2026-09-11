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

from django.core.exceptions import ValidationError
from ninja import Router
from ninja_jwt.authentication import JWTAuth

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
    ErrorOut,
    InsuranceDocumentOut,
    PendingVerificationsOut,
    ReviewPatch,
)

router = Router(tags=["Admin — Contractors"], auth=JWTAuth())


def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


# ------------------------------------------------------------
# GET /admin/contractors
# ------------------------------------------------------------
@router.get(
    "/contractors",
    response={200: list[ContractorProfileOut], 403: ErrorOut},
    summary="List all contractor profiles (admin only)",
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
)
def retrieve_contractor(request, profile_id: str):
    try:
        profile = svc.get_profile_by_id(request.user, profile_id)
    except (svc.AdminRoleRequiredError, svc.ContractorProfilePermissionError) as exc:
        return _error(403, exc.code, str(exc))
    except svc.ContractorProfileNotFoundError:
        return _error(
            404, "contractor_profile_not_found", "Contractor profile not found."
        )

    return 200, serialize(profile)


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
    return None


# ------------------------------------------------------------
# GET /admin/verifications/pending
# ------------------------------------------------------------
@router.get(
    "/verifications/pending",
    response={200: PendingVerificationsOut, 403: ErrorOut},
    summary="List all pending verifications (admin only)",
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
        422: ErrorOut,
    },
    summary="Approve or reject a business registration (admin only)",
)
def review_business_registration(request, registration_id: str, payload: ReviewPatch):
    """
    🔒 الرفض يتطلب سببًا — بدونه 400، لا قبول صامت.
    """
    try:
        registration = vsvc.review_business_registration(
            request.user,
            registration_id,
            status=payload.status.value,
            rejection_reason=payload.rejection_reason,
        )
    except (vsvc.AdminRoleRequiredError, vsvc.ContractorProfilePermissionError) as exc:
        return _error(403, exc.code, str(exc))
    except vsvc.VerificationNotFoundError:
        return _error(404, "verification_not_found", "Business registration not found.")
    except (vsvc.MissingRejectionReasonError, vsvc.InvalidReviewStatusError) as exc:
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
        422: ErrorOut,
    },
    summary="Approve or reject an insurance document (admin only)",
)
def review_insurance_document(request, document_id: str, payload: ReviewPatch):
    """🔒 نفس القاعدة: الرفض بلا سبب يعيد 400."""
    try:
        document = vsvc.review_insurance_document(
            request.user,
            document_id,
            status=payload.status.value,
            rejection_reason=payload.rejection_reason,
        )
    except (vsvc.AdminRoleRequiredError, vsvc.ContractorProfilePermissionError) as exc:
        return _error(403, exc.code, str(exc))
    except vsvc.VerificationNotFoundError:
        return _error(404, "verification_not_found", "Insurance document not found.")
    except (vsvc.MissingRejectionReasonError, vsvc.InvalidReviewStatusError) as exc:
        return _review_error(exc)
    except ValidationError as exc:
        return _error(422, "validation_error", "; ".join(exc.messages))

    return 200, serialize_insurance_document(document)
