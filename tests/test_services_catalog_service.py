"""
Service Catalog — Service Layer & Model Tests (Change Set §36.2، §5)

يتحقق من أن الإنفاذ يعيش في طبقة الخدمة نفسها (لا في الـAPI فقط)، ومن
قيود الـModel، ومن أن طبقة الـAPI لا تتجاوز طبقة الخدمة.
"""

import inspect
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.services.models import PricingConfig, ServiceType
from apps.services.services import catalog as svc


def make_user(phone, role):
    return User.objects.create_user(phone=phone, role=role)


@pytest.fixture
def admin_user(db):
    return make_user("+61400003001", ConfirmedRole.ADMIN)


@pytest.fixture
def customer(db):
    return make_user("+61400003002", ConfirmedRole.CUSTOMER)


@pytest.fixture
def contractor(db):
    return make_user("+61400003003", ConfirmedRole.CONTRACTOR)


def make_service(user, name="General Cleaning", **overrides):
    fields = {"room_price": Decimal("45.00"), "base_price": Decimal("80.00")}
    fields.update(overrides)
    return svc.create_service_type(user, name=name, **fields)


# ============================================================
# 1) بوابة الدور في طبقة الخدمة نفسها
# ============================================================
@pytest.mark.django_db
@pytest.mark.parametrize("role", [ConfirmedRole.CUSTOMER, ConfirmedRole.CONTRACTOR])
def test_non_admin_rejected_by_service_layer(db, role):
    """
    الإنفاذ لا يعتمد على طبقة الـAPI — استدعاء الخدمة مباشرة يُرفض أيضًا.
    """
    user = make_user("+61400003100", role)

    with pytest.raises(svc.CatalogPermissionError):
        svc.create_service_type(
            user, name="X", room_price=Decimal("1"), base_price=Decimal("1")
        )
    with pytest.raises(svc.CatalogPermissionError):
        svc.list_service_types(user)
    with pytest.raises(svc.CatalogPermissionError):
        svc.get_pricing_config(user)
    with pytest.raises(svc.CatalogPermissionError):
        svc.update_pricing_config(user, Decimal("5"))

    assert ServiceType.objects.count() == 0


@pytest.mark.django_db
def test_anonymous_user_rejected_by_service_layer(db):
    from django.contrib.auth.models import AnonymousUser

    with pytest.raises(svc.CatalogPermissionError):
        svc.list_service_types(AnonymousUser())
    with pytest.raises(svc.CatalogPermissionError):
        svc.get_pricing_config(AnonymousUser())


@pytest.mark.django_db
def test_none_user_rejected_by_service_layer(db):
    with pytest.raises(svc.CatalogPermissionError):
        svc.list_service_types(None)


@pytest.mark.django_db
def test_permission_error_carries_stable_code(db, customer):
    with pytest.raises(svc.CatalogPermissionError) as exc:
        svc.list_service_types(customer)
    assert exc.value.code == "admin_role_required"


# ============================================================
# 2) عمليات ServiceType
# ============================================================
@pytest.mark.django_db
def test_admin_can_create_and_read(admin_user):
    service = make_service(admin_user)

    assert service.is_active is True
    assert svc.get_service_type(admin_user, service.id) == service
    assert list(svc.list_service_types(admin_user)) == [service]


@pytest.mark.django_db
def test_get_unknown_id_raises_not_found(admin_user):
    import uuid

    with pytest.raises(svc.ServiceTypeNotFoundError):
        svc.get_service_type(admin_user, uuid.uuid4())


@pytest.mark.django_db
def test_update_allows_price_fields(admin_user):
    """§36.2: الأسعار ضمن الحقول المسموحة صراحةً."""
    service = make_service(admin_user)

    updated = svc.update_service_type(
        admin_user, service.id, room_price=Decimal("50.00"), base_price=Decimal("90.00")
    )

    assert updated.room_price == Decimal("50.00")
    assert updated.base_price == Decimal("90.00")


@pytest.mark.django_db
def test_update_rejects_unknown_field(admin_user):
    service = make_service(admin_user)

    with pytest.raises(svc.CatalogError):
        svc.update_service_type(admin_user, service.id, price_per_km=Decimal("3"))


@pytest.mark.django_db
def test_update_rejects_id_tampering(admin_user):
    service = make_service(admin_user)

    with pytest.raises(svc.CatalogError):
        svc.update_service_type(admin_user, service.id, id="something-else")


@pytest.mark.django_db
def test_deactivate_is_soft(admin_user):
    service = make_service(admin_user)

    svc.deactivate_service_type(admin_user, service.id)

    service.refresh_from_db()
    assert service.is_active is False
    assert ServiceType.objects.filter(pk=service.id).exists()


@pytest.mark.django_db
def test_list_includes_inactive(admin_user):
    active = make_service(admin_user, name="Active")
    inactive = make_service(admin_user, name="Inactive")
    svc.deactivate_service_type(admin_user, inactive.id)

    ids = {s.id for s in svc.list_service_types(admin_user)}
    assert ids == {active.id, inactive.id}


# ============================================================
# 3) قيود الـModel
# ============================================================
@pytest.mark.django_db
def test_negative_price_rejected_at_model_level(admin_user):
    with pytest.raises(ValidationError):
        svc.create_service_type(
            admin_user, name="Bad", room_price=Decimal("-1"), base_price=Decimal("10")
        )


@pytest.mark.django_db
def test_name_uniqueness_enforced_at_db_level(admin_user):
    make_service(admin_user, name="Carpet Cleaning")

    # full_clean يلتقطها كـValidationError قبل وصولها لقيد قاعدة البيانات
    with pytest.raises((ValidationError, IntegrityError)):
        with transaction.atomic():
            make_service(admin_user, name="Carpet Cleaning")

    assert ServiceType.objects.filter(name="Carpet Cleaning").count() == 1


@pytest.mark.django_db
def test_service_type_has_no_price_per_km_field(db):
    """§5: سعر الكيلومتر عام — لا يعيش على الخدمة."""
    names = {f.name for f in ServiceType._meta.get_fields()}
    assert "price_per_km" not in names
    assert {"room_price", "base_price"} <= names


@pytest.mark.django_db
def test_str_uses_name(admin_user):
    service = make_service(admin_user, name="Garden Cleaning")
    assert str(service) == "Garden Cleaning"


# ============================================================
# 4) PricingConfig — singleton
# ============================================================
@pytest.mark.django_db
def test_pricing_config_created_with_zero_default(admin_user):
    config = svc.get_pricing_config(admin_user)

    assert config.pk == PricingConfig.SINGLETON_PK
    assert config.price_per_km == Decimal("0.00")


@pytest.mark.django_db
def test_pricing_config_repeated_reads_do_not_duplicate(admin_user):
    svc.get_pricing_config(admin_user)
    svc.get_pricing_config(admin_user)
    svc.get_pricing_config(admin_user)

    assert PricingConfig.objects.count() == 1


@pytest.mark.django_db
def test_saving_with_forced_pk_still_yields_one_row(db):
    """محاولة إنشاء صف ثانٍ بمفتاح مختلف تُعاد إلى الـsingleton."""
    PricingConfig(pk=99, price_per_km=Decimal("7.00")).save()

    assert PricingConfig.objects.count() == 1
    assert PricingConfig.objects.get().pk == PricingConfig.SINGLETON_PK


@pytest.mark.django_db
def test_pricing_config_cannot_be_deleted(admin_user):
    config = svc.get_pricing_config(admin_user)

    with pytest.raises(NotImplementedError):
        config.delete()

    assert PricingConfig.objects.count() == 1


@pytest.mark.django_db
def test_update_pricing_config_persists(admin_user):
    svc.update_pricing_config(admin_user, Decimal("3.25"))

    assert PricingConfig.objects.get().price_per_km == Decimal("3.25")


@pytest.mark.django_db
def test_negative_price_per_km_rejected(admin_user):
    with pytest.raises(ValidationError):
        svc.update_pricing_config(admin_user, Decimal("-0.01"))


# ============================================================
# 5) الالتزام بالطبقات
# ============================================================
def test_api_layer_does_not_touch_orm_directly():
    """
    كل الطفرات تمر عبر طبقة الخدمة — لا استدعاءات .objects في طبقة الـAPI
    (نفس قاعدة apps/properties).
    """
    import apps.services.api.catalog as api_mod

    src = inspect.getsource(api_mod)

    assert ".objects." not in src
    assert "ServiceType(" not in src
    assert "PricingConfig(" not in src

    for fn in (
        "svc.create_service_type",
        "svc.get_service_type",
        "svc.list_service_types",
        "svc.update_service_type",
        "svc.deactivate_service_type",
        "svc.get_pricing_config",
        "svc.update_pricing_config",
    ):
        assert fn in src, f"{fn} not used by the API layer"


def test_api_layer_does_not_reimplement_the_role_check():
    """بوابة الدور تُستورد من طبقة الخدمة ولا تُكرَّر في الـAPI."""
    import apps.services.api.catalog as api_mod

    src = inspect.getsource(api_mod)

    assert "ConfirmedRole" not in src
    assert "role ==" not in src
    assert "role !=" not in src


def test_router_is_importable_from_the_api_package():
    """المسار المختصر المذكور في المواصفة يعمل أيضًا."""
    from apps.services.api import router

    assert router is not None
