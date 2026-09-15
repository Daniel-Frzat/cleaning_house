"""
Address consistency & serviceability tests — Properties Domain

موضوعان اكتُشفا من شاشة C05-address الحقيقية:

  1) عنوان متناقض (رمز بريدي من ولاية أخرى) كان يُقبل بـ201 صامتًا.
     ⚠️ المنطقة الزمنية تُشتق من state، فالولاية الخاطئة تعني نافذة
        ساعات عمل خاطئة — لا خطأ تجميليًا.

  2) عقار بلا إحداثيات كان يُنشأ بنجاح دون أي إشارة، ثم لا يُسنَد إليه
     مقاول أبدًا. الفشل كان غير مرئي حتى يظهر لاحقًا كـNO_CONTRACTOR.
"""

import json

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.properties.services.postcodes import (
    postcode_matches_state,
    postcode_state_error,
    states_for_postcode,
)
from apps.properties.services.serviceability import (
    REASON_MISSING_COORDINATES,
    missing_coordinates,
    serviceability_warning,
)


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def customer(db):
    return User.objects.create_user(phone="+61400552001",
                                    role=ConfirmedRole.CUSTOMER)


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


def address(**over):
    base = {
        "street_address": "24 Park St",
        "suburb": "Fitzroy",
        "state": "VIC",
        "postcode": "3065",
    }
    base.update(over)
    return base


def create(client, user, **over):
    payload = {"label": "Home", "property_type": "HOUSE", "address": address(**over)}
    return client.post("/api/properties", data=json.dumps(payload),
                       content_type="application/json", **auth(user))


# ============================================================
# 1) اتساق الرمز البريدي مع الولاية
# ============================================================
@pytest.mark.django_db
def test_the_screenshot_address_is_rejected(client, customer):
    """
    📌 الحالة الحقيقية من التصميم: Fitzroy / VIC / 2311.

    2311 نطاق نيو ساوث ويلز، وFitzroy في فيكتوريا.
    """
    r = create(client, customer, postcode="2311")

    assert r.status_code == 422, r.content
    assert "2311" in r.json()["detail"]


@pytest.mark.django_db
def test_the_error_names_the_right_state(client, customer):
    """العميل أخطأ في الولاية غالبًا — الرسالة تدلّه عليها."""
    r = create(client, customer, postcode="2311")

    assert "NSW" in r.json()["detail"]


@pytest.mark.django_db
def test_the_correct_address_is_accepted(client, customer):
    r = create(client, customer, postcode="3065")

    assert r.status_code == 201, r.content


@pytest.mark.django_db
def test_consistency_is_enforced_on_update_too(client, customer):
    """⚠️ القاعدة على النموذج، فلا يفلت منها مسار التعديل."""
    prop_id = create(client, customer).json()["id"]

    r = client.patch(
        f"/api/properties/{prop_id}",
        data=json.dumps({"address": {"postcode": "2311"}}),
        content_type="application/json", **auth(customer),
    )

    assert r.status_code == 422, r.content


@pytest.mark.django_db
@pytest.mark.parametrize("state,postcode", [
    ("NSW", "2000"), ("VIC", "3000"), ("QLD", "4000"), ("SA", "5000"),
    ("WA", "6000"), ("TAS", "7000"), ("NT", "0800"), ("ACT", "2600"),
])
def test_every_state_accepts_its_own_capital(state, postcode):
    """📌 كل ولاية ورمز عاصمتها — لا ولاية مقصيّة بالخطأ."""
    assert postcode_matches_state(postcode, state)


@pytest.mark.django_db
@pytest.mark.parametrize("state,postcode", [
    ("VIC", "2311"), ("NSW", "3000"), ("QLD", "6000"), ("WA", "4000"),
])
def test_mismatches_are_caught(state, postcode):
    assert not postcode_matches_state(postcode, state)


@pytest.mark.django_db
def test_act_and_nsw_share_ranges_without_conflict():
    """⚠️ ACT محاطة بـNSW: 2600 لها، و2000 لنيو ساوث ويلز."""
    assert postcode_matches_state("2600", "ACT")
    assert postcode_matches_state("2000", "NSW")
    assert not postcode_matches_state("2600", "NSW")


@pytest.mark.django_db
def test_a_malformed_postcode_is_not_this_checks_error(client, customer):
    """
    ⚠️ شكل الرمز مسؤولية POSTCODE_VALIDATOR لا هذا الفحص.

    وإلا لظهر "لا يطابق الولاية" على رمز شكله خاطئ أصلًا — رسالة مضلّلة.
    """
    assert postcode_state_error("231", "VIC") is None
    assert postcode_state_error("", "VIC") is None

    r = create(client, customer, postcode="231")
    assert r.status_code == 422
    assert "not in" not in r.json()["detail"]


@pytest.mark.django_db
def test_states_for_postcode_is_used_by_the_message():
    assert states_for_postcode("2311") == ["NSW"]
    assert states_for_postcode("3000") == ["VIC"]
    assert states_for_postcode("abcd") == []


# ============================================================
# 2) تحذير قابلية الإسناد
# ============================================================
@pytest.mark.django_db
def test_a_property_without_coordinates_is_flagged(client, customer):
    """
    📌 العطب الصامت: يُنشأ بنجاح، ثم لا يُسنَد إليه مقاول أبدًا.
    """
    r = create(client, customer)

    assert r.status_code == 201
    warning = r.json()["serviceability_warning"]
    assert warning is not None
    assert warning["code"] == REASON_MISSING_COORDINATES


@pytest.mark.django_db
def test_a_property_with_coordinates_is_not_flagged(client, customer):
    r = create(client, customer, latitude="-37.798700", longitude="144.978800")

    assert r.status_code == 201
    assert r.json()["serviceability_warning"] is None


@pytest.mark.django_db
def test_half_a_coordinate_is_still_missing(client, customer):
    """⚠️ نصف إحداثية لا تُقاس بها مسافة — كما في محرّك المسافة تمامًا."""
    r = create(client, customer, latitude="-37.798700")

    assert r.json()["serviceability_warning"]["code"] == REASON_MISSING_COORDINATES


@pytest.mark.django_db
def test_the_warning_clears_once_coordinates_are_added(client, customer):
    """📌 مشتقّ لا مخزَّن: PATCH يكفي، بلا إعادة إنشاء."""
    prop_id = create(client, customer).json()["id"]

    r = client.patch(
        f"/api/properties/{prop_id}",
        data=json.dumps({"address": {"latitude": "-37.798700",
                                     "longitude": "144.978800"}}),
        content_type="application/json", **auth(customer),
    )

    assert r.status_code == 200, r.content
    assert r.json()["serviceability_warning"] is None


@pytest.mark.django_db
def test_the_warning_appears_in_the_list_too(client, customer):
    """قائمة العقارات هي مكان الشارة في الواجهة."""
    create(client, customer)
    create(client, customer, latitude="-37.798700", longitude="144.978800")

    rows = client.get("/api/properties", **auth(customer)).json()

    flagged = [r for r in rows if r["serviceability_warning"] is not None]
    assert len(flagged) == 1


@pytest.mark.django_db
def test_the_warning_matches_what_dispatch_actually_does(client, customer):
    """
    🔒 التحذير ليس رأيًا مستقلًا: يطابق شرط محرّك المسافة نفسه.

    لو تغيّر أحدهما دون الآخر لأصبح التحذير كذبًا.
    """
    from apps.bookings.services.distance import property_coordinates
    from apps.properties.models import Property

    create(client, customer)
    create(client, customer, latitude="-37.798700", longitude="144.978800")

    for prop in Property.objects.all():
        warned = serviceability_warning(prop) is not None
        undispatchable = property_coordinates(prop) is None
        assert warned == undispatchable
        assert missing_coordinates(prop) == undispatchable
