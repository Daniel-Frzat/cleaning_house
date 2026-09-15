"""
Bookings API — Booking Domain (Change Set §36.1، §20)

نقاط النهاية (كلها محمية بـJWT، وكلها CUSTOMER فقط):
    POST /api/bookings        إنشاء حجز + أسطر خدماته (status=PENDING)
    GET  /api/bookings        قائمة حجوزات المستخدم الحالي
    GET  /api/bookings/{id}   قراءة حجز يملكه

طبقتا حماية منفصلتان عمدًا (نفس نمط apps/properties):
  1) بوابة الدور (طبقة الخدمة): CUSTOMER فقط.
  2) فحص الملكية على مستوى الكائن: الحجز/العقار يخص هذا العميل.

🔒 سياسة الحالة (موثّقة): حجز يخص عميلًا آخر يعيد 404 لا 403 — نفس قرار
   apps/properties، حتى لا يكشف الرد وجود مورد لغير مالكه
   (resource enumeration). أمّا العقار غير المملوك عند الإنشاء فيعيد 403
   كما تنص المواصفة صراحةً على هذه الحالة.

📌 توقيت كشف السعر (§36.1): الرد يحمل computed_price فقط حين تكون حالة
   الحجز CONFIRMED — أي بعد قبول مقاول لعرضه. وما دام PENDING يبقى
   الحقل None.

⚠️ الإسناد يُطلق تلقائيًا عند الإنشاء (طبقة الخدمة)، والرد على العروض
   يعيش في api/offers.py — مسارات المقاول لا العميل.
"""

from django.core.exceptions import ValidationError
from ninja import Router
from ninja_jwt.authentication import JWTAuth

from apps.properties.services import properties as properties_svc

from ..models import BookingStatus
from ..services import bookings as svc
from ..services import scheduling as scheduling_svc
from .schemas import BookingIn, BookingOut, ErrorOut

router = Router(tags=["Bookings"], auth=JWTAuth())


# ------------------------------------------------------------
# أدوات مشتركة
# ------------------------------------------------------------
def _error(status, code, detail):
    return status, {"code": code, "detail": detail}


def _not_found():
    """رد موحّد: غير موجود، أو موجود لكن ليس ملكًا للطالب."""
    return _error(404, "booking_not_found", "Booking not found.")


def _validation_error(exc):
    """يحوّل ValidationError من الـModel إلى رد 422 مفهوم."""
    if hasattr(exc, "message_dict"):
        detail = "; ".join(
            f"{field}: {' '.join(msgs)}" for field, msgs in exc.message_dict.items()
        )
    else:
        detail = "; ".join(exc.messages)
    return _error(422, "validation_error", detail)


def _serialize(booking):
    """
    📌 توقيت كشف السعر (§36.1/§36.2).

    🔒 السعر يُكشف فقط حين تكون الحالة CONFIRMED. الشرط على الحالة وليس
       على وجود قيمة: لو كُتبت لقطة في قاعدة البيانات دون أن يصل الحجز
       إلى CONFIRMED (مسار خاطئ مستقبلي)، يبقى السعر محجوبًا.
    """
    price_revealed = booking.status == BookingStatus.CONFIRMED

    return {
        "id": booking.id,
        "customer_id": booking.customer_id,
        "property_id": booking.property_id,
        "status": booking.status,
        "computed_price": booking.computed_price if price_revealed else None,
        "assigned_contractor_id": (
            booking.assigned_contractor_id if price_revealed else None
        ),
        # 📌 الموعد يُعاد باللحظتين: UTC كما هو مخزَّن، والمحلية للعرض.
        #    التحويل هنا لا في الواجهة (C01/C16).
        "scheduled_at": booking.scheduled_at,
        "scheduled_at_local": scheduling_svc.to_local(
            booking.scheduled_at, booking.customer_timezone
        ),
        "customer_timezone": booking.customer_timezone,
        # 📌 تقدّم البحث يُعاد كما هو دون شرط الحالة: هو حقل عرض لا
        #    يكشف سعرًا ولا هوية مقاول، بخلاف الحقلين أعلاه.
        "dispatch_status": booking.dispatch_status,
        "last_dispatch_attempt_at": booking.last_dispatch_attempt_at,
        "service_selections": [
            {
                "id": sel.id,
                "service_type_id": sel.service_type_id,
                "service_type_name": sel.service_type.name,
                "room_count": sel.room_count,
            }
            for sel in booking.service_selections.all()
        ],
        "created_at": booking.created_at,
        "updated_at": booking.updated_at,
    }


# ------------------------------------------------------------
# POST /bookings
# ------------------------------------------------------------
@router.post(
    "",
    response={201: BookingOut, 400: ErrorOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut},
    summary="Create a booking with its service selections (customer only)",
    description=(
        "**Who may call:** `CUSTOMER` only, and only against a property they "
        "own.\n\n"
        "**Preconditions:** at least one service selection, every selected "
        "service must be active in the catalog, and `scheduled_at` must be in "
        "the future and within business hours (07:00-19:00) in the property's "
        "local timezone.\n\n"
        "`scheduled_at` is required (ISO 8601). Sent without an offset it is "
        "read in the timezone derived from the property's state; sent with an "
        "explicit offset it is honoured as given. Either way it is stored in "
        "UTC and returned as both `scheduled_at` (UTC) and "
        "`scheduled_at_local`, so the client never has to convert.\n\n"
        "The booking is created as `PENDING` **with no price and no assigned "
        "contractor** — `computed_price` and `assigned_contractor_id` are `null` "
        "in the response, and the price is not calculated at this point.\n\n"
        "**Side effects:** once the booking is committed, auto-dispatch runs and "
        "offers it to the nearest available contractor. Dispatch happens outside "
        "the creating transaction, so a dispatch failure never undoes a valid "
        "booking; finding no eligible contractor is not an error either — the "
        "booking simply stays `PENDING` with no active offer."
    ),
    openapi_extra={
        "responses": {
            400: {
                "description": (
                    "No services were selected, a selected service is unknown or "
                    "inactive, a `room_count` is invalid, or `scheduled_at` is in "
                    "the past or outside business hours (07:00-19:00 local time)."
                )
            },
            403: {
                "description": "The caller is not a `CUSTOMER`, or the property belongs to someone else."
            },
            404: {"description": "No property with this id."},
            422: {"description": "The booking failed validation."},
        }
    },
)
def create_booking(request, payload: BookingIn):
    """
    ⚠️ الحجز يبدأ PENDING بلا سعر وبلا مقاول. الرد لا يحمل أي حقل سعر.

    🔒 قواعد الرفض (400 كما تنص المواصفة): لا خدمات، أو خدمة معطّلة.
    """
    selections = [s.dict() for s in payload.service_selections]

    try:
        booking = svc.create_booking(
            request.user,
            property_id=payload.property_id,
            service_selections=selections,
            scheduled_at=payload.scheduled_at,
        )
    except scheduling_svc.SchedulingError as exc:
        # 400: موعد ماضٍ أو خارج ساعات العمل — نفس رتبة بقية قواعد العمل
        return _error(400, exc.code, str(exc))
    except svc.InvalidCustomerRoleError as exc:
        return _error(403, exc.code, str(exc))
    except svc.BookingPermissionError as exc:
        return _error(403, exc.code, str(exc))
    except properties_svc.PropertyPermissionError as exc:
        # العقار ليس ملكًا لهذا العميل — 403 كما تنص المواصفة
        return _error(403, "property_forbidden", str(exc))
    except properties_svc.InvalidOwnerRoleError as exc:
        return _error(403, exc.code, str(exc))
    except properties_svc.PropertyNotFoundError:
        return _error(404, "property_not_found", "Property not found.")
    except (svc.EmptySelectionError, svc.InactiveServiceError) as exc:
        return _error(400, exc.code, str(exc))
    except (svc.UnknownServiceError, svc.InvalidRoomCountError) as exc:
        return _error(400, exc.code, str(exc))
    except ValidationError as exc:
        return _validation_error(exc)
    except svc.BookingError as exc:
        return _error(400, exc.code, str(exc))

    return 201, _serialize(booking)


# ------------------------------------------------------------
# GET /bookings
# ------------------------------------------------------------
@router.get(
    "",
    response={200: list[BookingOut], 403: ErrorOut},
    summary="List own bookings",
    description=(
        "**Who may call:** `CUSTOMER` only — the list is always scoped to the "
        "caller's own bookings, newest first.\n\n"
        "Each booking carries its visit time as both `scheduled_at` (UTC) and "
        "`scheduled_at_local`, alongside the `customer_timezone` used for the "
        "conversion.\n\n"
        "`computed_price` and `assigned_contractor_id` are populated only for "
        "bookings that have reached `CONFIRMED`; on every other booking they are "
        "`null`.\n\n"
        "**Side effects:** none — read-only."
    ),
    openapi_extra={"responses": {403: {"description": "The caller is not a `CUSTOMER`."}}},
)
def list_bookings(request):
    try:
        bookings = svc.list_bookings(request.user)
    except svc.InvalidCustomerRoleError as exc:
        return _error(403, exc.code, str(exc))
    except svc.BookingPermissionError as exc:
        return _error(403, exc.code, str(exc))

    return 200, [_serialize(b) for b in bookings]


# ------------------------------------------------------------
# GET /bookings/{id}
# ------------------------------------------------------------
@router.get(
    "/{booking_id}",
    response={200: BookingOut, 403: ErrorOut, 404: ErrorOut},
    summary="Retrieve one own booking",
    description=(
        "**Who may call:** `CUSTOMER` only, and only for a booking they own.\n\n"
        "The visit time is returned as both `scheduled_at` (UTC) and "
        "`scheduled_at_local`, alongside the `customer_timezone` used for the "
        "conversion.\n\n"
        "**Price visibility:** `computed_price` and `assigned_contractor_id` are "
        "returned only once the booking is `CONFIRMED` — that is, after a "
        "contractor has accepted the offer. While the booking is `PENDING` both "
        "are `null`, and no other endpoint reveals the price earlier.\n\n"
        "**Side effects:** none — read-only.\n\n"
        "A booking that belongs to another customer returns the same `404` as one "
        "that does not exist."
    ),
    openapi_extra={
        "responses": {
            403: {"description": "The caller is not a `CUSTOMER`."},
            404: {"description": "No such booking, or it belongs to another customer."},
        }
    },
)
def retrieve_booking(request, booking_id: str):
    """
    🔒 حجز الغير وحجز غير موجود يعيدان 404 نفسه — لا نكشف وجود المورد.
    """
    try:
        booking = svc.get_booking(request.user, booking_id)
    except svc.InvalidCustomerRoleError as exc:
        return _error(403, exc.code, str(exc))
    except (svc.BookingPermissionError, svc.BookingNotFoundError):
        return _not_found()

    return 200, _serialize(booking)
