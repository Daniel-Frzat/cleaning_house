"""
Public Service Catalog API — Services Domain (§36.2)

نقاط النهاية (محمية بـJWT، متاحة لأي دور):
    GET /api/services        الخدمات النشطة المتاحة للحجز
    GET /api/services/{id}   خدمة نشطة واحدة

📌 سبب الوجود: الكتالوج كان مقصورًا على الإدارة (§37)، فلم يكن للعميل
   أي مسار يقرأ منه الخدمات ليختار service_type_id عند الحجز.

🔒 حجب التسعير بنيوي لا شرطي: ServicePublicOut لا يُعرِّف room_price ولا
   base_price إطلاقًا، فالمفتاحان غائبان من JSON لأي دور — بما فيه ADMIN.
   الإدارة تقرأ الأسعار من مسارها الخاص (/api/admin/services) لا من هنا.
   لا Union ولا وراثة من الشكل الإداري (بند 9 من تدقيق §47).

🔒 الخدمة المعطّلة تعيد 404 نفسه الذي تعيده الخدمة غير الموجودة — لا
   يكشف الرد أن خدمة سُحبت من التداول.

⚠️ لا صلاحية ADMIN هنا، ولا لمس لأي نقطة إدارية: تلك تبقى في
   api/catalog.py كما هي.

⚠️ لا استعلام ORM في هذا الملف — كل شيء عبر طبقة الخدمة (§43).
"""

import uuid

from ninja import Router
from ninja_jwt.authentication import JWTAuth

from ..services import catalog as svc
from .public_schemas import ErrorOut, ServicePublicOut

router = Router(tags=["Services"], auth=JWTAuth())


def _serialize(service):
    """
    🔒 قائمة حقول بيضاء صريحة — لا تمرير كائن كامل للطبقة المُسلسِلة.

    الـschema وحده كافٍ لحجب التسعير، وهذا طبقة ثانية فوقه: ما لا يُبنى
    هنا لا يمكن أن يُسلسَل هناك.
    """
    return {
        "id": service.id,
        "name": service.name,
        "description": service.description,
    }


@router.get(
    "",
    response={200: list[ServicePublicOut]},
    summary="List the services available for booking",
    description=(
        "**Who may call:** any authenticated user. This is the customer-facing "
        "catalog — the one a client app reads to let someone pick what to "
        "book.\n\n"
        "Returns **only active services**. A service an administrator has "
        "deactivated never appears here, which matches booking creation "
        "rejecting it with `400`.\n\n"
        "**Pricing is deliberately absent.** `room_price` and `base_price` are "
        "internal administrative data and are not fields of this response for "
        "any role, administrators included. A customer sees one final total, "
        "and only after a contractor accepts the booking. Administrators read "
        "raw prices from `GET /api/admin/services` instead.\n\n"
        "**Side effects:** none — read-only."
    ),
)
def list_public_services(request):
    """الخدمات النشطة وحدها — بلا أي حقل تسعير."""
    services = svc.list_active_service_types()
    return 200, [_serialize(s) for s in services]


@router.get(
    "/{service_id}",
    response={200: ServicePublicOut, 404: ErrorOut},
    summary="Retrieve one available service",
    description=(
        "**Who may call:** any authenticated user.\n\n"
        "Returns one active service. **Pricing is absent** for the same reason "
        "as in the list endpoint.\n\n"
        "**Side effects:** none — read-only.\n\n"
        "A deactivated service returns the same `404` as one that never "
        "existed, so the response cannot be used to discover that a service "
        "was withdrawn."
    ),
    openapi_extra={
        "responses": {
            404: {
                "description": (
                    "No such service, or it is no longer active — deliberately "
                    "indistinguishable."
                )
            }
        }
    },
)
def retrieve_public_service(request, service_id: uuid.UUID):
    """خدمة نشطة واحدة، أو 404 موحّد."""
    try:
        service = svc.get_active_service_type(service_id)
    except svc.ServiceTypeNotFoundError:
        return 404, {"code": "service_not_found", "detail": "Service type not found."}

    return 200, _serialize(service)
