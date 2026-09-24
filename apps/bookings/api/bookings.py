"""
Bookings API — Booking Domain (Change Set §36.1، §20)

نقاط النهاية (كلها محمية بـJWT، وكلها CUSTOMER فقط):
    POST /api/bookings        إنشاء حجز + أسطر خدماته (status=PENDING)
    GET  /api/bookings        قائمة حجوزات المستخدم الحالي
    GET  /api/bookings/{id}   قراءة حجز يملكه
    POST /api/bookings/{id}/reschedule   تغيير موعد حجز لم يجد مقاولًا

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

import uuid

from django.core.exceptions import ValidationError
from ninja import Query, Router
from apps.accounts.authentication import ActiveUserJWTAuth

from apps.properties.services import properties as properties_svc

from ..models import BookingStatus
from ..services import bookings as svc
from ..services import scheduling as scheduling_svc
from .schemas import BookingIn, BookingOut, BookingRescheduleIn, ErrorOut

router = Router(tags=["Bookings"], auth=ActiveUserJWTAuth())


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


def _serialize_payment(booking):
    """
    ملخص الدفع، أو None إن لم تُنشأ دفعة بعد.

    🔒 ثلاثة حقول لا أكثر — والقائمة مبنية هنا صراحةً لا مُمرَّرة من
       الكائن: ما لا يُبنى هنا لا يمكن أن يُسلسَل هناك. provider_reference
       و failure_reason يبقيان حصرًا في /bookings/{id}/payment.

    📌 getattr بافتراضي None تكفي: العلاقة واحد-لواحد عكسية، وغيابها
       يرفع Booking.payment.RelatedObjectDoesNotExist وهو وريث
       AttributeError (كما أنه وريث Payment.DoesNotExist)، فتلتقطه
       getattr وتعيد الافتراضي بلا استيراد نموذج الدفع هنا.
    """
    payment = getattr(booking, "payment", None)
    if payment is None:
        return None

    return {
        "status": payment.status,
        "amount": payment.amount,
        "method": payment.method,
    }


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
        "public_reference": booking.public_reference,
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
        # 📌 يُعاد للعميل لأنه كاتبه: يحتاج أن يراجعه ويصحّحه.
        # 🔒 هذه الـendpoints للعميل حصرًا (assert_is_customer في طبقة
        #    الخدمة)، فلا يصل المقاول إلى هنا أصلًا — وصوله إليه عبر
        #    GET /api/bookings/{id}/job بعد الإسناد وحده.
        "access_notes": booking.access_notes,
        # 📌 ملخص الدفع — نفس شرط السعر: لا يظهر قبل CONFIRMED. لا دفعة
        #    تُنشأ قبل قبول المقاول أصلًا، والشرط على الحالة لا على وجود
        #    الصف تمامًا كـcomputed_price أعلاه.
        # ⚠️ الحالة FAILED تظهر كما هي: الشحن يقع بعد التأكيد وقد يفشل
        #    دون أن يتراجع التأكيد (offers.py)، وإخفاء ذلك كان سيترك
        #    العميل يظن أنه دفع.
        "payment": _serialize_payment(booking) if price_revealed else None,
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
    response={201: BookingOut, 400: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut},
    summary="Create a booking with its service selections (customer only)",
    description=(
        "**Who may call:** `CUSTOMER` only, and only against a property they "
        "own.\n\n"
        "**Preconditions:** at least one service selection, every selected "
        "service must be active in the catalog, and `scheduled_at` must be in "
        "the future, at least `BOOKING_MIN_LEAD_MINUTES` (default 120) minutes "
        "away, and within business hours (07:00-19:00) in the property's local "
        "timezone. The property must not have been removed.\n\n"
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
            409: {"description": "The property has been removed (`property_inactive`)."},
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

    quote = None
    if payload.quote_id is not None:
        from ..services import quotes as quotes_svc

        try:
            quote = quotes_svc.get_usable_quote(
                request.user, payload.quote_id, property_id=payload.property_id
            )
        except quotes_svc.QuoteNotFoundError as exc:
            return _error(404, exc.code, str(exc))
        except (quotes_svc.QuoteExpiredError, quotes_svc.QuoteAlreadyUsedError) as exc:
            return _error(409, exc.code, str(exc))
        except quotes_svc.QuoteError as exc:
            return _error(400, exc.code, str(exc))

    try:
        booking = svc.create_booking(
            request.user,
            property_id=payload.property_id,
            service_selections=selections,
            scheduled_at=payload.scheduled_at,
            # 📌 من العميل مباشرةً — لا يُشتق ولا يُورَّث من حجز سابق
            access_notes=payload.access_notes,
            quote=quote,
            payment_method_reference=payload.payment_method_reference,
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
    except svc.PropertyInactiveError as exc:
        return _error(409, exc.code, str(exc))
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
        "**Paging:** `limit` (default 100, max 200) and `offset` query "
        "parameters; the response is still a plain array.\n\n"
        "**Side effects:** none — read-only."
    ),
    openapi_extra={"responses": {403: {"description": "The caller is not a `CUSTOMER`."}}},
)
def list_bookings(request, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    try:
        bookings = svc.list_bookings(request.user)
    except svc.InvalidCustomerRoleError as exc:
        return _error(403, exc.code, str(exc))
    except svc.BookingPermissionError as exc:
        return _error(403, exc.code, str(exc))

    return 200, [_serialize(b) for b in bookings[offset : offset + limit]]


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
def retrieve_booking(request, booking_id: uuid.UUID):
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


# ------------------------------------------------------------
# POST /bookings/{id}/reschedule
# ------------------------------------------------------------
@router.post(
    "/{booking_id}/reschedule",
    response={
        200: BookingOut,
        400: ErrorOut,
        403: ErrorOut,
        404: ErrorOut,
        409: ErrorOut,
        422: ErrorOut,
    },
    summary="Reschedule a booking that found no cleaner (customer only)",
    description=(
        "**Who may call:** `CUSTOMER` only, and only for a booking they own.\n\n"
        "**Preconditions:** the booking must still be `PENDING`, its "
        "`dispatch_status` must be `NO_CONTRACTOR`, it must have no assigned "
        "contractor and no successful payment. Any other state returns `409` — "
        "rescheduling after a contractor has been assigned or the booking "
        "confirmed is not available yet, pending the cancellation and refund "
        "policy.\n\n"
        "`scheduled_at` follows the same rules as booking creation: it must be in "
        "the future, at least `BOOKING_MIN_LEAD_MINUTES` away, and between 07:00 "
        "and 19:00 local time. The timezone is always the one derived from the "
        "property address; `timezone` is accepted only for backwards "
        "compatibility and must equal it (otherwise `400 timezone_not_allowed`)."
        "\n\n"
        "**Side effects:** the booking's previous dispatch offers are **deleted**, "
        "`dispatch_status` returns to `SEARCHING`, and a fresh dispatch round "
        "starts after the change commits. Clearing the old offers is what makes "
        "the new round meaningful: a contractor is never offered the same booking "
        "twice, so without it every eligible contractor would still be excluded "
        "and the search would end immediately. A contractor who declined the old "
        "time is therefore eligible for the new one.\n\n"
        "Finding no contractor again is not an error: the booking simply returns "
        "to `NO_CONTRACTOR` and can be rescheduled again."
    ),
    openapi_extra={
        "responses": {
            400: {
                "description": (
                    "`scheduled_at` is in the past or outside business hours "
                    "(07:00-19:00 local time)."
                )
            },
            403: {"description": "The caller is not a `CUSTOMER`."},
            404: {"description": "No such booking, or it belongs to another customer."},
            409: {
                "description": (
                    "The booking is not in a reschedulable state — it is no longer "
                    "`PENDING`, a search is still running or an offer is live "
                    "(`dispatch_status` is not `NO_CONTRACTOR`), a contractor is "
                    "already assigned, or it has been paid."
                )
            },
            422: {"description": "The booking failed validation."},
        }
    },
)
def reschedule_booking(request, booking_id: uuid.UUID, payload: BookingRescheduleIn):
    """
    🔒 حجز الغير وحجز غير موجود يعيدان 404 نفسه — نفس سياسة القراءة.

    ⚠️ 409 لا 400 لحالة الحجز: الطلب سليم شكلًا والرفض بسبب حالة المورد.

    📌 booking_id مُوصَّف uuid.UUID لا str: معرّف مشوَّه يُرفض عند التحقق
       بـ422 بدل أن يصل إلى الـORM فيرفع ValidationError غير ملتقَط
       (500). المسارات الأقدم في المشروع تستعمل str وتعاني هذا — لم
       تُغيَّر هنا لأنها خارج نطاق هذا التغيير.
    """
    try:
        booking = svc.reschedule_booking(
            request.user,
            booking_id,
            scheduled_at=payload.scheduled_at,
            timezone_name=payload.timezone,
        )
    except svc.InvalidCustomerRoleError as exc:
        return _error(403, exc.code, str(exc))
    except (svc.BookingPermissionError, svc.BookingNotFoundError):
        return _not_found()
    except svc.BookingNotReschedulableError as exc:
        return _error(409, exc.code, str(exc))
    except scheduling_svc.SchedulingError as exc:
        return _error(400, exc.code, str(exc))
    except ValidationError as exc:
        return _validation_error(exc)
    except svc.BookingError as exc:
        return _error(400, exc.code, str(exc))

    return 200, _serialize(booking)
