"""
Pricing Engine Unit Tests — Services Domain (Change Set §36.2)

يتحقق من الصيغة المعتمدة حرفيًا:

    per_service_amount = (room_price × room_count) + base_price
    total = Σ per_service_amount + (distance_km × price_per_km)

ويتحقق من الحدود: خدمة معطّلة، اختيار فارغ، مسافة سالبة، وغياب أي
لقطة سعر (snapshotting) داخل هذه الدالة.
"""

import uuid
from decimal import Decimal

import pytest

from apps.services.models import PricingConfig, ServiceType
from apps.services.services import pricing
from apps.services.services.pricing import calculate_price


# ------------------------------------------------------------
# أدوات
# ------------------------------------------------------------
def make_service(name, room_price, base_price, is_active=True):
    return ServiceType.objects.create(
        name=name,
        room_price=Decimal(room_price),
        base_price=Decimal(base_price),
        is_active=is_active,
    )


def set_price_per_km(value):
    config, _ = PricingConfig.objects.get_or_create(pk=PricingConfig.SINGLETON_PK)
    config.price_per_km = Decimal(value)
    config.save()
    return config


def select(service, room_count):
    return {"service_type_id": service.id, "room_count": room_count}


@pytest.fixture
def general(db):
    # 45 للغرفة + 80 أساسي
    return make_service("General Cleaning", "45.00", "80.00")


@pytest.fixture
def carpet(db):
    # 30 للغرفة + 50 أساسي
    return make_service("Carpet Cleaning", "30.00", "50.00")


@pytest.fixture
def garden(db):
    # 20 للغرفة + 25 أساسي
    return make_service("Garden Cleaning", "20.00", "25.00")


@pytest.fixture
def inactive_service(db):
    return make_service("Retired Cleaning", "10.00", "10.00", is_active=False)


# ============================================================
# 1) خدمة واحدة، عدد غرف واحد
# ============================================================
@pytest.mark.django_db
def test_single_service_single_room_count(general):
    set_price_per_km("0.00")

    # (45 × 3) + 80 = 215
    total = calculate_price([select(general, 3)], Decimal("0"))

    assert total == Decimal("215.00")


@pytest.mark.django_db
def test_single_service_with_distance(general):
    set_price_per_km("2.50")

    # (45 × 3) + 80 = 215، + (10 × 2.50) = 25 → 240
    total = calculate_price([select(general, 3)], Decimal("10"))

    assert total == Decimal("240.00")


@pytest.mark.django_db
def test_single_room(general):
    set_price_per_km("0.00")

    # (45 × 1) + 80 = 125
    assert calculate_price([select(general, 1)], Decimal("0")) == Decimal("125.00")


@pytest.mark.django_db
def test_zero_rooms_charges_base_price_only(general):
    """صفر غرف قيمة مشروعة — يتبقّى الرسم الأساسي وحده."""
    set_price_per_km("0.00")

    assert calculate_price([select(general, 0)], Decimal("0")) == Decimal("80.00")


@pytest.mark.django_db
def test_result_is_decimal_quantized_to_two_places(general):
    set_price_per_km("0.00")

    total = calculate_price([select(general, 2)], Decimal("0"))

    assert isinstance(total, Decimal)
    assert total.as_tuple().exponent == -2


# ============================================================
# 2) خدمات متعددة — تراكمية (§36.2)
# ============================================================
@pytest.mark.django_db
def test_multiple_services_are_additive(general, carpet):
    set_price_per_km("0.00")

    # general: (45 × 2) + 80 = 170
    # carpet:  (30 × 2) + 50 = 110
    # total = 280
    total = calculate_price(
        [select(general, 2), select(carpet, 2)], Decimal("0")
    )

    assert total == Decimal("280.00")


@pytest.mark.django_db
def test_three_services_each_with_own_prices(general, carpet, garden):
    set_price_per_km("0.00")

    # general: (45 × 1) + 80 = 125
    # carpet:  (30 × 2) + 50 = 110
    # garden:  (20 × 3) + 25 = 85
    # total = 320
    total = calculate_price(
        [select(general, 1), select(carpet, 2), select(garden, 3)], Decimal("0")
    )

    assert total == Decimal("320.00")


@pytest.mark.django_db
def test_each_service_uses_its_own_room_price_not_a_shared_one(general, carpet):
    """
    §36.2: السعر مُحدَّد لكل خدمة. لو كانت الأسعار مشتركة لاختلف الناتج.
    """
    set_price_per_km("0.00")

    total = calculate_price([select(general, 4), select(carpet, 4)], Decimal("0"))

    # general: (45 × 4) + 80 = 260 ؛ carpet: (30 × 4) + 50 = 170 → 430
    assert total == Decimal("430.00")
    # لو استُخدم سعر general لكليهما لكان 260 + 260 = 520
    assert total != Decimal("520.00")


@pytest.mark.django_db
def test_total_equals_sum_of_individual_calculations(general, carpet, garden):
    """التجميع تراكمي بحت: مجموع الاستدعاءات المنفصلة = الاستدعاء المجمّع."""
    set_price_per_km("0.00")

    combined = calculate_price(
        [select(general, 2), select(carpet, 3), select(garden, 1)], Decimal("0")
    )
    separate = (
        calculate_price([select(general, 2)], Decimal("0"))
        + calculate_price([select(carpet, 3)], Decimal("0"))
        + calculate_price([select(garden, 1)], Decimal("0"))
    )

    assert combined == separate


@pytest.mark.django_db
def test_duplicate_service_lines_are_counted_as_given(general):
    """
    الدمج ليس قاعدة معتمدة في §36.2 — كل سطر يُحتسب كما ورد، بأساسه.
    """
    set_price_per_km("0.00")

    total = calculate_price([select(general, 1), select(general, 1)], Decimal("0"))

    # ((45 × 1) + 80) × 2 = 250
    assert total == Decimal("250.00")


# ============================================================
# 3) مكوّن المسافة يُحتسب مرة واحدة
# ============================================================
@pytest.mark.django_db
def test_distance_component_counted_once_regardless_of_service_count(
    general, carpet, garden
):
    """
    المسافة للطلب لا للخدمة: إضافة خدمات لا تضاعف مكوّن المسافة.
    """
    set_price_per_km("3.00")
    distance = Decimal("10")  # مكوّن المسافة = 30 دائمًا

    one = calculate_price([select(general, 1)], distance)
    two = calculate_price([select(general, 1), select(carpet, 1)], distance)
    three = calculate_price(
        [select(general, 1), select(carpet, 1), select(garden, 1)], distance
    )

    # general = 125، carpet = 80، garden = 45، المسافة = 30
    assert one == Decimal("155.00")  # 125 + 30
    assert two == Decimal("235.00")  # 125 + 80 + 30
    assert three == Decimal("280.00")  # 125 + 80 + 45 + 30

    # الفروق تساوي قيمة الخدمة المضافة وحدها — بلا أي مسافة إضافية
    assert two - one == Decimal("80.00")
    assert three - two == Decimal("45.00")


@pytest.mark.django_db
def test_distance_component_isolated_by_differencing(general, carpet):
    """
    نفس السلة بمسافتين مختلفتين: الفارق = (Δmسافة × price_per_km) فقط.
    """
    set_price_per_km("2.00")
    basket = [select(general, 2), select(carpet, 2)]

    near = calculate_price(basket, Decimal("5"))
    far = calculate_price(basket, Decimal("15"))

    assert far - near == Decimal("20.00")  # (15 - 5) × 2.00


@pytest.mark.django_db
def test_zero_distance_adds_nothing(general):
    set_price_per_km("5.00")

    assert calculate_price([select(general, 2)], Decimal("0")) == Decimal("170.00")


@pytest.mark.django_db
def test_zero_price_per_km_makes_distance_free(general):
    set_price_per_km("0.00")

    near = calculate_price([select(general, 2)], Decimal("0"))
    far = calculate_price([select(general, 2)], Decimal("500"))

    assert near == far == Decimal("170.00")


@pytest.mark.django_db
def test_price_per_km_is_global_not_per_service(general, carpet):
    """
    §5: سعر كيلومتر واحد للنظام. تغييره يغيّر مكوّن المسافة مرة واحدة فقط
    مهما بلغ عدد الخدمات.
    """
    basket = [select(general, 1), select(carpet, 1)]

    set_price_per_km("1.00")
    at_one = calculate_price(basket, Decimal("10"))  # مسافة = 10

    set_price_per_km("4.00")
    at_four = calculate_price(basket, Decimal("10"))  # مسافة = 40

    assert at_four - at_one == Decimal("30.00")


@pytest.mark.django_db
def test_fractional_distance_and_rate(general):
    set_price_per_km("1.35")

    # (45 × 1) + 80 = 125 ؛ 12.5 × 1.35 = 16.875 → 141.875 → 141.88
    total = calculate_price([select(general, 1)], Decimal("12.5"))

    assert total == Decimal("141.88")


@pytest.mark.django_db
def test_missing_pricing_config_treats_distance_as_free(general):
    """غياب الصف = نظام غير مضبوط بعد؛ لا انهيار، والصفر هو افتراض الـModel."""
    PricingConfig.objects.all().delete()

    total = calculate_price([select(general, 2)], Decimal("100"))

    assert total == Decimal("170.00")


# ============================================================
# 4) خدمة معطّلة → استثناء
# ============================================================
@pytest.mark.django_db
def test_inactive_service_raises(inactive_service):
    set_price_per_km("0.00")

    with pytest.raises(pricing.InactiveServiceError):
        calculate_price([select(inactive_service, 2)], Decimal("0"))


@pytest.mark.django_db
def test_inactive_service_raises_even_when_mixed_with_active_ones(
    general, carpet, inactive_service
):
    """لا تسعير جزئي: وجود خدمة معطّلة يُبطل الطلب كله."""
    set_price_per_km("0.00")

    with pytest.raises(pricing.InactiveServiceError):
        calculate_price(
            [select(general, 1), select(inactive_service, 1), select(carpet, 1)],
            Decimal("0"),
        )


@pytest.mark.django_db
def test_inactive_error_names_the_service_and_carries_a_code(inactive_service):
    with pytest.raises(pricing.InactiveServiceError) as exc:
        calculate_price([select(inactive_service, 1)], Decimal("0"))

    assert exc.value.code == "inactive_service"
    assert "Retired Cleaning" in str(exc.value)


@pytest.mark.django_db
def test_service_deactivated_after_creation_stops_being_priceable(general):
    """التعطيل الناعم من الإدارة يسري فورًا على التسعير اللاحق."""
    set_price_per_km("0.00")
    assert calculate_price([select(general, 1)], Decimal("0")) == Decimal("125.00")

    general.is_active = False
    general.save()

    with pytest.raises(pricing.InactiveServiceError):
        calculate_price([select(general, 1)], Decimal("0"))


@pytest.mark.django_db
def test_unknown_service_id_raises(db):
    set_price_per_km("0.00")

    with pytest.raises(pricing.UnknownServiceError):
        calculate_price(
            [{"service_type_id": uuid.uuid4(), "room_count": 1}], Decimal("0")
        )


# ============================================================
# 5) اختيار فارغ → استثناء
# ============================================================
@pytest.mark.django_db
def test_empty_selection_raises(db):
    with pytest.raises(pricing.EmptySelectionError):
        calculate_price([], Decimal("10"))


@pytest.mark.django_db
def test_none_selection_raises(db):
    with pytest.raises(pricing.EmptySelectionError):
        calculate_price(None, Decimal("10"))


@pytest.mark.django_db
def test_empty_selection_error_carries_code(db):
    with pytest.raises(pricing.EmptySelectionError) as exc:
        calculate_price([], Decimal("0"))

    assert exc.value.code == "empty_service_selection"


@pytest.mark.django_db
def test_empty_selection_raises_even_with_positive_distance(db):
    """لا يُسعَّر طلب بالمسافة وحدها."""
    set_price_per_km("5.00")

    with pytest.raises(pricing.EmptySelectionError):
        calculate_price([], Decimal("100"))


@pytest.mark.django_db
def test_malformed_selection_entries_raise(general):
    set_price_per_km("0.00")

    with pytest.raises(pricing.PricingError):
        calculate_price([{"room_count": 2}], Decimal("0"))  # بلا معرّف

    with pytest.raises(pricing.PricingError):
        calculate_price([{"service_type_id": general.id}], Decimal("0"))  # بلا عدد

    with pytest.raises(pricing.PricingError):
        calculate_price(["not-a-dict"], Decimal("0"))


# ============================================================
# 6) مسافة سالبة → استثناء
# ============================================================
@pytest.mark.django_db
def test_negative_distance_raises(general):
    set_price_per_km("2.00")

    with pytest.raises(pricing.InvalidDistanceError):
        calculate_price([select(general, 1)], Decimal("-1"))


@pytest.mark.django_db
def test_negative_distance_error_carries_code(general):
    with pytest.raises(pricing.InvalidDistanceError) as exc:
        calculate_price([select(general, 1)], Decimal("-0.01"))

    assert exc.value.code == "invalid_distance"


@pytest.mark.django_db
def test_tiny_negative_distance_still_raises(general):
    """لا تسامح مع السالب مهما صغر — لا تقريب إلى الصفر."""
    with pytest.raises(pricing.InvalidDistanceError):
        calculate_price([select(general, 1)], Decimal("-0.0001"))


@pytest.mark.django_db
def test_float_distance_is_rejected(general):
    """
    الـfloat مرفوض عمدًا: التمثيل الثنائي غير دقيق للحساب النقدي.
    """
    with pytest.raises(pricing.InvalidDistanceError):
        calculate_price([select(general, 1)], 10.5)


@pytest.mark.django_db
def test_integer_distance_is_accepted(general):
    """العدد الصحيح غير غامض ويُحوَّل بأمان."""
    set_price_per_km("2.00")

    assert calculate_price([select(general, 1)], 10) == Decimal("145.00")


@pytest.mark.django_db
def test_negative_room_count_raises(general):
    with pytest.raises(pricing.InvalidRoomCountError):
        calculate_price([select(general, -1)], Decimal("0"))


@pytest.mark.django_db
def test_non_integer_room_count_raises(general):
    with pytest.raises(pricing.InvalidRoomCountError):
        calculate_price([select(general, 2.5)], Decimal("0"))


@pytest.mark.django_db
def test_nothing_is_computed_when_validation_fails(general, inactive_service):
    """
    التحقق يسبق الحساب: الاستثناء يُرفع ولا يُعاد أي مبلغ جزئي.
    """
    set_price_per_km("2.00")

    for bad_call in (
        lambda: calculate_price([select(general, 1)], Decimal("-5")),
        lambda: calculate_price([select(inactive_service, 1)], Decimal("5")),
        lambda: calculate_price([], Decimal("5")),
    ):
        with pytest.raises(pricing.PricingError):
            bad_call()


# ============================================================
# 7) لا لقطة سعر — المسؤولية مؤجَّلة إلى Booking Domain
# ============================================================
@pytest.mark.django_db
def test_price_change_is_reflected_proving_no_snapshotting(general):
    """
    ⚠️ الاختبار المحوري لحدود المسؤولية.

    استدعاء قبل تعديل الإدارة للسعر، واستدعاء بعده → نتيجتان مختلفتان.
    هذا يثبت أن calculate_price تقرأ القيم الحيّة ولا تحتفظ بأي لقطة.
    تثبيت السعر على حجز بعينه شأن Booking Domain، وليس هنا.
    """
    set_price_per_km("0.00")

    before = calculate_price([select(general, 2)], Decimal("0"))
    assert before == Decimal("170.00")  # (45 × 2) + 80

    # الإدارة ترفع السعر (مسموح في أي وقت — §36.2)
    general.room_price = Decimal("60.00")
    general.save()

    after = calculate_price([select(general, 2)], Decimal("0"))
    assert after == Decimal("200.00")  # (60 × 2) + 80

    assert after != before


@pytest.mark.django_db
def test_previously_returned_decimal_is_not_mutated_by_a_price_change(general):
    """
    القيمة المُعادة Decimal ثابت (immutable): تعديل الكتالوج لاحقًا لا
    يغيّرها بأثر رجعي. المستدعي الذي يريد الاحتفاظ بها يخزّنها بنفسه —
    وهو بالضبط ما سيفعله Booking Domain.
    """
    set_price_per_km("1.00")

    first = calculate_price([select(general, 2)], Decimal("10"))
    assert first == Decimal("180.00")  # 170 + 10

    general.room_price = Decimal("90.00")
    general.base_price = Decimal("100.00")
    general.save()
    set_price_per_km("9.00")

    second = calculate_price([select(general, 2)], Decimal("10"))
    assert second == Decimal("370.00")  # (90 × 2) + 100 + 90

    # الكائن الأول لم يتأثر إطلاقًا
    assert first == Decimal("180.00")


@pytest.mark.django_db
def test_base_price_change_is_reflected(general):
    set_price_per_km("0.00")

    before = calculate_price([select(general, 1)], Decimal("0"))

    general.base_price = Decimal("120.00")
    general.save()

    after = calculate_price([select(general, 1)], Decimal("0"))

    assert after - before == Decimal("40.00")  # 120 - 80


@pytest.mark.django_db
def test_price_per_km_change_is_reflected(general):
    set_price_per_km("1.00")
    before = calculate_price([select(general, 1)], Decimal("20"))

    set_price_per_km("3.00")
    after = calculate_price([select(general, 1)], Decimal("20"))

    assert after - before == Decimal("40.00")  # (3 - 1) × 20


@pytest.mark.django_db
def test_repeated_calls_with_unchanged_catalog_are_stable(general, carpet):
    """بلا تعديل، الدالة حتمية (deterministic)."""
    set_price_per_km("2.00")
    basket = [select(general, 2), select(carpet, 3)]

    results = {calculate_price(basket, Decimal("7")) for _ in range(5)}

    assert len(results) == 1


@pytest.mark.django_db
def test_function_stores_nothing(general):
    """
    الدالة لا تكتب شيئًا: لا صف جديد ولا تعديل على الكتالوج.
    """
    set_price_per_km("2.00")
    before_services = ServiceType.objects.count()
    snapshot = (general.room_price, general.base_price)

    calculate_price([select(general, 3)], Decimal("12"))

    assert ServiceType.objects.count() == before_services
    general.refresh_from_db()
    assert (general.room_price, general.base_price) == snapshot


# ============================================================
# 8) الحياد عن نطاقات أخرى
# ============================================================
def test_pricing_module_knows_nothing_about_bookings_or_actors():
    """
    الدالة تأخذ مدخلات أوّلية فقط.

    الفحص على الكود المنفَّذ لا على النص الكامل: ذكر Booking في docstring
    هو توثيق لحدّ المسؤولية (وهو مطلوب)، بينما استيراده أو استخدامه في
    الكود تسرّب فعلي من نطاق لم يُبنَ بعد. نُحلّل الشجرة النحوية (AST)
    لنفصل الاثنين بدل مطابقة النص.
    """
    import ast
    import inspect

    from apps.services.services import pricing as mod

    tree = ast.parse(inspect.getsource(mod))

    # 1) لا استيراد من نطاق آخر
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    for module in imported:
        assert "booking" not in module.lower(), f"leaked import: {module}"
        assert "accounts" not in module.lower(), f"leaked import: {module}"
        assert "dispatch" not in module.lower(), f"leaked import: {module}"

    # 2) لا أسماء من نطاق آخر في الكود المنفَّذ (الـdocstrings مستثناة)
    identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    identifiers |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

    for foreign in ("Booking", "Customer", "Contractor", "Dispatch", "user"):
        assert foreign not in identifiers, f"pricing.py must not reference {foreign}"


def test_calculate_price_signature_matches_the_spec():
    import inspect

    sig = inspect.signature(calculate_price)

    assert list(sig.parameters) == ["service_selections", "distance_km"]


def test_pricing_does_not_require_a_user():
    """
    التسعير ليس عملية إدارية: لا تمر عبر بوابة ADMIN، لأنها ستُستدعى
    لاحقًا في مسارات العميل.
    """
    import inspect

    from apps.services.services import pricing as mod

    src = inspect.getsource(mod)

    assert "assert_is_admin" not in src
    assert "ConfirmedRole" not in src
