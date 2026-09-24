"""
Booking API Integration Tests — Booking Domain (Change Set §36.1، §20)

يغطي: الإنشاء بأسطر صحيحة، رفض الاختيار الفارغ، رفض الخدمة المعطّلة،
إنفاذ الملكية (عقار الغير وحجز الغير)، وغياب أي حقل سعر من الرد قبل
ضبط اللقطة.
"""

import datetime
import json
import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.bookings.models import Booking, BookingServiceSelection, BookingStatus
from apps.properties.models import Property, PropertyAddress, PropertyType
from apps.services.models import ServiceType


# ------------------------------------------------------------
# أدوات
# ------------------------------------------------------------
@pytest.fixture
def client():
    return Client()


def make_user(phone, role=ConfirmedRole.CUSTOMER):
    return User.objects.create_user(phone=phone, role=role)


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


def post(client, url, payload, **extra):
    return client.post(
        url, data=json.dumps(payload), content_type="application/json", **extra
    )


def make_property(owner, label="Home"):
    prop = Property.objects.create(
        owner=owner, label=label, property_type=PropertyType.HOUSE
    )
    PropertyAddress.objects.create(
        property=prop,
        street_address="12 Example St",
        suburb="Bondi",
        state="NSW",
        postcode="2026",
    )
    return prop


def make_service(name, is_active=True):
    return ServiceType.objects.create(
        name=name,
        room_price=Decimal("45.00"),
        base_price=Decimal("80.00"),
        is_active=is_active,
    )


@pytest.fixture
def customer_a(db):
    return make_user("+61400006001")


@pytest.fixture
def customer_b(db):
    return make_user("+61400006002")


@pytest.fixture
def contractor(db):
    return make_user("+61400006003", role=ConfirmedRole.CONTRACTOR)


@pytest.fixture
def admin_user(db):
    return make_user("+61400006004", role=ConfirmedRole.ADMIN)


@pytest.fixture
def property_a(customer_a):
    return make_property(customer_a, "A Home")


@pytest.fixture
def property_b(customer_b):
    return make_property(customer_b, "B Home")


@pytest.fixture
def general(db):
    return make_service("General Cleaning")


@pytest.fixture
def carpet(db):
    return make_service("Carpet Cleaning")


@pytest.fixture
def retired(db):
    return make_service("Retired Cleaning", is_active=False)


def next_business_slot(days=3, hour=10, minute=0):
    """
    موعد صالح افتراضيًا: بعد أيام، الساعة 10 صباحًا بتوقيت سيدني.

    يُحسب بتوقيت العقار (NSW في هذه الاختبارات) لا بتوقيت الخادم، وإلا
    لاختلفت النتيجة بين بيئة وأخرى.
    """
    local = datetime.datetime.now(ZoneInfo("Australia/Sydney")) + datetime.timedelta(
        days=days
    )
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def booking_payload(prop, selections, scheduled_at=None):
    return {
        "property_id": str(prop.id),
        "service_selections": [
            {"service_type_id": str(s.id), "room_count": n} for s, n in selections
        ],
        "scheduled_at": (scheduled_at or next_business_slot()).isoformat(),
    }


def create_via_api(client, user, prop, selections):
    r = post(client, "/api/bookings", booking_payload(prop, selections), **auth(user))
    assert r.status_code == 201, r.content
    return r.json()


# ============================================================
# 1) الإنشاء بأسطر صحيحة
# ============================================================
@pytest.mark.django_db
def test_customer_creates_booking(client, customer_a, property_a, general):
    r = post(
        client,
        "/api/bookings",
        booking_payload(property_a, [(general, 3)]),
        **auth(customer_a),
    )

    assert r.status_code == 201, r.content
    body = r.json()

    assert body["customer_id"] == str(customer_a.id)
    assert body["property_id"] == str(property_a.id)
    assert body["status"] == BookingStatus.PENDING
    assert len(body["service_selections"]) == 1
    assert body["service_selections"][0]["room_count"] == 3
    assert body["service_selections"][0]["service_type_name"] == "General Cleaning"

    # حُفظ فعلًا
    booking = Booking.objects.get(pk=body["id"])
    assert booking.customer_id == customer_a.id
    assert booking.service_selections.count() == 1


@pytest.mark.django_db
def test_booking_with_multiple_selections(client, customer_a, property_a, general, carpet):
    body = create_via_api(client, customer_a, property_a, [(general, 2), (carpet, 4)])

    assert len(body["service_selections"]) == 2
    counts = {s["service_type_name"]: s["room_count"] for s in body["service_selections"]}
    assert counts == {"General Cleaning": 2, "Carpet Cleaning": 4}


@pytest.mark.django_db
def test_new_booking_starts_pending(client, customer_a, property_a, general):
    body = create_via_api(client, customer_a, property_a, [(general, 1)])

    assert body["status"] == BookingStatus.PENDING
    assert Booking.objects.get(pk=body["id"]).status == BookingStatus.PENDING


@pytest.mark.django_db
def test_zero_room_count_is_allowed(client, customer_a, property_a, general):
    """صفر غرف قيمة مشروعة — الرسم الأساسي وحده لتلك الخدمة."""
    body = create_via_api(client, customer_a, property_a, [(general, 0)])

    assert body["service_selections"][0]["room_count"] == 0


@pytest.mark.django_db
def test_status_cannot_be_set_by_client(client, customer_a, property_a, general):
    """status ليس ضمن schema الإنشاء — إرساله يُتجاهل."""
    payload = {**booking_payload(property_a, [(general, 1)]), "status": "CONFIRMED"}

    r = post(client, "/api/bookings", payload, **auth(customer_a))

    assert r.status_code == 201, r.content
    assert r.json()["status"] == BookingStatus.PENDING


# ============================================================
# 2) رفض الاختيار الفارغ — 400
# ============================================================
@pytest.mark.django_db
def test_zero_selections_is_rejected(client, customer_a, property_a):
    r = post(
        client,
        "/api/bookings",
        {
            "property_id": str(property_a.id),
            "service_selections": [],
            "scheduled_at": next_business_slot().isoformat(),
        },
        **auth(customer_a),
    )

    assert r.status_code == 400, r.content
    assert r.json()["code"] == "empty_service_selection"
    assert Booking.objects.count() == 0


@pytest.mark.django_db
def test_no_partial_booking_left_behind_on_empty_selection(client, customer_a, property_a):
    """🔒 التحقق يسبق الكتابة — لا صف حجز يتيم."""
    post(
        client,
        "/api/bookings",
        {"property_id": str(property_a.id), "service_selections": []},
        **auth(customer_a),
    )

    assert Booking.objects.count() == 0
    assert BookingServiceSelection.objects.count() == 0


# ============================================================
# 3) رفض الخدمة المعطّلة — 400
# ============================================================
@pytest.mark.django_db
def test_inactive_service_is_rejected(client, customer_a, property_a, retired):
    r = post(
        client,
        "/api/bookings",
        booking_payload(property_a, [(retired, 2)]),
        **auth(customer_a),
    )

    assert r.status_code == 400, r.content
    assert r.json()["code"] == "inactive_service"
    assert Booking.objects.count() == 0


@pytest.mark.django_db
def test_inactive_service_mixed_with_active_rejects_whole_booking(
    client, customer_a, property_a, general, retired
):
    """لا حجز جزئي: خدمة معطّلة واحدة تُبطل الطلب كله."""
    r = post(
        client,
        "/api/bookings",
        booking_payload(property_a, [(general, 1), (retired, 1)]),
        **auth(customer_a),
    )

    assert r.status_code == 400, r.content
    assert Booking.objects.count() == 0
    assert BookingServiceSelection.objects.count() == 0


@pytest.mark.django_db
def test_service_deactivated_after_catalog_change_cannot_be_booked(
    client, customer_a, property_a, general
):
    """التعطيل الناعم يسري فورًا على الحجوزات اللاحقة."""
    create_via_api(client, customer_a, property_a, [(general, 1)])

    general.is_active = False
    general.save()

    r = post(
        client,
        "/api/bookings",
        booking_payload(property_a, [(general, 1)]),
        **auth(customer_a),
    )

    assert r.status_code == 400
    assert r.json()["code"] == "inactive_service"


@pytest.mark.django_db
def test_unknown_service_id_is_rejected(client, customer_a, property_a):
    r = post(
        client,
        "/api/bookings",
        {
            "property_id": str(property_a.id),
            "service_selections": [
                {"service_type_id": str(uuid.uuid4()), "room_count": 1}
            ],
            "scheduled_at": next_business_slot().isoformat(),
        },
        **auth(customer_a),
    )

    assert r.status_code == 400, r.content
    assert r.json()["code"] == "service_not_found"
    assert Booking.objects.count() == 0


# ============================================================
# 4) إنفاذ الملكية
# ============================================================
@pytest.mark.django_db
def test_cannot_create_booking_on_another_customers_property(
    client, customer_a, property_b, general
):
    """🔒 المواصفة تنص على 403 لهذه الحالة."""
    r = post(
        client,
        "/api/bookings",
        booking_payload(property_b, [(general, 1)]),
        **auth(customer_a),
    )

    assert r.status_code == 403, r.content
    assert Booking.objects.count() == 0


@pytest.mark.django_db
def test_cannot_create_booking_on_nonexistent_property(client, customer_a, general):
    r = post(
        client,
        "/api/bookings",
        {
            "property_id": str(uuid.uuid4()),
            "service_selections": [{"service_type_id": str(general.id), "room_count": 1}],
            "scheduled_at": next_business_slot().isoformat(),
        },
        **auth(customer_a),
    )

    assert r.status_code == 404, r.content
    assert Booking.objects.count() == 0


@pytest.mark.django_db
def test_customer_b_cannot_view_customer_a_booking(
    client, customer_a, customer_b, property_a, general
):
    created = create_via_api(client, customer_a, property_a, [(general, 1)])

    r = client.get(f"/api/bookings/{created['id']}", **auth(customer_b))

    assert r.status_code == 404
    assert r.json()["code"] == "booking_not_found"


@pytest.mark.django_db
def test_nonexistent_and_forbidden_bookings_are_indistinguishable(
    client, customer_a, customer_b, property_a, general
):
    """🔒 نفس الرد للحالتين — لا نكشف وجود حجز لغير مالكه."""
    created = create_via_api(client, customer_a, property_a, [(general, 1)])

    forbidden = client.get(f"/api/bookings/{created['id']}", **auth(customer_b))
    missing = client.get(f"/api/bookings/{uuid.uuid4()}", **auth(customer_b))

    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json() == missing.json()


@pytest.mark.django_db
def test_list_returns_only_own_bookings(
    client, customer_a, customer_b, property_a, property_b, general
):
    create_via_api(client, customer_a, property_a, [(general, 1)])
    create_via_api(client, customer_a, property_a, [(general, 2)])
    create_via_api(client, customer_b, property_b, [(general, 3)])

    r = client.get("/api/bookings", **auth(customer_a))

    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert all(b["customer_id"] == str(customer_a.id) for b in body)


@pytest.mark.django_db
def test_customer_retrieves_own_booking(client, customer_a, property_a, general):
    created = create_via_api(client, customer_a, property_a, [(general, 1)])

    r = client.get(f"/api/bookings/{created['id']}", **auth(customer_a))

    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


@pytest.mark.django_db
def test_ownership_enforced_in_service_layer(db, customer_a, customer_b, property_a, general):
    """فحص الملكية على مستوى الكائن — استدعاء مباشر بحجز الغير يُرفض."""
    from apps.bookings.services import bookings as svc

    booking = svc.create_booking(
        customer_a,
        property_id=property_a.id,
        service_selections=[{"service_type_id": general.id, "room_count": 1}],
    )

    with pytest.raises(svc.BookingPermissionError):
        svc.assert_owns(customer_b, booking)


# ============================================================
# 5) بوابة الدور — CUSTOMER فقط
# ============================================================
@pytest.mark.django_db
@pytest.mark.parametrize("role", [ConfirmedRole.CONTRACTOR, ConfirmedRole.ADMIN])
def test_non_customer_forbidden(client, db, role, property_a, general):
    user = make_user("+61400006100", role=role)

    created = post(
        client, "/api/bookings", booking_payload(property_a, [(general, 1)]), **auth(user)
    )
    listed = client.get("/api/bookings", **auth(user))

    assert created.status_code == 403, created.content
    assert listed.status_code == 403, listed.content
    assert Booking.objects.count() == 0


@pytest.mark.django_db
def test_unauthenticated_requests_return_401(client, customer_a, property_a, general):
    created = create_via_api(client, customer_a, property_a, [(general, 1)])

    assert post(client, "/api/bookings", booking_payload(property_a, [(general, 1)])).status_code == 401
    assert client.get("/api/bookings").status_code == 401
    assert client.get(f"/api/bookings/{created['id']}").status_code == 401


@pytest.mark.django_db
def test_invalid_token_returns_401(client):
    bad = {"HTTP_AUTHORIZATION": "Bearer not-a-token"}
    assert client.get("/api/bookings", **bad).status_code == 401


# ============================================================
# 6) لا كشف سعر قبل ضبط اللقطة (§36.1)
# ============================================================
@pytest.mark.django_db
def test_create_response_has_no_price_value(client, customer_a, property_a, general):
    """
    🔒 قاعدة التوقيت (§36.1): الحجز يُنشأ PENDING، فالسعر لا يُكشف.

    ⚠️ تغيّر الشكل في مرحلة Dispatch: الحقل صار موجودًا في الـschema
       ليحمل اللقطة بعد التأكيد، لكن قيمته تبقى null ما دام PENDING.
       المحمي هو القيمة لا وجود المفتاح.
    """
    body = create_via_api(client, customer_a, property_a, [(general, 3)])

    assert body["status"] == "PENDING"
    assert body["computed_price"] is None
    assert body["assigned_contractor_id"] is None


@pytest.mark.django_db
def test_no_price_value_anywhere_while_pending(client, customer_a, property_a, general, carpet):
    """
    لا رقم سعر في أي موضع ما دام الحجز PENDING — ولا حقل سعر على الأسطر.

    ⚠️ أسطر الخدمات تبقى بلا أي حقل سعر في كل الحالات (§36.2): لا تفصيل
       بالبنود للعميل حتى بعد التأكيد.
    """
    body = create_via_api(client, customer_a, property_a, [(general, 2), (carpet, 1)])

    assert body["computed_price"] is None
    for leaked in ("room_price", "base_price", "amount", "total"):
        assert leaked not in json.dumps(body), f"{leaked} leaked into the response"

    for selection in body["service_selections"]:
        assert set(selection) == {"id", "service_type_id", "service_type_name", "room_count"}


@pytest.mark.django_db
def test_list_and_retrieve_have_no_price_while_pending(client, customer_a, property_a, general):
    created = create_via_api(client, customer_a, property_a, [(general, 1)])

    listed = client.get("/api/bookings", **auth(customer_a)).json()
    retrieved = client.get(f"/api/bookings/{created['id']}", **auth(customer_a)).json()

    assert listed[0]["computed_price"] is None
    assert retrieved["computed_price"] is None


@pytest.mark.django_db
def test_price_in_db_stays_hidden_while_not_confirmed(client, customer_a, property_a, general):
    """
    🔒 الحارس الجوهري، محفوظ عبر المراحل: الكشف مشروط بالحالة CONFIRMED
       لا بوجود قيمة مخزَّنة. لقطة مكتوبة على حجز PENDING تبقى محجوبة.
    """
    created = create_via_api(client, customer_a, property_a, [(general, 1)])

    booking = Booking.objects.get(pk=created["id"])
    booking.computed_price = Decimal("215.00")
    booking.save()

    r = client.get(f"/api/bookings/{created['id']}", **auth(customer_a))

    assert r.status_code == 200
    assert r.json()["status"] == "PENDING"
    assert r.json()["computed_price"] is None
    assert "215.00" not in json.dumps(r.json())


@pytest.mark.django_db
def test_created_booking_has_null_price_and_no_contractor(
    client, customer_a, property_a, general
):
    """على مستوى قاعدة البيانات: الحقلان يبقيان فارغين في هذه المرحلة."""
    created = create_via_api(client, customer_a, property_a, [(general, 2)])

    booking = Booking.objects.get(pk=created["id"])
    assert booking.computed_price is None
    assert booking.assigned_contractor_id is None
    assert booking.is_price_revealed() is False


# ============================================================
# 7) حدود النطاق والطبقات
# ============================================================
@pytest.mark.django_db
def test_no_line_item_price_stored(db):
    """§36.2: لا حقل سعر على سطر الخدمة."""
    names = {f.name for f in BookingServiceSelection._meta.get_fields()}

    for price_ish in ("price", "amount", "room_price", "base_price", "subtotal", "total"):
        assert price_ish not in names


@pytest.mark.django_db
def test_status_enum_has_exactly_three_values(db):
    """⚠️ ثلاث قيم فقط — لا تُضاف حالات استباقًا."""
    assert set(BookingStatus.values) == {"PENDING", "CONFIRMED", "CANCELLED"}


def test_customer_api_layer_never_prices_or_dispatches():
    """
    ⚠️ تغيّر النطاق في مرحلة Dispatch: التسعير والإسناد صارا مطلوبين —
       لكن في طبقة الخدمة وحدها. طبقة الـAPI الموجَّهة للعميل تبقى خالية
       منهما: لا تحسب سعرًا ولا تُسند مقاولًا.

    🔒 ويبقى حظر الـadapters قائمًا في كل الطبقات (Infra §8): المسافة
       رياضيات محلية، لا Routing Engine ولا مزوّد خارجي.
    """
    import ast
    import inspect

    from apps.bookings import models
    from apps.bookings.api import bookings as api_mod
    from apps.bookings.services import bookings as svc_mod

    def imports_of(mod):
        tree = ast.parse(inspect.getsource(mod))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
            elif isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
        return tree, found

    # حظر الـadapters في كل الوحدات
    for mod in (models, svc_mod, api_mod):
        _, imported = imports_of(mod)
        for name in imported:
            assert "adapter" not in name.lower(), f"{mod.__name__} imports adapter: {name}"

    # طبقة الـAPI للعميل: لا تسعير ولا إسناد
    tree, imported = imports_of(api_mod)
    for name in imported:
        assert "pricing" not in name.lower(), f"api imports pricing: {name}"
        assert "dispatch" not in name.lower(), f"api imports dispatch: {name}"

    identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    identifiers |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for foreign in ("calculate_price", "assign_next_contractor"):
        assert foreign not in identifiers, f"customer API calls {foreign}"


def test_api_layer_does_not_touch_orm_directly():
    """كل الوصول عبر طبقة الخدمة — لا .objects في طبقة الـAPI."""
    import inspect

    from apps.bookings.api import bookings as api_mod

    src = inspect.getsource(api_mod)

    assert ".objects." not in src
    assert "Booking(" not in src


def test_api_layer_does_not_reimplement_ownership_or_role_checks():
    """الفحوص تعيش في طبقة الخدمة ولا تُكرَّر في الـAPI."""
    import inspect

    from apps.bookings.api import bookings as api_mod

    src = inspect.getsource(api_mod)

    assert "ConfirmedRole" not in src
    assert "customer_id ==" not in src
    assert "owner_id ==" not in src


def test_booking_out_price_is_optional_and_lines_are_price_free():
    """
    الضمان الهيكلي بعد مرحلة Dispatch: الحقل موجود لكنه اختياري ويبدأ None
    (فلا يُكشف إلا حين تملؤه الطبقة المُسلسِلة عند CONFIRMED)، وأسطر
    الخدمات تبقى بلا أي حقل سعر إطلاقًا (§36.2).
    """
    from apps.bookings.api.schemas import BookingOut, ServiceSelectionOut

    field = BookingOut.model_fields["computed_price"]
    assert field.default is None, "price must default to hidden"
    assert not field.is_required()

    for name in ServiceSelectionOut.model_fields:
        assert "price" not in name


# ============================================================
# ملخص الدفع داخل الحجز
# ============================================================
@pytest.mark.django_db
def test_payment_summary_is_absent_while_pending(
    client, customer_a, property_a, general
):
    """📌 لا دفعة قبل قبول المقاول — والملخص None كـcomputed_price."""
    r = post(
        client,
        "/api/bookings",
        booking_payload(property_a, [(general, 3)]),
        **auth(customer_a),
    )

    assert r.status_code == 201, r.content
    assert r.json()["payment"] is None


@pytest.mark.django_db
def test_payment_summary_appears_once_confirmed(client, customer_a, property_a, general):
    """📌 الحالة والمبلغ وطريقة الدفع — في نداء واحد مع الحجز."""
    from apps.payments.models import Payment, PaymentMethod, PaymentStatus

    booking = Booking.objects.create(
        customer=customer_a,
        property=property_a,
        status=BookingStatus.CONFIRMED,
        computed_price=Decimal("215.00"),
    )
    BookingServiceSelection.objects.create(
        booking=booking, service_type=general, room_count=3
    )
    Payment.objects.create(
        booking=booking,
        amount=Decimal("215.00"),
        method=PaymentMethod.APPLE_PAY,
        status=PaymentStatus.SUCCEEDED,
        provider_reference="fake_ref_123",
    )

    r = client.get(f"/api/bookings/{booking.id}", **auth(customer_a))

    assert r.status_code == 200, r.content
    summary = r.json()["payment"]

    assert summary == {
        "status": PaymentStatus.SUCCEEDED,
        "amount": "215.00",
        "method": PaymentMethod.APPLE_PAY,
    }


@pytest.mark.django_db
def test_failed_payment_is_visible_on_a_confirmed_booking(
    client, customer_a, property_a, general
):
    """
    ⚠️ الحالة الواقعية: القبول ينجح والشحن يفشل.

    الشحن يقع بعد الـcommit ولا يتراجع عن التأكيد، فالحجز CONFIRMED
    ودفعته FAILED. الواجهة يجب أن ترى ذلك لا أن يُخفى عنها.
    """
    from apps.payments.models import Payment, PaymentMethod, PaymentStatus

    booking = Booking.objects.create(
        customer=customer_a,
        property=property_a,
        status=BookingStatus.CONFIRMED,
        computed_price=Decimal("215.00"),
    )
    BookingServiceSelection.objects.create(
        booking=booking, service_type=general, room_count=3
    )
    Payment.objects.create(
        booking=booking,
        amount=Decimal("215.00"),
        method=PaymentMethod.CARD,
        status=PaymentStatus.FAILED,
        failure_reason="Card declined by issuer (simulated).",
    )

    r = client.get(f"/api/bookings/{booking.id}", **auth(customer_a))

    assert r.status_code == 200, r.content
    assert r.json()["payment"]["status"] == PaymentStatus.FAILED


@pytest.mark.django_db
def test_payment_summary_never_leaks_internal_fields(
    client, customer_a, property_a, general, admin_user
):
    """
    🔒 provider_reference و failure_reason غائبان من ملخص الحجز — لكل
       الأدوار. التفاصيل تعيش في /bookings/{id}/payment وحده.
    """
    from apps.payments.models import Payment, PaymentMethod, PaymentStatus

    booking = Booking.objects.create(
        customer=customer_a,
        property=property_a,
        status=BookingStatus.CONFIRMED,
        computed_price=Decimal("215.00"),
    )
    BookingServiceSelection.objects.create(
        booking=booking, service_type=general, room_count=3
    )
    Payment.objects.create(
        booking=booking,
        amount=Decimal("215.00"),
        method=PaymentMethod.CARD,
        status=PaymentStatus.FAILED,
        provider_reference="fake_secret_ref",
        failure_reason="Card declined.",
    )

    r = client.get(f"/api/bookings/{booking.id}", **auth(customer_a))
    raw = r.content.decode()

    assert "fake_secret_ref" not in raw
    assert "provider_reference" not in raw
    assert "failure_reason" not in raw


def test_payment_summary_schema_omits_internal_fields():
    """🔒 الحجب هيكلي: الشكل لا يعرّف الحقلين أصلًا."""
    from apps.bookings.api.schemas import PaymentSummaryOut

    assert set(PaymentSummaryOut.model_fields) == {"status", "amount", "method"}


@pytest.mark.django_db
def test_booking_list_does_not_issue_a_query_per_payment(
    client, customer_a, property_a, general, django_assert_num_queries
):
    """
    ⚠️ select_related("payment") إلزامي لا تحسين: بدونه تصدر القائمة
       استعلامًا لكل حجز (N+1).
    """
    from apps.payments.models import Payment, PaymentMethod, PaymentStatus

    for _ in range(5):
        booking = Booking.objects.create(
            customer=customer_a,
            property=property_a,
            status=BookingStatus.CONFIRMED,
            computed_price=Decimal("215.00"),
        )
        BookingServiceSelection.objects.create(
            booking=booking, service_type=general, room_count=3
        )
        Payment.objects.create(
            booking=booking,
            amount=Decimal("215.00"),
            method=PaymentMethod.CARD,
            status=PaymentStatus.SUCCEEDED,
        )

    # التوكن يُصدر خارج العدّ: إصداره يسجّل OutstandingToken (قائمة الإبطال)
    headers = auth(customer_a)
    # عدد ثابت لا يتناسب مع عدد الحجوزات
    with django_assert_num_queries(4):
        r = client.get("/api/bookings", **headers)

    assert r.status_code == 200, r.content
    assert len(r.json()) == 5
    assert all(b["payment"] is not None for b in r.json())
