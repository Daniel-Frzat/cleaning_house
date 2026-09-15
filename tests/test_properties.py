"""
Properties & Address Domain Tests (Phase 1)

يغطي: إلزامية المالك، تحقق الولاية/الرمز البريدي، وفحص الملكية على مستوى
الكائن في طبقة الخدمة.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.properties.models import (
    AustralianState,
    Property,
    PropertyAddress,
    PropertyType,
)
from apps.properties.services import properties as svc


@pytest.fixture
def customer(db):
    return User.objects.create_user(phone="+61400000001", role=ConfirmedRole.CUSTOMER)


@pytest.fixture
def other_customer(db):
    return User.objects.create_user(phone="+61400000002", role=ConfirmedRole.CUSTOMER)


VALID_ADDRESS = {
    "street_address": "12 Example St",
    "suburb": "Bondi",
    "state": AustralianState.NSW,
    "postcode": "2026",
}

# رمز بريدي حقيقي لكل ولاية — الرمز والولاية يجب أن يتّسقا (models.clean)
CAPITAL_POSTCODES = {
    "NSW": "2000", "VIC": "3000", "QLD": "4000", "SA": "5000",
    "WA": "6000", "TAS": "7000", "NT": "0800", "ACT": "2600",
}


# ============================================================
# 3) العقار يتطلب مالكًا
# ============================================================
@pytest.mark.django_db
def test_property_requires_owner_at_db_level():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Property.objects.create(owner=None, property_type=PropertyType.HOUSE)


@pytest.mark.django_db
def test_property_requires_owner_at_validation_level():
    prop = Property(property_type=PropertyType.HOUSE)
    with pytest.raises(ValidationError) as exc:
        prop.full_clean()
    assert "owner" in exc.value.error_dict


@pytest.mark.django_db
def test_owner_relation_uses_expected_related_name(customer):
    prop = Property.objects.create(owner=customer, property_type=PropertyType.UNIT)
    assert list(customer.properties.all()) == [prop]


@pytest.mark.django_db
def test_property_defaults(customer):
    prop = Property.objects.create(owner=customer)
    assert prop.property_type == PropertyType.HOUSE
    assert prop.is_active is True
    assert prop.label == ""
    assert prop.created_at is not None and prop.updated_at is not None


# ============================================================
# 4) تحقق العنوان: الولاية والرمز البريدي
# ============================================================
@pytest.mark.django_db
def test_address_accepts_all_valid_australian_states(customer):
    assert set(AustralianState.values) == {"NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"}

    for i, state in enumerate(AustralianState.values):
        prop = Property.objects.create(owner=customer, label=f"P{i}")
        address = PropertyAddress(
            property=prop,
            **{**VALID_ADDRESS, "state": state,
               "postcode": CAPITAL_POSTCODES[state]},
        )
        address.full_clean()  # يجب ألا يرفع
        address.save()
    assert PropertyAddress.objects.count() == 8


@pytest.mark.django_db
def test_invalid_state_raises_validation_error(customer):
    prop = Property.objects.create(owner=customer)
    address = PropertyAddress(property=prop, **{**VALID_ADDRESS, "state": "XYZ"})
    with pytest.raises(ValidationError) as exc:
        address.full_clean()
    assert "state" in exc.value.error_dict


@pytest.mark.django_db
@pytest.mark.parametrize("bad_postcode", ["123", "12345", "abcd", "20a6", "", "2 26"])
def test_invalid_postcode_raises_validation_error(customer, bad_postcode):
    prop = Property.objects.create(owner=customer, label=bad_postcode or "empty")
    address = PropertyAddress(
        property=prop, **{**VALID_ADDRESS, "postcode": bad_postcode}
    )
    with pytest.raises(ValidationError) as exc:
        address.full_clean()
    assert "postcode" in exc.value.error_dict


@pytest.mark.django_db
def test_valid_four_digit_postcode_is_accepted(customer):
    prop = Property.objects.create(owner=customer)
    # 0800 دارون — الصفر البادئ يجب أن يبقى، والولاية تطابقه (NT)
    address = PropertyAddress(
        property=prop,
        **{**VALID_ADDRESS, "state": AustralianState.NT, "postcode": "0800"},
    )
    address.full_clean()
    address.save()
    assert address.postcode == "0800"


@pytest.mark.django_db
def test_country_defaults_to_au_and_is_not_user_editable(customer):
    prop = Property.objects.create(owner=customer)
    address = PropertyAddress.objects.create(property=prop, **VALID_ADDRESS)
    assert address.country == "AU"
    assert PropertyAddress._meta.get_field("country").editable is False


@pytest.mark.django_db
def test_coordinates_and_raw_input_are_nullable(customer):
    """الإحداثيات تُعبَّأ لاحقًا عبر GPS adapter (Phase 2)."""
    prop = Property.objects.create(owner=customer)
    address = PropertyAddress.objects.create(property=prop, **VALID_ADDRESS)
    assert address.latitude is None
    assert address.longitude is None
    assert address.raw_input is None


@pytest.mark.django_db
def test_address_is_separate_entity_one_to_one(customer):
    """PropertyAddress كيان منفصل (Change Set قسم 20) — لا يُدمج في Property."""
    prop = Property.objects.create(owner=customer)
    PropertyAddress.objects.create(property=prop, **VALID_ADDRESS)

    # حقول العنوان ليست على Property
    property_fields = {f.name for f in Property._meta.get_fields()}
    for f in ("street_address", "suburb", "state", "postcode"):
        assert f not in property_fields

    # علاقة واحد-لواحد
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PropertyAddress.objects.create(property=prop, **VALID_ADDRESS)


@pytest.mark.django_db
def test_deleting_property_deletes_address(customer):
    prop = Property.objects.create(owner=customer)
    PropertyAddress.objects.create(property=prop, **VALID_ADDRESS)
    prop.delete()
    assert PropertyAddress.objects.count() == 0


# ============================================================
# 5) فحص الملكية في طبقة الخدمة
# ============================================================
@pytest.mark.django_db
def test_customer_can_create_property_for_themselves(customer):
    prop = svc.create_property(
        customer, property_type=PropertyType.APARTMENT, label="Home", address=VALID_ADDRESS
    )
    assert prop.owner_id == customer.id
    assert prop.label == "Home"
    assert prop.address.suburb == "Bondi"


@pytest.mark.django_db
def test_second_customer_cannot_read_first_customers_property(customer, other_customer):
    prop = svc.create_property(customer, property_type=PropertyType.HOUSE)

    with pytest.raises(svc.PropertyPermissionError):
        svc.get_property(other_customer, prop.id)


@pytest.mark.django_db
def test_second_customer_cannot_edit_first_customers_property(customer, other_customer):
    prop = svc.create_property(customer, property_type=PropertyType.HOUSE, label="Mine")

    with pytest.raises(svc.PropertyPermissionError):
        svc.update_property(other_customer, prop.id, label="Hacked")

    prop.refresh_from_db()
    assert prop.label == "Mine"


@pytest.mark.django_db
def test_second_customer_cannot_edit_address(customer, other_customer):
    prop = svc.create_property(
        customer, property_type=PropertyType.HOUSE, address=VALID_ADDRESS
    )
    with pytest.raises(svc.PropertyPermissionError):
        svc.update_address(other_customer, prop.id, suburb="Elsewhere")

    prop.address.refresh_from_db()
    assert prop.address.suburb == "Bondi"


@pytest.mark.django_db
def test_second_customer_cannot_deactivate_property(customer, other_customer):
    prop = svc.create_property(customer, property_type=PropertyType.HOUSE)
    with pytest.raises(svc.PropertyPermissionError):
        svc.deactivate_property(other_customer, prop.id)
    prop.refresh_from_db()
    assert prop.is_active is True


@pytest.mark.django_db
def test_list_properties_returns_only_own(customer, other_customer):
    svc.create_property(customer, property_type=PropertyType.HOUSE, label="A")
    svc.create_property(customer, property_type=PropertyType.UNIT, label="B")
    svc.create_property(other_customer, property_type=PropertyType.HOUSE, label="C")

    labels = {p.label for p in svc.list_properties(customer)}
    assert labels == {"A", "B"}


@pytest.mark.django_db
def test_owner_can_read_and_edit_own_property(customer):
    prop = svc.create_property(customer, property_type=PropertyType.HOUSE, label="Old")

    fetched = svc.get_property(customer, prop.id)
    assert fetched.id == prop.id

    updated = svc.update_property(customer, prop.id, label="New")
    assert updated.label == "New"


@pytest.mark.django_db
def test_ownership_cannot_be_transferred_via_update(customer, other_customer):
    prop = svc.create_property(customer, property_type=PropertyType.HOUSE)
    svc.update_property(customer, prop.id, owner=other_customer, label="X")
    prop.refresh_from_db()
    assert prop.owner_id == customer.id


@pytest.mark.django_db
def test_non_customer_roles_cannot_own_properties(db):
    contractor = User.objects.create_user(
        phone="+61400000003", role=ConfirmedRole.CONTRACTOR
    )
    with pytest.raises(svc.InvalidOwnerRoleError):
        svc.create_property(contractor, property_type=PropertyType.HOUSE)


@pytest.mark.django_db
def test_missing_property_raises_not_found(customer):
    import uuid

    with pytest.raises(svc.PropertyNotFoundError):
        svc.get_property(customer, uuid.uuid4())


@pytest.mark.django_db
def test_invalid_address_rolls_back_property_creation(customer):
    """العنوان والعقار يُنشآن في معاملة واحدة."""
    with pytest.raises(ValidationError):
        svc.create_property(
            customer,
            property_type=PropertyType.HOUSE,
            address={**VALID_ADDRESS, "postcode": "99"},
        )
    assert Property.objects.count() == 0
    assert PropertyAddress.objects.count() == 0


# ============================================================
# نطاق الخطوة: لا تكاملات ولا كيانات خارج النطاق
# ============================================================
@pytest.mark.django_db
def test_no_out_of_scope_foreign_keys():
    """
    لا FK لـBooking / Job / QualityGuarantee في هذه الخطوة.

    ⚠️ الفحص على الحقول الأمامية (forward) وحدها: القاعدة أن نطاق العقارات
       لا يعرف النطاقات اللاحقة ولا يشير إليها. أمّا إشارة نطاق لاحق إلى
       Property (مثل Booking.property) فتُنشئ accessor عكسيًا تلقائيًا على
       Property، وهو ليس FK يملكه هذا النطاق ولا يخرق حدوده — الاتجاه هو
       المهم، لا مجرد وجود العلاقة.
    """
    for model in (Property, PropertyAddress):
        for field in model._meta.get_fields():
            # العكسي auto_created وغير concrete — نتجاوزه
            if field.auto_created and not field.concrete:
                continue
            related = getattr(field, "related_model", None)
            if related is not None:
                assert related.__name__ not in ("Booking", "Job", "QualityGuarantee")


def test_no_adapter_integration_in_domain_code():
    """لا استدعاء لـaddress_validation أو gps_distance بعد."""
    import inspect

    import apps.properties.models as models_mod
    import apps.properties.services.properties as svc_mod

    for mod in (models_mod, svc_mod):
        src = inspect.getsource(mod)
        for banned in ("address_validation", "gps_distance", "get_address_adapter"):
            # مسموح ذكره في التعليقات فقط، لا كاستيراد
            assert f"import {banned}" not in src
            assert f"from adapters.{banned}" not in src
