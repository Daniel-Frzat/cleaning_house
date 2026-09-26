"""
Scheduled Visit Tests — Booking Domain (موعد الزيارة)

يغطي: خريطة المناطق الزمنية لكل ولاية، تطبيع الموعد إلى UTC (مع إزاحة
وبدونها)، إنفاذ ساعات العمل بالتوقيت المحلي، رفض الماضي، وظهور الموعد
في الإنشاء والقائمة والتفصيل.

⚠️ التوقيت الصيفي يُختبر بلحظات حقيقية عبر zoneinfo — لا يُرمَّز هنا أي
   حساب تحوّل يدوي.
"""

import datetime
import json
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.utils import timezone as dj_timezone

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.bookings.models import Booking
from apps.bookings.services import bookings as booking_svc
from apps.bookings.services import scheduling as sched
from apps.bookings.services.timezone import (
    DEFAULT_TIMEZONE,
    STATE_TIMEZONES,
    get_timezone_for_state,
)
from apps.properties.models import Property, PropertyAddress, PropertyType
from apps.services.models import ServiceType


# ------------------------------------------------------------
# أدوات
# ------------------------------------------------------------
@pytest.fixture
def client():
    return Client()


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


def post(client, url, payload, **extra):
    return client.post(
        url, data=json.dumps(payload), content_type="application/json", **extra
    )


def make_property(owner, state="NSW", postcode="2026"):
    prop = Property.objects.create(
        owner=owner, label="Home", property_type=PropertyType.HOUSE
    )
    PropertyAddress.objects.create(
        property=prop,
        street_address="12 Example St",
        suburb="Bondi",
        state=state,
        postcode=postcode,
    )
    return prop


@pytest.fixture
def customer(db):
    return User.objects.create_user(phone="+61400009001", role=ConfirmedRole.CUSTOMER)


@pytest.fixture
def property_nsw(customer):
    return make_property(customer, state="NSW")


@pytest.fixture
def general(db):
    return ServiceType.objects.create(
        name="General Cleaning",
        room_price=Decimal("45.00"),
        base_price=Decimal("80.00"),
    )


def local_slot(tz_name="Australia/Sydney", days=3, hour=10, minute=0):
    """موعد مستقبلي داخل الدوام بالتوقيت المحلي المطلوب."""
    local = datetime.datetime.now(ZoneInfo(tz_name)) + datetime.timedelta(days=days)
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def payload(prop, service, scheduled_at, rooms=2):
    return {
        "property_id": str(prop.id),
        "service_selections": [
            {"service_type_id": str(service.id), "room_count": rooms}
        ],
        "scheduled_at": scheduled_at,
    }


# ============================================================
# 1) خريطة المناطق الزمنية
# ============================================================
@pytest.mark.parametrize(
    "state,expected",
    [
        ("NSW", "Australia/Sydney"),
        ("VIC", "Australia/Melbourne"),
        ("TAS", "Australia/Hobart"),
        ("ACT", "Australia/Sydney"),
        ("QLD", "Australia/Brisbane"),
        ("SA", "Australia/Adelaide"),
        ("NT", "Australia/Darwin"),
        ("WA", "Australia/Perth"),
    ],
)
def test_state_maps_to_expected_iana_zone(state, expected):
    assert get_timezone_for_state(state) == expected


def test_mapping_covers_every_australian_state():
    """الولايات الثماني كلها ممثَّلة — لا ولاية بلا منطقة."""
    assert set(STATE_TIMEZONES) == {
        "NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"
    }


def test_state_lookup_is_case_and_space_insensitive():
    assert get_timezone_for_state(" nsw ") == "Australia/Sydney"


def test_unknown_or_empty_state_falls_back_without_raising():
    """عنوان ناقص لا يُسقط إنشاء حجز صالح — الحقل للعرض."""
    assert get_timezone_for_state("") == DEFAULT_TIMEZONE
    assert get_timezone_for_state(None) == DEFAULT_TIMEZONE
    assert get_timezone_for_state("ZZZ") == DEFAULT_TIMEZONE


def test_queensland_has_no_daylight_saving():
    """
    🔒 QLD بلا توقيت صيفي: الإزاحة نفسها في يناير ويوليو.

    القاعدة تأتي من zoneinfo لا من كودنا — هذا يثبت أننا اخترنا المنطقة
    الصحيحة، لا أننا حسبنا التحوّل.
    """
    tz = ZoneInfo(get_timezone_for_state("QLD"))
    jan = datetime.datetime(2026, 1, 15, 12, tzinfo=tz).utcoffset()
    jul = datetime.datetime(2026, 7, 15, 12, tzinfo=tz).utcoffset()
    assert jan == jul == datetime.timedelta(hours=10)


def test_northern_territory_has_no_daylight_saving():
    tz = ZoneInfo(get_timezone_for_state("NT"))
    jan = datetime.datetime(2026, 1, 15, 12, tzinfo=tz).utcoffset()
    jul = datetime.datetime(2026, 7, 15, 12, tzinfo=tz).utcoffset()
    assert jan == jul == datetime.timedelta(hours=9, minutes=30)


def test_new_south_wales_observes_daylight_saving():
    """NSW تتحوّل: يناير (صيف جنوبي) +11، يوليو +10."""
    tz = ZoneInfo(get_timezone_for_state("NSW"))
    jan = datetime.datetime(2026, 1, 15, 12, tzinfo=tz).utcoffset()
    jul = datetime.datetime(2026, 7, 15, 12, tzinfo=tz).utcoffset()
    assert jan == datetime.timedelta(hours=11)
    assert jul == datetime.timedelta(hours=10)
    assert jan != jul


def test_western_australia_offset_is_plus_eight():
    tz = ZoneInfo(get_timezone_for_state("WA"))
    assert datetime.datetime(2026, 7, 15, 12, tzinfo=tz).utcoffset() == (
        datetime.timedelta(hours=8)
    )


# ============================================================
# 2) تطبيع الموعد إلى UTC
# ============================================================
@pytest.mark.django_db
def test_naive_datetime_is_read_in_property_timezone(customer, property_nsw, general):
    """
    📌 موعد بلا إزاحة يُفسَّر بتوقيت العقار لا بتوقيت الخادم.

    10:00 في سيدني بيوليو (+10) = 00:00 UTC.
    """
    naive = datetime.datetime(2099, 7, 15, 10, 0)
    utc = sched.normalize_scheduled_at(naive, "Australia/Sydney")

    assert utc.tzinfo == datetime.timezone.utc
    assert utc == datetime.datetime(2099, 7, 15, 0, 0, tzinfo=datetime.timezone.utc)


@pytest.mark.django_db
def test_explicit_offset_is_honoured_as_sent(customer, property_nsw, general):
    """
    📌 موعد بإزاحة صريحة يُحترم كما وصل ثم يُحوَّل لـUTC.

    09:00+08:00 (بيرث) = 01:00 UTC، بغضّ النظر عن منطقة العقار.
    """
    aware = datetime.datetime(
        2099, 7, 15, 9, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=8))
    )
    utc = sched.normalize_scheduled_at(aware, "Australia/Sydney")

    assert utc == datetime.datetime(2099, 7, 15, 1, 0, tzinfo=datetime.timezone.utc)


def test_daylight_saving_shift_is_applied_by_zoneinfo():
    """
    نفس ساعة الحائط في صيف/شتاء سيدني تعطي لحظتَي UTC مختلفتَين.

    10:00 يناير (+11) = 23:00 UTC السابق؛ 10:00 يوليو (+10) = 00:00 UTC.
    """
    jan = sched.normalize_scheduled_at(
        datetime.datetime(2099, 1, 15, 10, 0), "Australia/Sydney"
    )
    jul = sched.normalize_scheduled_at(
        datetime.datetime(2099, 7, 15, 10, 0), "Australia/Sydney"
    )

    assert jan.hour == 23 and jan.day == 14
    assert jul.hour == 0 and jul.day == 15


def test_missing_scheduled_at_raises():
    with pytest.raises(sched.MissingScheduledAtError):
        sched.normalize_scheduled_at(None, "Australia/Sydney")


def test_past_datetime_is_rejected():
    past = dj_timezone.now() - datetime.timedelta(days=1)
    with pytest.raises(sched.ScheduledAtInPastError):
        sched.normalize_scheduled_at(past, "Australia/Sydney")


# ============================================================
# 3) ساعات العمل — بالتوقيت المحلي
# ============================================================
@pytest.mark.parametrize("hour", [7, 12, 19])
def test_times_inside_business_hours_are_accepted(hour):
    """الحدّان شاملان: 07:00 و19:00 مقبولتان."""
    slot = local_slot(hour=hour)
    assert sched.normalize_scheduled_at(slot, "Australia/Sydney") is not None


@pytest.mark.parametrize("hour", [0, 6, 20, 23])
def test_times_outside_business_hours_are_rejected(hour):
    slot = local_slot(hour=hour)
    with pytest.raises(sched.OutsideBusinessHoursError):
        sched.normalize_scheduled_at(slot, "Australia/Sydney")


def test_business_hours_are_checked_in_local_not_server_time():
    """
    🔒 نفس لحظة UTC تقع داخل الدوام في بيرث وخارجه في سيدني.

    22:00 UTC = 06:00 في بيرث (+8، مرفوض) و08:00 في سيدني (+10، مقبول).
    هذا يثبت أن المقارنة محلية لا على UTC الخام.
    """
    moment = datetime.datetime(2099, 7, 15, 22, 0, tzinfo=datetime.timezone.utc)

    assert sched.normalize_scheduled_at(moment, "Australia/Sydney") == moment

    with pytest.raises(sched.OutsideBusinessHoursError):
        sched.normalize_scheduled_at(moment, "Australia/Perth")


def test_error_message_names_the_local_window_and_zone():
    with pytest.raises(sched.OutsideBusinessHoursError) as exc:
        sched.normalize_scheduled_at(local_slot(hour=23), "Australia/Sydney")

    message = str(exc.value)
    assert "07:00" in message and "19:00" in message
    assert "Australia/Sydney" in message


def test_past_is_rejected_before_business_hours():
    """موعد ماضٍ داخل الدوام يبقى مرفوضًا — ترتيب الفحص مقصود."""
    past_in_hours = dj_timezone.now().astimezone(
        ZoneInfo("Australia/Sydney")
    ) - datetime.timedelta(days=2)
    past_in_hours = past_in_hours.replace(hour=10, minute=0)

    with pytest.raises(sched.ScheduledAtInPastError):
        sched.normalize_scheduled_at(past_in_hours, "Australia/Sydney")


def test_to_local_returns_none_for_missing_schedule():
    """الصفوف السابقة للحقل بلا موعد — لا انفجار عند العرض."""
    assert sched.to_local(None, "Australia/Sydney") is None


# ============================================================
# 4) الإنشاء عبر الـAPI
# ============================================================
@pytest.mark.django_db
def test_create_stores_utc_and_derived_timezone(client, customer, property_nsw, general):
    slot = local_slot()
    r = post(
        client,
        "/api/bookings",
        payload(property_nsw, general, slot.isoformat()),
        **auth(customer),
    )

    assert r.status_code == 201, r.content
    body = r.json()

    booking = Booking.objects.get(pk=body["id"])
    assert booking.customer_timezone == "Australia/Sydney"
    assert booking.scheduled_at == slot  # نفس اللحظة، مخزَّنة UTC
    assert booking.scheduled_at.utcoffset() == datetime.timedelta(0)


@pytest.mark.django_db
def test_create_response_carries_utc_and_local_representations(
    client, customer, property_nsw, general
):
    """📌 الواجهة لا تحوّل بنفسها — الطرفان في الرد."""
    slot = local_slot(hour=9)
    r = post(
        client,
        "/api/bookings",
        payload(property_nsw, general, slot.isoformat()),
        **auth(customer),
    )
    body = r.json()

    assert body["customer_timezone"] == "Australia/Sydney"
    assert body["scheduled_at"] is not None
    assert body["scheduled_at_local"] is not None

    utc_value = datetime.datetime.fromisoformat(body["scheduled_at"].replace("Z", "+00:00"))
    local_value = datetime.datetime.fromisoformat(body["scheduled_at_local"])

    # نفس اللحظة، تمثيلان مختلفان
    assert utc_value == local_value
    assert local_value.hour == 9
    assert utc_value.utcoffset() == datetime.timedelta(0)
    assert local_value.utcoffset() != datetime.timedelta(0)


@pytest.mark.django_db
def test_timezone_is_derived_from_property_state_not_a_default(client, customer, general):
    """عقار في بيرث يُنتج Australia/Perth لا المنطقة الافتراضية."""
    prop = make_property(customer, state="WA", postcode="6000")
    slot = local_slot("Australia/Perth", hour=10)

    r = post(
        client,
        "/api/bookings",
        payload(prop, general, slot.isoformat()),
        **auth(customer),
    )

    assert r.status_code == 201, r.content
    assert r.json()["customer_timezone"] == "Australia/Perth"


def _pin_sydney_clock(monkeypatch, hour):
    """يثبّت "الآن" على ساعة محددة بتوقيت سيدني (غد، لتفادي حدود اليوم)."""
    import datetime
    from zoneinfo import ZoneInfo

    from apps.bookings.services import scheduling

    sydney = ZoneInfo("Australia/Sydney")
    fixed = (datetime.datetime.now(sydney) + datetime.timedelta(days=1)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    monkeypatch.setattr(scheduling.dj_timezone, "now", lambda: fixed)
    return fixed


@pytest.mark.django_db
def test_omitting_scheduled_at_creates_an_on_demand_request(
    client, customer, property_nsw, general, monkeypatch
):
    """📌 قرار PO — 2026-09-26: بلا scheduled_at = "اطلب عاملًا الآن"، بلا مهلة."""
    _pin_sydney_clock(monkeypatch, 10)
    r = post(
        client,
        "/api/bookings",
        {
            "property_id": str(property_nsw.id),
            "service_selections": [{"service_type_id": str(general.id), "room_count": 1}],
        },
        **auth(customer),
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["is_on_demand"] is True
    assert body["scheduled_at"] is None and body["requested_at"]


@pytest.mark.django_db
def test_on_demand_request_outside_business_hours_is_refused(
    client, customer, property_nsw, general, monkeypatch
):
    _pin_sydney_clock(monkeypatch, 21)
    r = post(
        client,
        "/api/bookings",
        {
            "property_id": str(property_nsw.id),
            "service_selections": [{"service_type_id": str(general.id), "room_count": 1}],
        },
        **auth(customer),
    )
    assert r.status_code == 400
    assert r.json()["code"] == "outside_business_hours"
    assert Booking.objects.count() == 0


@pytest.mark.django_db
def test_customer_cancels_an_unpaid_booking(client, customer, property_nsw, general, monkeypatch):
    _pin_sydney_clock(monkeypatch, 10)
    booking_id = post(
        client,
        "/api/bookings",
        {
            "property_id": str(property_nsw.id),
            "service_selections": [{"service_type_id": str(general.id), "room_count": 1}],
        },
        **auth(customer),
    ).json()["id"]

    r = post(client, f"/api/bookings/{booking_id}/cancel", {"reason": "Changed my mind"}, **auth(customer))
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "CANCELLED"
    assert r.json()["cancelled_at"] and r.json()["cancellation_reason"] == "Changed my mind"

    again = post(client, f"/api/bookings/{booking_id}/cancel", {}, **auth(customer))
    assert again.status_code == 409 and again.json()["code"] == "booking_not_cancellable"


@pytest.mark.django_db
def test_cancel_refused_once_payment_has_started(client, customer, property_nsw, general, monkeypatch):
    from decimal import Decimal

    from apps.payments.models import Payment

    _pin_sydney_clock(monkeypatch, 10)
    booking_id = post(
        client,
        "/api/bookings",
        {
            "property_id": str(property_nsw.id),
            "service_selections": [{"service_type_id": str(general.id), "room_count": 1}],
        },
        **auth(customer),
    ).json()["id"]
    Payment.objects.create(booking_id=booking_id, amount=Decimal("125.00"), method="CARD", status="PROCESSING")

    r = post(client, f"/api/bookings/{booking_id}/cancel", {}, **auth(customer))
    assert r.status_code == 409
    assert r.json()["code"] == "cancellation_requires_support"


@pytest.mark.django_db
def test_other_customer_cannot_cancel(client, customer, property_nsw, general, monkeypatch):
    from apps.accounts.models import User

    _pin_sydney_clock(monkeypatch, 10)
    booking_id = post(
        client,
        "/api/bookings",
        {
            "property_id": str(property_nsw.id),
            "service_selections": [{"service_type_id": str(general.id), "room_count": 1}],
        },
        **auth(customer),
    ).json()["id"]
    stranger = User.objects.create_user(phone="+61400555999")
    assert post(client, f"/api/bookings/{booking_id}/cancel", {}, **auth(stranger)).status_code == 404


@pytest.mark.django_db
def test_past_visit_is_rejected_with_400(client, customer, property_nsw, general):
    past = dj_timezone.now() - datetime.timedelta(days=1)
    r = post(
        client,
        "/api/bookings",
        payload(property_nsw, general, past.isoformat()),
        **auth(customer),
    )

    assert r.status_code == 400, r.content
    assert r.json()["code"] == "scheduled_at_in_past"
    assert Booking.objects.count() == 0


@pytest.mark.django_db
def test_visit_outside_business_hours_is_rejected_with_400(
    client, customer, property_nsw, general
):
    r = post(
        client,
        "/api/bookings",
        payload(property_nsw, general, local_slot(hour=22).isoformat()),
        **auth(customer),
    )

    assert r.status_code == 400, r.content
    assert r.json()["code"] == "outside_business_hours"
    assert "07:00" in r.json()["detail"]
    assert Booking.objects.count() == 0


@pytest.mark.django_db
def test_no_booking_row_left_behind_when_schedule_is_invalid(
    client, customer, property_nsw, general
):
    """🔒 التحقق يسبق الكتابة — لا صف يتيم ولا أسطر خدمات."""
    post(
        client,
        "/api/bookings",
        payload(property_nsw, general, local_slot(hour=23).isoformat()),
        **auth(customer),
    )

    assert Booking.objects.count() == 0


# ============================================================
# 5) القائمة والتفصيل (فجوة C01/C16)
# ============================================================
@pytest.mark.django_db
def test_list_includes_scheduled_visit(client, customer, property_nsw, general):
    slot = local_slot(hour=11)
    post(
        client,
        "/api/bookings",
        payload(property_nsw, general, slot.isoformat()),
        **auth(customer),
    )

    listed = client.get("/api/bookings", **auth(customer)).json()

    assert len(listed) == 1
    row = listed[0]
    assert row["scheduled_at"] is not None
    assert row["scheduled_at_local"] is not None
    assert row["customer_timezone"] == "Australia/Sydney"
    assert datetime.datetime.fromisoformat(row["scheduled_at_local"]).hour == 11


@pytest.mark.django_db
def test_retrieve_includes_scheduled_visit(client, customer, property_nsw, general):
    slot = local_slot(hour=8)
    created = post(
        client,
        "/api/bookings",
        payload(property_nsw, general, slot.isoformat()),
        **auth(customer),
    ).json()

    body = client.get(f"/api/bookings/{created['id']}", **auth(customer)).json()

    assert body["scheduled_at"] == created["scheduled_at"]
    assert body["scheduled_at_local"] == created["scheduled_at_local"]
    assert body["customer_timezone"] == "Australia/Sydney"


# ============================================================
# 6) حدود النطاق — ما لا يجوز أن يتغير
# ============================================================
@pytest.mark.django_db
def test_schedule_does_not_affect_price_or_assignment(
    client, customer, property_nsw, general
):
    """⚠️ الموعد لا يدخل التسعير ولا الإسناد (§36.2/§36.1)."""
    body = post(
        client,
        "/api/bookings",
        payload(property_nsw, general, local_slot().isoformat()),
        **auth(customer),
    ).json()

    assert body["computed_price"] is None
    assert body["assigned_contractor_id"] is None
    assert body["status"] == "PENDING"


@pytest.mark.django_db
def test_service_layer_still_creates_without_a_schedule(customer, property_nsw, general):
    """
    ⚠️ الإلزام في الـschema لا في الخدمة: المسارات الداخلية تبقى قادرة
       على إنشاء حجز بلا موعد، كالصفوف السابقة للحقل.
    """
    booking = booking_svc.create_booking(
        customer,
        property_id=property_nsw.id,
        service_selections=[{"service_type_id": general.id, "room_count": 1}],
    )

    assert booking.scheduled_at is None
    # المنطقة تُحسب دائمًا حتى بلا موعد
    assert booking.customer_timezone == "Australia/Sydney"


@pytest.mark.django_db
def test_no_duration_or_slot_fields_are_exposed(client, customer, property_nsw, general):
    """⚠️ نقطة زمنية واحدة فقط — لا مدة ولا معرّف فترة (🟡 #17 مفتوح)."""
    body = post(
        client,
        "/api/bookings",
        payload(property_nsw, general, local_slot().isoformat()),
        **auth(customer),
    ).json()

    for forbidden in ("duration", "duration_minutes", "slot_id", "time_slot",
                      "scheduled_end", "ends_at", "recurrence"):
        assert forbidden not in body

    concrete = [f.name for f in Booking._meta.get_fields() if hasattr(f, "attname")]
    for forbidden in ("duration", "duration_minutes", "slot_id", "time_slot",
                      "scheduled_end", "ends_at", "recurrence"):
        assert forbidden not in concrete
