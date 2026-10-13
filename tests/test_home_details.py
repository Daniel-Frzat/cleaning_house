"""
شاشة "Tell us about your home" — الأسئلة والأيقونات من لوحة التحكم، والإجابات
مع الحجز (قرار PO — 2026-10-13: معلومات فقط، مجمَّدة على الحجز، ومحفوظة على
العقار للتعبئة المسبقة).
"""

import json
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.bookings.models import Booking, DispatchOffer
from apps.services.models import CatalogIcon, HomeDetailField
from tests import backoffice as bo
from tests.test_bookings_dispatch import NEAR, make_contractor, make_property, make_user
from tests.test_bookings_dispatch import auth as user_auth

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24v24H0z"/></svg>'
SEEDED = ["bedrooms", "bathrooms", "living-areas", "floors", "pets"]


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin(db):
    return bo.make_admin()


@pytest.fixture
def customer(db):
    return make_user("+61400009001")


@pytest.fixture
def general(db):
    from apps.services.models import PricingConfig, ServiceType

    PricingConfig.objects.get_or_create(pk=PricingConfig.SINGLETON_PK)
    return ServiceType.objects.create(
        name="General Cleaning", room_price=Decimal("45.00"), base_price=Decimal("80.00")
    )


def send(client, method, url, user, payload=None):
    if method == "get":
        return client.get(url, **bo.auth(user))
    return getattr(client, method)(
        url, data=json.dumps(payload or {}), content_type="application/json", **bo.auth(user)
    )


def upload(client, admin, data=PNG, name="Bed", filename="bed.png"):
    return client.post(
        "/api/admin/icons",
        data={"name": name, "file": SimpleUploadedFile(filename, data)},
        **bo.auth(admin),
    )


def form(client, user, property_id=None):
    url = "/api/home-details/form" + (f"?property_id={property_id}" if property_id else "")
    r = client.get(url, **user_auth(user))
    assert r.status_code == 200, r.content
    return {f["key"]: f for f in r.json()["fields"]}


def book(client, customer, prop, general, home_details=None):
    payload = {
        "property_id": str(prop.id),
        "service_selections": [{"service_type_id": str(general.id), "room_count": 3}],
    }
    if home_details is not None:
        payload["home_details"] = home_details
    return client.post(
        "/api/bookings", data=json.dumps(payload), content_type="application/json",
        **user_auth(customer),
    )


# ============================================================
# الشاشة — الأسئلة الافتراضية
# ============================================================
@pytest.mark.django_db
def test_form_starts_with_the_screen_questions_in_order(client, customer):
    fields = form(client, customer)
    assert list(fields) == SEEDED
    assert fields["floors"]["value"] == 1 and fields["floors"]["min_value"] == 1
    assert fields["bedrooms"]["value"] == 0 and fields["bedrooms"]["input_type"] == "COUNTER"
    assert fields["pets"]["value"] is False and fields["pets"]["min_value"] is None


@pytest.mark.django_db
def test_form_requires_sign_in(client):
    assert client.get("/api/home-details/form").status_code == 401


# ============================================================
# الأيقونات
# ============================================================
@pytest.mark.django_db
def test_admin_uploads_icon_and_app_loads_it_without_a_token(client, admin):
    r = upload(client, admin)
    assert r.status_code == 201, r.content
    icon = r.json()
    assert icon["content_type"] == "image/png" and icon["size"] == len(PNG)
    assert "?v=" in icon["url"]

    file = Client().get(icon["url"])
    assert file.status_code == 200
    assert file.content == PNG
    assert file["Content-Type"] == "image/png"
    assert "immutable" in file["Cache-Control"]

    again = Client().get(icon["url"], HTTP_IF_NONE_MATCH=file["ETag"])
    assert again.status_code == 304


@pytest.mark.django_db
def test_svg_icon_is_served_with_a_sandbox_policy(client, admin):
    r = upload(client, admin, data=SVG, filename="x.svg")
    assert r.status_code == 201, r.content
    assert r.json()["content_type"] == "image/svg+xml"
    file = Client().get(r.json()["url"])
    assert "sandbox" in file["Content-Security-Policy"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.example/x.png"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"/></svg>',
        b"<html><body>not an icon</body></html>",
        b"GIF89a....",
    ],
)
def test_unsafe_or_unknown_files_are_rejected(client, admin, data):
    r = upload(client, admin, data=data, filename="icon.svg")
    assert r.status_code == 422
    assert r.json()["code"] == "unsupported_icon_format"
    assert not CatalogIcon.objects.exists()


@pytest.mark.django_db
def test_icon_size_limit_and_empty_file(client, admin):
    big = upload(client, admin, data=PNG + b"\x00" * CatalogIcon.MAX_BYTES)
    assert big.status_code == 413 and big.json()["code"] == "icon_too_large"
    empty = upload(client, admin, data=b"")
    assert empty.status_code == 422


@pytest.mark.django_db
def test_replacing_an_icon_changes_its_url(client, admin):
    icon = upload(client, admin).json()
    r = client.post(
        f"/api/admin/icons/{icon['id']}/file",
        data={"file": SimpleUploadedFile("x.svg", SVG)},
        **bo.auth(admin),
    )
    assert r.status_code == 200, r.content
    assert r.json()["url"] != icon["url"]
    assert Client().get(r.json()["url"]).content == SVG


@pytest.mark.django_db
def test_icon_in_use_cannot_be_deleted(client, admin, customer):
    icon = upload(client, admin).json()
    bedrooms = HomeDetailField.objects.get(key="bedrooms")
    url = f"/api/admin/home-detail-fields/{bedrooms.id}"
    assert send(client, "patch", url, admin, {"icon_id": icon["id"]}).status_code == 200

    assert form(client, customer)["bedrooms"]["icon"]["url"] == icon["url"]

    blocked = client.delete(f"/api/admin/icons/{icon['id']}", **bo.auth(admin))
    assert blocked.status_code == 409 and blocked.json()["code"] == "icon_in_use"

    assert send(client, "patch", url, admin, {"icon_id": None}).json()["icon"] is None
    assert client.delete(f"/api/admin/icons/{icon['id']}", **bo.auth(admin)).status_code == 204


@pytest.mark.django_db
def test_non_admin_cannot_manage(client, customer):
    for method, url in (
        ("get", "/api/admin/icons"),
        ("get", "/api/admin/home-detail-fields"),
        ("post", "/api/admin/home-detail-fields"),
    ):
        r = getattr(client, method)(url, **user_auth(customer))
        assert r.status_code == 403, url


# ============================================================
# الأسئلة والخيارات من لوحة التحكم
# ============================================================
@pytest.mark.django_db
def test_admin_builds_a_choice_question_and_the_app_sees_it(client, admin, customer):
    icon = upload(client, admin).json()
    r = send(client, "post", "/api/admin/home-detail-fields", admin, {
        "key": "room-contents", "label": "What's in the rooms?",
        "input_type": "MULTI_CHOICE", "section": "EXTRA",
    })
    assert r.status_code == 201, r.content
    field = r.json()
    assert field["sort_order"] == len(SEEDED)  # يُضاف في الآخر

    base = f"/api/admin/home-detail-fields/{field['id']}/options"
    for key, label in (("oven", "Oven"), ("fridge", "Fridge"), ("carpet", "Carpet")):
        r = send(client, "post", base, admin, {"key": key, "label": label, "icon_id": icon["id"]})
        assert r.status_code == 201, r.content
    options = {o["key"]: o["id"] for o in r.json()["options"]}

    dup = send(client, "post", base, admin, {"key": "oven", "label": "Oven again"})
    assert dup.status_code == 409 and dup.json()["code"] == "home_detail_key_taken"

    ids = [options["carpet"], options["oven"], options["fridge"]]
    r = send(client, "put", f"{base}/order", admin, {"ids": ids})
    assert [o["key"] for o in r.json()["options"]] == ["carpet", "oven", "fridge"]

    send(client, "patch", f"{base}/{options['fridge']}", admin, {"is_active": False})

    shown = form(client, customer)["room-contents"]
    assert shown["section"] == "EXTRA" and shown["value"] == []
    assert [o["key"] for o in shown["options"]] == ["carpet", "oven"]
    assert shown["options"][0]["icon"]["url"] == icon["url"]


@pytest.mark.django_db
def test_question_validation(client, admin):
    url = "/api/admin/home-detail-fields"
    bad_default = send(client, "post", url, admin, {
        "key": "garages", "label": "Garages", "input_type": "COUNTER",
        "min_value": 1, "max_value": 3, "default_value": 5,
    })
    assert bad_default.status_code == 422

    taken = send(client, "post", url, admin, {"key": "bedrooms", "label": "x", "input_type": "COUNTER"})
    assert taken.status_code == 409

    bad_key = send(client, "post", url, admin, {"key": "Bed Rooms", "label": "x", "input_type": "COUNTER"})
    assert bad_key.status_code == 422

    floors = HomeDetailField.objects.get(key="floors")
    opt = send(client, "post", f"{url}/{floors.id}/options", admin, {"key": "a", "label": "A"})
    assert opt.status_code == 422 and opt.json()["code"] == "home_detail_not_choice_field"


@pytest.mark.django_db
def test_key_and_type_cannot_be_changed(client, admin):
    floors = HomeDetailField.objects.get(key="floors")
    r = send(client, "patch", f"/api/admin/home-detail-fields/{floors.id}", admin,
             {"key": "levels", "input_type": "YES_NO", "label": "Levels"})
    assert r.status_code == 200
    assert r.json()["key"] == "floors" and r.json()["input_type"] == "COUNTER"
    assert r.json()["label"] == "Levels"


@pytest.mark.django_db
def test_reorder_hide_and_delete_questions(client, admin, customer):
    rows = send(client, "get", "/api/admin/home-detail-fields", admin).json()["items"]
    ids = {row["key"]: row["id"] for row in rows}

    partial = send(client, "put", "/api/admin/home-detail-fields/order", admin, {"ids": [ids["pets"]]})
    assert partial.status_code == 422 and partial.json()["code"] == "invalid_order"

    order = ["pets", "floors", "bedrooms", "bathrooms", "living-areas"]
    r = send(client, "put", "/api/admin/home-detail-fields/order", admin, {"ids": [ids[k] for k in order]})
    assert r.status_code == 200
    assert list(form(client, customer)) == order

    send(client, "patch", f"/api/admin/home-detail-fields/{ids['floors']}", admin, {"is_active": False})
    assert client.delete(f"/api/admin/home-detail-fields/{ids['pets']}", **bo.auth(admin)).status_code == 204
    assert list(form(client, customer)) == ["bedrooms", "bathrooms", "living-areas"]


# ============================================================
# الإجابات مع الحجز
# ============================================================
@pytest.mark.django_db
def test_answers_are_frozen_on_the_booking_and_remembered_on_the_property(
    client, customer, general, django_capture_on_commit_callbacks
):
    prop = make_property(customer)
    answers = {"bedrooms": 3, "bathrooms": 2, "floors": 2, "pets": True}
    r = book(client, customer, prop, general, answers)
    assert r.status_code == 201, r.content

    by_key = {a["key"]: a for a in r.json()["home_details"]}
    assert list(by_key) == ["bedrooms", "bathrooms", "floors", "pets"]
    assert by_key["bedrooms"] == {
        "key": "bedrooms", "label": "Bedrooms", "input_type": "COUNTER",
        "section": "MAIN", "value": 3, "value_labels": ["3"],
    }
    assert by_key["pets"]["value_labels"] == ["Yes"]

    prop.refresh_from_db()
    assert prop.home_details == answers

    prefilled = form(client, customer, prop.id)
    assert prefilled["bedrooms"]["value"] == 3 and prefilled["pets"]["value"] is True
    assert prefilled["living-areas"]["value"] == 0  # لم يُرسل → الافتراضي


@pytest.mark.django_db
def test_booking_snapshot_survives_later_dashboard_changes(client, admin, customer, general):
    prop = make_property(customer)
    booking_id = book(client, customer, prop, general, {"bedrooms": 4}).json()["id"]

    bedrooms = HomeDetailField.objects.get(key="bedrooms")
    send(client, "patch", f"/api/admin/home-detail-fields/{bedrooms.id}", admin, {"label": "Sleeping rooms"})
    client.delete(f"/api/admin/home-detail-fields/{bedrooms.id}", **bo.auth(admin))

    snapshot = Booking.objects.get(pk=booking_id).home_details
    assert snapshot[0]["label"] == "Bedrooms" and snapshot[0]["value"] == 4

    detail = send(client, "get", f"/api/admin/bookings/{booking_id}", admin).json()
    assert detail["home_details"][0]["label"] == "Bedrooms"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "answers, message",
    [
        ({"garages": 1}, "Unknown question"),
        ({"bedrooms": 21}, "between 0 and 20"),
        ({"bedrooms": "3"}, "whole number"),
        ({"bedrooms": True}, "whole number"),
        ({"floors": 0}, "between 1 and 10"),
        ({"pets": "yes"}, "true or false"),
    ],
)
def test_invalid_answers_are_rejected_before_anything_is_saved(client, customer, general, answers, message):
    prop = make_property(customer)
    r = book(client, customer, prop, general, answers)
    assert r.status_code == 400, r.content
    assert r.json()["code"] == "invalid_home_details"
    assert message in r.json()["detail"]
    assert not Booking.objects.exists()
    prop.refresh_from_db()
    assert prop.home_details == {}


@pytest.mark.django_db
def test_required_questions_and_older_apps(client, admin, customer, general):
    pets = HomeDetailField.objects.get(key="pets")
    send(client, "patch", f"/api/admin/home-detail-fields/{pets.id}", admin, {"is_required": True})
    prop = make_property(customer)

    missing = book(client, customer, prop, general, {"bedrooms": 1})
    assert missing.status_code == 400 and "required" in missing.json()["detail"]

    # تطبيق أقدم لا يرسل الحقل أصلًا — لا لقطة ولا فرض
    older = book(client, customer, prop, general)
    assert older.status_code == 201, older.content
    assert older.json()["home_details"] == []


@pytest.mark.django_db
def test_choice_answers_and_stale_prefill(client, admin, customer, general):
    field = send(client, "post", "/api/admin/home-detail-fields", admin, {
        "key": "flooring", "label": "Flooring", "input_type": "SINGLE_CHOICE",
    }).json()
    base = f"/api/admin/home-detail-fields/{field['id']}/options"
    send(client, "post", base, admin, {"key": "tiles", "label": "Tiles"})
    options = send(client, "post", base, admin, {"key": "carpet", "label": "Carpet"}).json()["options"]

    prop = make_property(customer)
    wrong = book(client, customer, prop, general, {"flooring": "marble"})
    assert wrong.status_code == 400 and "no option 'marble'" in wrong.json()["detail"]

    r = book(client, customer, prop, general, {"flooring": "carpet"})
    assert r.json()["home_details"][0]["value_labels"] == ["Carpet"]
    assert form(client, customer, prop.id)["flooring"]["value"] == "carpet"

    # الخيار حُذف من اللوحة → التعبئة المسبقة تعود للافتراضي بدل قيمة ميتة
    carpet = next(o for o in options if o["key"] == "carpet")
    client.delete(f"{base}/{carpet['id']}", **bo.auth(admin))
    assert form(client, customer, prop.id)["flooring"]["value"] is None


@pytest.mark.django_db
def test_prefill_only_from_own_property(client, customer):
    stranger_prop = make_property(make_user("+61400009002"))
    r = client.get(f"/api/home-details/form?property_id={stranger_prop.id}", **user_auth(customer))
    assert r.status_code in (403, 404)


@pytest.mark.django_db
def test_contractor_sees_answers_and_price_is_unchanged(client, customer, general, django_capture_on_commit_callbacks):
    contractor_user, _ = make_contractor("+61400009101", coords=NEAR)
    prop = make_property(customer)

    with django_capture_on_commit_callbacks(execute=True):
        plain = book(client, customer, prop, general).json()
    with django_capture_on_commit_callbacks(execute=True):
        detailed = book(client, customer, prop, general, {"bedrooms": 5, "pets": True}).json()

    plain_offer = DispatchOffer.objects.get(booking_id=plain["id"])
    offer = DispatchOffer.objects.get(booking_id=detailed["id"])
    # 🔒 قرار PO — 2026-10-13: معلومات فقط، لا أثر على السعر
    assert offer.total_amount == plain_offer.total_amount

    r = client.get(f"/api/contractor/offers/{offer.id}", **user_auth(contractor_user))
    assert r.status_code == 200, r.content
    assert [a["key"] for a in r.json()["home_details"]] == ["bedrooms", "pets"]
