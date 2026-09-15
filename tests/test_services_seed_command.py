"""
Seed / list command tests — Services Domain

يغطي: البذر بمعرّفات ثابتة، عدم التكرار عند إعادة التشغيل، احترام
تعديلات الإدارة، و--dry-run الذي لا يكتب شيئًا.

📌 السبب: الواجهة تبني خريطة الأيقونات على service_type_id. لو تغيّرت
   المعرّفات بين البيئات أو بين عمليات النشر لانكسرت الخريطة صامتًا،
   فثباتها سلوك مُختبَر لا اتفاق شفهي.
"""

import json
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from apps.services.management.commands.seed_services import SEED_SERVICES
from apps.services.models import ServiceType


def run(command, *args):
    out = StringIO()
    call_command(command, *args, stdout=out)
    return out.getvalue()


# ============================================================
# 1) البذر
# ============================================================
@pytest.mark.django_db
def test_seed_creates_every_baseline_service():
    assert ServiceType.objects.count() == 0

    run("seed_services")

    assert ServiceType.objects.count() == 7
    names = set(ServiceType.objects.values_list("name", flat=True))
    assert names == {
        "Regular Cleaning", "Deep Cleaning", "End of Lease Cleaning",
        "Inside Oven Clean", "Interior Windows", "Carpet Steam Clean",
        "Balcony Clean",
    }


# 🔒 المعرّفات المنشورة، مكتوبة هنا حرفيًا لا مستوردة من SEED_SERVICES.
#    الاستيراد كان سيجعل الاختبار ذاتي المرجع: يتغيّر مع الكود فلا يمسك
#    شيئًا. هذه النسخة الثانية المستقلة هي ما يكسر البناء فعليًا لو غُيّر
#    معرّف خدمة منشورة — وهو تغيير يكسر خريطة الأيقونات عند كل عميل.
PUBLISHED_IDS = {
    # الخدمات الأساسية — سعر غرفة + رسم أساسي
    "Regular Cleaning": "a1b2c3d4-0001-4000-8000-000000000001",
    "Deep Cleaning": "a1b2c3d4-0002-4000-8000-000000000002",
    "End of Lease Cleaning": "a1b2c3d4-0003-4000-8000-000000000003",
    # الإضافات — رسم ثابت وحده (room_price = 0)
    "Inside Oven Clean": "a1b2c3d4-0101-4000-8000-000000000101",
    "Interior Windows": "a1b2c3d4-0102-4000-8000-000000000102",
    "Carpet Steam Clean": "a1b2c3d4-0103-4000-8000-000000000103",
    "Balcony Clean": "a1b2c3d4-0104-4000-8000-000000000104",
}

PUBLISHED_ADDON_IDS = {
    "a1b2c3d4-0101-4000-8000-000000000101",
    "a1b2c3d4-0102-4000-8000-000000000102",
    "a1b2c3d4-0103-4000-8000-000000000103",
    "a1b2c3d4-0104-4000-8000-000000000104",
}


@pytest.mark.django_db
def test_seeded_ids_match_the_published_constants_literally():
    """
    🔒 المعرّفات ثابتة عبر البيئات — عليها تُبنى خريطة الأيقونات.

    تغيير أي معرّف هنا يعني كسر خرائط كل العملاء المنشورين، فالاختبار
    يقارن بنسخة مكتوبة يدويًا لا بالثابت نفسه.
    """
    run("seed_services")

    actual = {s.name: str(s.pk) for s in ServiceType.objects.all()}
    assert actual == PUBLISHED_IDS


def test_seed_constants_still_declare_the_published_ids():
    """يمسك تغيير المعرّف حتى دون لمس قاعدة البيانات."""
    declared = {s["name"]: s["id"] for s in SEED_SERVICES}
    assert declared == PUBLISHED_IDS


@pytest.mark.django_db
def test_seeded_services_are_active_and_therefore_publicly_listed():
    run("seed_services")

    assert ServiceType.objects.filter(is_active=True).count() == 7


@pytest.mark.django_db
def test_seeded_services_have_a_description():
    """الوصف يصل للواجهة عبر /api/services — لا يجوز أن يكون فارغًا."""
    run("seed_services")

    for service in ServiceType.objects.all():
        assert service.description.strip()


# ============================================================
# 1b) الإضافات (add-ons)
# ============================================================
@pytest.mark.django_db
def test_addons_have_zero_room_price_and_a_flat_base_price():
    """
    📌 هذا ما يجعل "الإضافة" إضافةً: رسم ثابت لا يتغيّر بعدد الغرف.

    المعادلة (room_price × room_count) + base_price تعطي base_price
    وحده حين يكون room_price صفرًا.
    """
    run("seed_services")

    for pk in PUBLISHED_ADDON_IDS:
        addon = ServiceType.objects.get(pk=pk)
        assert addon.room_price == Decimal("0.00"), addon.name
        assert addon.base_price > 0, addon.name


@pytest.mark.django_db
def test_baseline_services_do_charge_per_room():
    """الخدمات الأساسية عكس الإضافات — سعر غرفة موجب."""
    run("seed_services")

    for name in ("Regular Cleaning", "Deep Cleaning", "End of Lease Cleaning"):
        service = ServiceType.objects.get(name=name)
        assert service.room_price > 0, name


@pytest.mark.django_db
def test_addon_price_is_flat_whatever_the_room_count():
    """
    🔒 الحماية العملية: لو أرسلت الواجهة room_count خاطئًا لإضافة، لا
       يتشوّه السعر — لأن room_price صفر.
    """
    from apps.services.services.pricing import calculate_price

    run("seed_services")
    oven = ServiceType.objects.get(name="Inside Oven Clean")

    prices = {
        calculate_price(
            service_selections=[{"service_type_id": oven.id, "room_count": n}],
            distance_km=Decimal("0"),
        )
        for n in (0, 1, 5, 100)
    }

    assert prices == {oven.base_price}


@pytest.mark.django_db
def test_addon_adds_its_flat_fee_on_top_of_a_baseline_service():
    """التركيبة المتوقَّعة من الواجهة: خدمة أساسية + إضافة."""
    from apps.services.services.pricing import calculate_price

    run("seed_services")
    regular = ServiceType.objects.get(name="Regular Cleaning")
    oven = ServiceType.objects.get(name="Inside Oven Clean")

    alone = calculate_price(
        service_selections=[{"service_type_id": regular.id, "room_count": 3}],
        distance_km=Decimal("0"),
    )
    together = calculate_price(
        service_selections=[
            {"service_type_id": regular.id, "room_count": 3},
            {"service_type_id": oven.id, "room_count": 0},
        ],
        distance_km=Decimal("0"),
    )

    assert together == alone + oven.base_price


@pytest.mark.django_db
def test_addon_is_bookable_like_any_other_service(client):
    """
    ⚠️ لا يميّز الباكند الإضافة: تُحجز بنفس الطريقة تمامًا.

    يثبت أن room_count=0 مقبول فعلًا في مسار الحجز الحقيقي.
    """
    import json as _json
    from decimal import Decimal as _D

    from apps.accounts.models import User
    from apps.accounts.roles import ConfirmedRole
    from apps.accounts.services.tokens import issue_tokens_for_user
    from apps.properties.models import Property, PropertyAddress, PropertyType
    import datetime
    from zoneinfo import ZoneInfo

    run("seed_services")
    user = User.objects.create_user(phone="+61400779001", role=ConfirmedRole.CUSTOMER)
    auth = {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}
    prop = Property.objects.create(
        owner=user, label="H", property_type=PropertyType.HOUSE
    )
    PropertyAddress.objects.create(
        property=prop, street_address="1 St", suburb="Sydney",
        state="NSW", postcode="2000",
    )
    slot = (
        datetime.datetime.now(ZoneInfo("Australia/Sydney"))
        + datetime.timedelta(days=3)
    ).replace(hour=10, minute=0, second=0, microsecond=0)

    regular = ServiceType.objects.get(name="Regular Cleaning")
    oven = ServiceType.objects.get(name="Inside Oven Clean")

    r = client.post(
        "/api/bookings",
        data=_json.dumps({
            "property_id": str(prop.id),
            "service_selections": [
                {"service_type_id": str(regular.id), "room_count": 2},
                {"service_type_id": str(oven.id), "room_count": 0},
            ],
            "scheduled_at": slot.isoformat(),
        }),
        content_type="application/json",
        **auth,
    )

    assert r.status_code == 201, r.content
    selections = r.json()["service_selections"]
    assert len(selections) == 2
    assert {s["service_type_name"] for s in selections} == {
        "Regular Cleaning", "Inside Oven Clean"
    }


@pytest.mark.django_db
def test_addons_appear_in_the_public_catalog(client):
    """
    ⚠️ لا قسم منفصل في الـAPI: الإضافات تظهر مع البقية في قائمة واحدة.
       فصلها بصريًا قرار واجهة يُبنى على معرّفاتها الثابتة.
    """
    from apps.accounts.models import User
    from apps.accounts.roles import ConfirmedRole
    from apps.accounts.services.tokens import issue_tokens_for_user

    run("seed_services")
    user = User.objects.create_user(phone="+61400779002", role=ConfirmedRole.CUSTOMER)
    auth = {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}

    listed = client.get("/api/services", **auth).json()

    assert len(listed) == 7
    assert PUBLISHED_ADDON_IDS <= {s["id"] for s in listed}


def test_addon_ids_constant_matches_the_published_set():
    """ADDON_IDS يُشتق من room_price==0 — نتحقق أنه يطابق المنشور."""
    from apps.services.management.commands.seed_services import ADDON_IDS

    assert set(ADDON_IDS) == PUBLISHED_ADDON_IDS


# ============================================================
# 2) عدم التكرار
# ============================================================
@pytest.mark.django_db
def test_running_twice_creates_nothing_new():
    run("seed_services")
    output = run("seed_services")

    assert ServiceType.objects.count() == 7
    assert "0 created, 7 already existed" in output


@pytest.mark.django_db
def test_seed_does_not_overwrite_admin_edits():
    """
    ⚠️ الإدارة تملك القيم بعد البذر: إعادة التشغيل لا تدهس تعديلاتها.

    الأسعار والأسماء تُدار عبر PATCH /api/admin/services، والبذر ينشئ
    المفقود فقط.
    """
    run("seed_services")
    service = ServiceType.objects.get(pk=SEED_SERVICES[0]["id"])
    service.name = "Renamed By Admin"
    service.room_price = Decimal("99.00")
    service.save()

    run("seed_services")

    service.refresh_from_db()
    assert service.name == "Renamed By Admin"
    assert service.room_price == Decimal("99.00")
    assert ServiceType.objects.count() == 7


@pytest.mark.django_db
def test_seed_matches_by_name_when_id_differs():
    """
    خدمة أنشأتها الإدارة يدويًا بنفس الاسم ومعرّف آخر لا تُكرَّر.

    الاسم فريد على مستوى قاعدة البيانات، فالإنشاء كان سيفشل بخطأ
    IntegrityError بدل رسالة مفهومة.
    """
    ServiceType.objects.create(
        name="Deep Cleaning",
        description="created by hand",
        room_price=Decimal("10.00"),
        base_price=Decimal("10.00"),
    )

    run("seed_services")

    assert ServiceType.objects.filter(name="Deep Cleaning").count() == 1
    assert ServiceType.objects.count() == 7


# ============================================================
# 3) dry-run
# ============================================================
@pytest.mark.django_db
def test_dry_run_writes_nothing():
    output = run("seed_services", "--dry-run")

    assert ServiceType.objects.count() == 0
    assert "Nothing written" in output


@pytest.mark.django_db
def test_dry_run_after_seeding_reports_all_existing():
    run("seed_services")
    output = run("seed_services", "--dry-run")

    assert "0 would be created, 7 already exist" in output
    assert ServiceType.objects.count() == 7


# ============================================================
# 4) أمر العرض
# ============================================================
@pytest.mark.django_db
def test_list_reports_an_empty_catalog_clearly():
    output = run("list_services")

    assert "EMPTY" in output


@pytest.mark.django_db
def test_list_prints_every_active_service_with_its_id():
    run("seed_services")

    output = run("list_services")

    for spec in SEED_SERVICES:
        assert spec["id"] in output
        assert spec["name"] in output


@pytest.mark.django_db
def test_list_json_is_machine_readable_and_carries_ids():
    """المخرجات JSON تُلصق مباشرة في كود الواجهة."""
    run("seed_services")

    payload = json.loads(run("list_services", "--json"))

    assert len(payload) == 7
    assert {p["id"] for p in payload} == {s["id"] for s in SEED_SERVICES}
    for item in payload:
        assert set(item) == {"id", "name", "description", "is_active"}


@pytest.mark.django_db
def test_list_hides_inactive_services_by_default_but_all_shows_them():
    """يطابق سلوك GET /api/services: النشط فقط."""
    run("seed_services")
    dead = ServiceType.objects.get(pk=SEED_SERVICES[0]["id"])
    dead.is_active = False
    dead.save()

    default = json.loads(run("list_services", "--json"))
    every = json.loads(run("list_services", "--json", "--all"))

    assert len(default) == 6
    assert len(every) == 7
    assert dead.name not in {d["name"] for d in default}


# ============================================================
# 5) التكامل مع الـAPI
# ============================================================
@pytest.mark.django_db
def test_seeded_ids_are_accepted_by_the_public_catalog_endpoint(client):
    """
    📌 الغرض كله: المعرّف المعروض هو نفسه المقبول في إنشاء الحجز.
    """
    from apps.accounts.models import User
    from apps.accounts.roles import ConfirmedRole
    from apps.accounts.services.tokens import issue_tokens_for_user

    run("seed_services")
    user = User.objects.create_user(phone="+61400778001", role=ConfirmedRole.CUSTOMER)
    auth = {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}

    listed = client.get("/api/services", **auth).json()

    assert {s["id"] for s in listed} == {s["id"] for s in SEED_SERVICES}
    for item in listed:
        assert set(item) == {"id", "name", "description"}
