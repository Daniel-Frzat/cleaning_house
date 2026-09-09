"""
Properties Service — Properties Domain (Phase 1)

كل الوصول إلى العقارات يمر من هنا. فحص الملكية على مستوى الكائن
(object-level) يعيش في طبقة الخدمة وليس في ترشيح الـviews فقط — بنفس روح
النمط المعتمد لصلاحية Re-clean (Change Set — قسم 12): الصلاحية تُفحص عند
كل عملية على الكيان نفسه، لا بالاعتماد على أن الواجهة أرسلت الاستعلام الصحيح.

⚠️ لا يوجد هنا أي استدعاء لـaddress_validation أو gps_distance adapter.
"""

import logging

from django.db import transaction

from apps.accounts.roles import ConfirmedRole

from ..models import Property, PropertyAddress

logger = logging.getLogger(__name__)


class PropertyError(Exception):
    """أصل أخطاء نطاق العقارات."""

    code = "property_error"


class PropertyPermissionError(PropertyError):
    """المستخدم لا يملك هذا العقار."""

    code = "property_forbidden"


class PropertyNotFoundError(PropertyError):
    code = "property_not_found"


class InvalidOwnerRoleError(PropertyError):
    """الدور غير مسموح له بامتلاك عقار."""

    code = "invalid_owner_role"


# ------------------------------------------------------------
# فحوص الصلاحية
# ------------------------------------------------------------
def assert_can_own_properties(user):
    """
    امتلاك العقارات صلاحية CUSTOMER.

    ملاحظة: PropertyManager = CUSTOMER (Change Set — قسم 12)، فلا حاجة
    لدور إضافي هنا.
    """
    if user is None or not user.is_authenticated:
        raise PropertyPermissionError("Authentication required.")
    if user.role != ConfirmedRole.CUSTOMER:
        raise InvalidOwnerRoleError("Only customers can own properties.")


def assert_owns(user, prop):
    """
    فحص الملكية على مستوى الكائن — نقطة الإنفاذ الوحيدة.

    تُستدعى في كل قراءة/تعديل/حذف، حتى لو كان الاستعلام مُرشَّحًا أصلًا.
    """
    if user is None or not user.is_authenticated:
        raise PropertyPermissionError("Authentication required.")
    if prop.owner_id != user.id:
        # لا نكشف وجود العقار لغير مالكه
        logger.warning(
            "Ownership check failed (user_id=%s, property_id=%s)", user.id, prop.id
        )
        raise PropertyPermissionError("You do not have access to this property.")


# ------------------------------------------------------------
# العمليات
# ------------------------------------------------------------
@transaction.atomic
def create_property(owner, property_type, label="", address=None):
    """
    ينشئ عقارًا للمالك المُمرَّر (وله فقط).

    address (اختياري): dict بحقول PropertyAddress. يُنشأ داخل نفس المعاملة
    حتى لا يبقى عقار بلا عنوان عند فشل التحقق.
    """
    assert_can_own_properties(owner)

    prop = Property(owner=owner, property_type=property_type, label=label or "")
    prop.full_clean()
    prop.save()

    if address:
        create_address(owner, prop, **address)

    logger.info("Property created (property_id=%s, owner_id=%s)", prop.id, owner.id)
    return prop


@transaction.atomic
def create_address(user, prop, **fields):
    """ينشئ عنوان العقار بعد التحقق من الملكية."""
    assert_owns(user, prop)

    # country غير قابل للتعديل من المستخدم — يُتجاهل إن أُرسل
    fields.pop("country", None)

    address = PropertyAddress(property=prop, **fields)
    address.full_clean()
    address.save()
    return address


def get_property(user, property_id):
    """يعيد عقارًا يملكه المستخدم، وإلا يرفع خطأ صلاحية."""
    prop = Property.objects.filter(pk=property_id).select_related("owner").first()
    if prop is None:
        raise PropertyNotFoundError("Property not found.")

    assert_owns(user, prop)
    return prop


def list_properties(user):
    """عقارات المستخدم الحالي فقط."""
    assert_can_own_properties(user)
    return Property.objects.filter(owner=user).select_related("address")


@transaction.atomic
def update_property(user, property_id, **fields):
    """يعدّل عقارًا بعد التحقق من الملكية."""
    prop = get_property(user, property_id)

    # الملكية لا تُنقل عبر هذا المسار
    fields.pop("owner", None)
    fields.pop("owner_id", None)

    allowed = {"label", "property_type", "is_active"}
    for key, value in fields.items():
        if key not in allowed:
            raise PropertyError(f"Field '{key}' cannot be updated here.")
        setattr(prop, key, value)

    prop.full_clean()
    prop.save()
    return prop


@transaction.atomic
def update_address(user, property_id, **fields):
    """يعدّل عنوان عقار بعد التحقق من الملكية."""
    prop = get_property(user, property_id)

    address = getattr(prop, "address", None)
    if address is None:
        raise PropertyNotFoundError("This property has no address yet.")

    fields.pop("country", None)
    for key, value in fields.items():
        setattr(address, key, value)

    address.full_clean()
    address.save()
    return address


@transaction.atomic
def deactivate_property(user, property_id):
    """إلغاء تفعيل ناعم — لا حذف فعلي في هذه المرحلة."""
    prop = get_property(user, property_id)
    prop.is_active = False
    prop.save(update_fields=["is_active", "updated_at"])
    return prop
