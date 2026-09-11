"""
Contractor Profile API (self-service) — Contractors Domain

نقاط النهاية (كلها محمية بـJWT، وكلها CONTRACTOR فقط):
    POST   /api/contractor/profile               إنشاء ملفه
    GET    /api/contractor/profile               قراءة ملفه
    PATCH  /api/contractor/profile               تعديل ملفه
    PATCH  /api/contractor/profile/availability  تبديل الجاهزية

طبقتا حماية منفصلتان عمدًا (نفس نمط apps/properties):
  1) بوابة الدور (هنا): CONTRACTOR فقط — CUSTOMER/ADMIN يحصلون على 403.
  2) فحص الملكية (طبقة الخدمة): الملف يخص هذا المستخدم تحديدًا.

🔒 لا يأخذ أي مسار هنا معرّف ملف من العميل: المسارات ذاتية بالكامل وتشتقّ
   الملف من التوكن. هذا يُلغي فئة كاملة من ثغرات IDOR — لا يوجد معرّف
   ليُستبدل أصلًا. الوصول إلى ملف بعينه بمعرّفه متاح للإدارة وحدها.

⚠️ مسار الجاهزية منفصل عمدًا عن التعديل العام: يُستدعى بكثرة، ويحمل
   حقلًا واحدًا فقط، ولا يلمس بقية بيانات الملف.

⚠️ لا يوجد هنا أي منطق Dispatch/مسافة ولا استدعاء لأي adapter (§34/§4).
"""

from django.core.exceptions import ValidationError
from ninja import Router
from ninja_jwt.authentication import JWTAuth

from ..services import profile as svc
from ..services import verification as vsvc
from .schemas import (
    AvailabilityPatch,
    BusinessRegistrationIn,
    BusinessRegistrationOut,
    ContractorProfileIn,
    ContractorProfileOut,
    ContractorProfilePatch,
    ErrorOut,
    InsuranceDocumentIn,
    InsuranceDocumentOut,
)

router = Router(tags=["Contractor Profile"], auth=JWTAuth())


# ------------------------------------------------------------
# أدوات مشتركة
# ------------------------------------------------------------
def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


def _not_found():
    return _error(404, "contractor_profile_not_found", "Contractor profile not found.")


def _validation_error(exc):
    """يحوّل ValidationError من الـModel إلى رد 422 مفهوم."""
    if hasattr(exc, "message_dict"):
        detail = "; ".join(
            f"{field}: {' '.join(msgs)}" for field, msgs in exc.message_dict.items()
        )
    else:
        detail = "; ".join(exc.messages)
    return _error(422, "validation_error", detail)


def serialize(profile):
    """يحوّل الملف إلى شكل الاستجابة — معرّف المستخدم فقط، بلا بيانات حساب."""
    return {
        "id": profile.id,
        "user_id": profile.user_id,
        "business_name": profile.business_name,
        "street_address": profile.street_address,
        "suburb": profile.suburb,
        "state": profile.state,
        "postcode": profile.postcode,
        "country": profile.country,
        "latitude": profile.latitude,
        "longitude": profile.longitude,
        "availability_status": profile.availability_status,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


# ------------------------------------------------------------
# POST /contractor/profile
# ------------------------------------------------------------
@router.post(
    "/profile",
    response={201: ContractorProfileOut, 403: ErrorOut, 409: ErrorOut, 422: ErrorOut},
    summary="Create own contractor profile (contractor only)",
)
def create_profile(request, payload: ContractorProfileIn):
    """
    ⚠️ الملف يبدأ UNAVAILABLE دائمًا — الجاهزية لا تُقبل في جسم الإنشاء.
    """
    data = payload.dict(exclude_unset=True)

    try:
        profile = svc.create_profile(request.user, **data)
    except svc.InvalidContractorRoleError as exc:
        return _error(403, exc.code, str(exc))
    except svc.ContractorProfilePermissionError as exc:
        return _error(403, exc.code, str(exc))
    except svc.ProfileAlreadyExistsError as exc:
        return _error(409, exc.code, str(exc))
    except ValidationError as exc:
        return _validation_error(exc)
    except svc.ContractorProfileError as exc:
        return _error(422, exc.code, str(exc))

    return 201, serialize(profile)


# ------------------------------------------------------------
# GET /contractor/profile
# ------------------------------------------------------------
@router.get(
    "/profile",
    response={200: ContractorProfileOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve own contractor profile",
)
def retrieve_profile(request):
    try:
        profile = svc.get_own_profile(request.user)
    except svc.InvalidContractorRoleError as exc:
        return _error(403, exc.code, str(exc))
    except svc.ContractorProfilePermissionError as exc:
        return _error(403, exc.code, str(exc))
    except svc.ContractorProfileNotFoundError:
        return _not_found()

    return 200, serialize(profile)


# ------------------------------------------------------------
# PATCH /contractor/profile
# ------------------------------------------------------------
@router.patch(
    "/profile",
    response={200: ContractorProfileOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut},
    summary="Update own contractor profile",
)
def update_profile(request, payload: ContractorProfilePatch):
    """
    ⚠️ الجاهزية لا تُعدَّل من هنا — لها مسارها المخصّص.
    """
    fields = payload.dict(exclude_unset=True)

    try:
        if fields:
            profile = svc.update_profile(request.user, **fields)
        else:
            # لا شيء لتعديله — نتحقق من الصلاحية والوجود على الأقل
            profile = svc.get_own_profile(request.user)
    except svc.InvalidContractorRoleError as exc:
        return _error(403, exc.code, str(exc))
    except svc.ContractorProfilePermissionError as exc:
        return _error(403, exc.code, str(exc))
    except svc.ContractorProfileNotFoundError:
        return _not_found()
    except ValidationError as exc:
        return _validation_error(exc)
    except svc.ContractorProfileError as exc:
        return _error(422, exc.code, str(exc))

    return 200, serialize(profile)


# ------------------------------------------------------------
# PATCH /contractor/profile/availability
# ------------------------------------------------------------
@router.patch(
    "/profile/availability",
    response={200: ContractorProfileOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut},
    summary="Toggle own availability (contractor only)",
)
def update_availability(request, payload: AvailabilityPatch):
    """
    مسار خفيف مخصّص للجاهزية — منفصل عن تعديل البيانات لأنه يُستدعى بكثرة.
    """
    try:
        profile = svc.update_availability(
            request.user, payload.availability_status.value
        )
    except svc.InvalidContractorRoleError as exc:
        return _error(403, exc.code, str(exc))
    except svc.ContractorProfilePermissionError as exc:
        return _error(403, exc.code, str(exc))
    except svc.ContractorProfileNotFoundError:
        return _not_found()
    except ValidationError as exc:
        return _validation_error(exc)
    except svc.ContractorProfileError as exc:
        return _error(422, exc.code, str(exc))

    return 200, serialize(profile)


# ============================================================
# التحقق — تقديم وعرض (CONTRACTOR على نفسه فقط)
# ============================================================
# ⚠️ مراجعة يدوية بحتة (§6): لا استدعاء لأي سجل حكومي أو خدمة تحقق.
#    فحص الـABN شكلي (11 رقمًا) ويعيش في الـschema والـModel validator.
#
# 🔒 هذه المسارات ذاتية أيضًا: لا تقبل معرّف مقاول من العميل، بل تشتقّه
#    من التوكن — فلا سبيل لتقديم مستند نيابةً عن مقاول آخر، ولا لقراءة
#    مستنداته.


def serialize_business_registration(registration):
    return {
        "id": registration.id,
        "contractor_id": registration.contractor_id,
        "abn": registration.abn,
        "business_name": registration.business_name,
        "status": registration.status,
        "reviewed_by_id": registration.reviewed_by_id,
        "reviewed_at": registration.reviewed_at,
        "rejection_reason": registration.rejection_reason,
        "created_at": registration.created_at,
        "updated_at": registration.updated_at,
    }


def serialize_insurance_document(document):
    return {
        "id": document.id,
        "contractor_id": document.contractor_id,
        "document_reference": document.document_reference,
        "expiry_date": document.expiry_date,
        "status": document.status,
        "reviewed_by_id": document.reviewed_by_id,
        "reviewed_at": document.reviewed_at,
        "rejection_reason": document.rejection_reason,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


def _verification_error(exc):
    """يوحّد ردود أخطاء نطاق التحقق على طبقة المقاول."""
    if isinstance(exc, vsvc.InvalidContractorRoleError):
        return _error(403, exc.code, str(exc))
    if isinstance(exc, vsvc.ContractorProfilePermissionError):
        return _error(403, exc.code, str(exc))
    if isinstance(exc, vsvc.ContractorProfileNotFoundError):
        return _not_found()
    return None


# ------------------------------------------------------------
# POST /contractor/business-registration
# ------------------------------------------------------------
@router.post(
    "/business-registration",
    response={201: BusinessRegistrationOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut},
    summary="Submit own business registration (contractor only)",
)
def submit_business_registration(request, payload: BusinessRegistrationIn):
    """
    ⚠️ يبدأ PENDING دائمًا — لا اعتماد ذاتي. المراجعة إدارية بشرية.
    """
    try:
        registration = vsvc.submit_business_registration(
            request.user, abn=payload.abn, business_name=payload.business_name
        )
    except (
        vsvc.InvalidContractorRoleError,
        vsvc.ContractorProfilePermissionError,
        vsvc.ContractorProfileNotFoundError,
    ) as exc:
        return _verification_error(exc)
    except ValidationError as exc:
        return _validation_error(exc)

    return 201, serialize_business_registration(registration)


# ------------------------------------------------------------
# GET /contractor/business-registration
# ------------------------------------------------------------
@router.get(
    "/business-registration",
    response={200: list[BusinessRegistrationOut], 403: ErrorOut, 404: ErrorOut},
    summary="List own business registration submissions",
)
def list_business_registrations(request):
    """التعدد مسموح (إعادة تقديم بعد رفض) — تُعاد كلها، الأحدث أولًا."""
    try:
        registrations = vsvc.list_own_business_registrations(request.user)
    except (
        vsvc.InvalidContractorRoleError,
        vsvc.ContractorProfilePermissionError,
        vsvc.ContractorProfileNotFoundError,
    ) as exc:
        return _verification_error(exc)

    return 200, [serialize_business_registration(r) for r in registrations]


# ------------------------------------------------------------
# POST /contractor/insurance
# ------------------------------------------------------------
@router.post(
    "/insurance",
    response={201: InsuranceDocumentOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut},
    summary="Submit own insurance document (contractor only)",
)
def submit_insurance(request, payload: InsuranceDocumentIn):
    """
    ⚠️ document_reference نص وليس ملفًا مرفوعًا (Infra §7).
    """
    try:
        document = vsvc.submit_insurance_document(
            request.user,
            document_reference=payload.document_reference,
            expiry_date=payload.expiry_date,
        )
    except (
        vsvc.InvalidContractorRoleError,
        vsvc.ContractorProfilePermissionError,
        vsvc.ContractorProfileNotFoundError,
    ) as exc:
        return _verification_error(exc)
    except ValidationError as exc:
        return _validation_error(exc)

    return 201, serialize_insurance_document(document)


# ------------------------------------------------------------
# GET /contractor/insurance
# ------------------------------------------------------------
@router.get(
    "/insurance",
    response={200: list[InsuranceDocumentOut], 403: ErrorOut, 404: ErrorOut},
    summary="List own insurance submissions",
)
def list_insurance(request):
    """التعدد مسموح (تجديد التأمين) — تُعاد كلها، الأحدث أولًا."""
    try:
        documents = vsvc.list_own_insurance_documents(request.user)
    except (
        vsvc.InvalidContractorRoleError,
        vsvc.ContractorProfilePermissionError,
        vsvc.ContractorProfileNotFoundError,
    ) as exc:
        return _verification_error(exc)

    return 200, [serialize_insurance_document(d) for d in documents]
