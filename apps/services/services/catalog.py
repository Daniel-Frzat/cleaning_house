"""
Service Catalog Service — Services Domain (Change Set §36.2، §5)

كل وصول إلى الكتالوج والتسعير يمر من هنا. طبقة الـAPI لا تستدعي
`.objects` مباشرة — نفس النمط المعتمد في apps/properties.

🔒 بوابة الدور (ADMIN فقط) مُنفَّذة هنا أيضًا وليس في الـAPI فقط:
   الصلاحية تُفحص عند كل عملية على الكيان نفسه، حتى لا يعتمد الإنفاذ
   على أن الواجهة استدعت الفحص الصحيح.

⚠️ لا يوجد هنا أي حساب سعر ولا إعادة حساب رجعي. تعديل السعر يغيّر القيمة
   الحيّة فقط — الحجوزات السابقة شأن Booking Domain لاحقًا.
"""

import logging

from django.db import transaction

from apps.accounts.roles import ConfirmedRole
from apps.audit.services.audit import record

from ..models import PricingConfig, ServiceType

logger = logging.getLogger(__name__)


def _audit_value(value):
    """قيم JSON آمنة لسجل التدقيق — Decimal نصًا لا float (لا فقد دقة)."""
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    return str(value)


class CatalogError(Exception):
    """أصل أخطاء نطاق الكتالوج."""

    code = "catalog_error"


class CatalogPermissionError(CatalogError):
    """الدور لا يملك صلاحية إدارة الكتالوج."""

    code = "admin_role_required"


class ServiceTypeNotFoundError(CatalogError):
    code = "service_not_found"


# الحقول المسموح تعديلها عبر طبقة الخدمة — قائمة بيضاء صريحة.
# الأسعار ضمنها عمدًا: قابلة للتعديل في أي وقت (§36.2).
UPDATABLE_FIELDS = {
    "name",
    "description",
    "room_price",
    "base_price",
    "is_active",
}


# ------------------------------------------------------------
# فحوص الصلاحية
# ------------------------------------------------------------
def assert_is_admin(user):
    """
    إدارة كتالوج الخدمات والتسعير صلاحية ADMIN حصرًا.

    CUSTOMER و CONTRACTOR لا يريان هذا الكتالوج إطلاقًا في هذه المرحلة —
    ولا حتى للقراءة، لأن الأسعار الخام غير مكشوفة بعد.
    """
    if user is None or not user.is_authenticated:
        raise CatalogPermissionError("Authentication required.")
    if not user.has_admin_access():
        logger.warning(
            "Catalog access denied (user_id=%s, role=%s)", user.id, user.role
        )
        raise CatalogPermissionError("Only admins can manage the service catalog.")


# ------------------------------------------------------------
# ServiceType — عمليات
# ------------------------------------------------------------
@transaction.atomic
def create_service_type(
    user, name, room_price, base_price, description="", is_active=True, request=None
):
    """ينشئ نوع خدمة جديدًا في الكتالوج."""
    assert_is_admin(user)

    service = ServiceType(
        name=name,
        description=description or "",
        room_price=room_price,
        base_price=base_price,
        is_active=is_active,
    )
    service.full_clean()
    service.save()

    record(
        user,
        "service_type.create",
        target=service,
        details={
            "name": service.name,
            "room_price": _audit_value(service.room_price),
            "base_price": _audit_value(service.base_price),
            "is_active": service.is_active,
        },
        request=request,
    )

    logger.info(
        "ServiceType created (service_id=%s, name=%s, by=%s)", service.id, name, user.id
    )
    return service


def get_service_type(user, service_id):
    """يعيد نوع خدمة واحدًا (نشطًا كان أو معطّلًا)."""
    assert_is_admin(user)

    service = ServiceType.objects.filter(pk=service_id).first()
    if service is None:
        raise ServiceTypeNotFoundError("Service type not found.")
    return service


def list_service_types(user):
    """
    يعيد كل أنواع الخدمات — النشطة والمعطّلة معًا.

    الإدارة تحتاج رؤية المعطّل أيضًا لإعادة تفعيله، لذلك لا ترشيح هنا
    (بخلاف قائمة العقارات الموجّهة للعميل).
    """
    assert_is_admin(user)
    return ServiceType.objects.all()


@transaction.atomic
def update_service_type(user, service_id, request=None, **fields):
    """
    يعدّل حقول نوع خدمة.

    ⚠️ room_price و base_price ضمن الحقول المسموحة عمدًا — التعديل متاح في
       أي وقت بلا قيود (§36.2). لا إعادة حساب لأي حجز سابق هنا.
    """
    service = get_service_type(user, service_id)

    changes = {}
    for key, value in fields.items():
        if key not in UPDATABLE_FIELDS:
            raise CatalogError(f"Field '{key}' cannot be updated here.")
        before = getattr(service, key)
        setattr(service, key, value)
        if before != value:
            changes[key] = {"from": _audit_value(before), "to": _audit_value(value)}

    service.full_clean()
    service.save()

    record(
        user,
        "service_type.update",
        target=service,
        details={"changes": changes},
        request=request,
    )

    logger.info(
        "ServiceType updated (service_id=%s, fields=%s, by=%s)",
        service.id,
        sorted(fields),
        user.id,
    )
    return service


@transaction.atomic
def deactivate_service_type(user, service_id, request=None):
    """
    تعطيل ناعم (is_active=False) — لا حذف فعلي.

    الصف يبقى لأن حجوزات لاحقة قد تشير إلى هذه الخدمة.
    """
    service = get_service_type(user, service_id)

    was_active = service.is_active
    service.is_active = False
    service.save(update_fields=["is_active", "updated_at"])

    record(
        user,
        "service_type.deactivate",
        target=service,
        details={"was_active": was_active},
        request=request,
    )

    logger.info("ServiceType deactivated (service_id=%s, by=%s)", service.id, user.id)
    return service


# ============================================================
# القراءة العامة — أي مستخدم مصادَق عليه (§36.2)
# ============================================================
# 🔒 مسار منفصل تمامًا عن الدوال الإدارية أعلاه، ولو بدا مشابهًا:
#    تلك تفرض ADMIN وتعيد الصفوف كاملة بحقول التسعير. إعادة استخدامها
#    هنا كانت ستربط العرض العام بدالة مصمَّمة لكشف كل شيء، فيكفي تعديل
#    ترشيح واحد فيها مستقبلًا ليتسرّب السعر من هذا الباب.
#
# ⚠️ الترشيح is_active=True ليس تفصيلًا تجميليًا: الخدمة المعطّلة مسحوبة
#    من التداول، ولا يجوز أن يراها العميل ولا أن يبني عليها حجزًا
#    (طبقة الحجز ترفضها بـ400 أصلًا).
#
# ⚠️ حجب التسعير مسؤولية الـschema العام (ServicePublicOut) لا هذه الدوال:
#    هي تعيد كائنات ServiceType كاملة، والطبقة المُسلسِلة تختار الحقول.
#    لذلك لا تُمرَّر نتائجها إلى أي schema إداري.


def list_active_service_types():
    """
    يعيد الخدمات النشطة وحدها — للعرض العام.

    لا فحص دور: القراءة متاحة لأي مستخدم مصادَق عليه، والمصادقة نفسها
    تفرضها طبقة الـAPI عبر JWTAuth. الكتالوج النشط ليس سرًّا — السعر هو
    السرّ، وهو محجوب بالـschema.
    """
    return ServiceType.objects.filter(is_active=True)


def get_active_service_type(service_id):
    """
    يعيد خدمة نشطة واحدة بمعرّفها.

    🔒 المعطّلة وغير الموجودة تعطيان ServiceTypeNotFoundError نفسه: الرد
       لا يميّز بينهما، فلا يكشف أن خدمة ما كانت موجودة ثم سُحبت.
    """
    service = ServiceType.objects.filter(pk=service_id, is_active=True).first()
    if service is None:
        raise ServiceTypeNotFoundError("Service type not found.")
    return service


# ------------------------------------------------------------
# PricingConfig — عمليات
# ------------------------------------------------------------
def get_pricing_config(user):
    """
    يعيد الـsingleton، وينشئه بقيمه الافتراضية إن لم يكن موجودًا بعد.

    لا يحتاج migration لبذر الصف: أول قراءة تنشئه.
    """
    assert_is_admin(user)

    config, _ = PricingConfig.objects.get_or_create(pk=PricingConfig.SINGLETON_PK)
    return config


@transaction.atomic
def update_pricing_config(user, price_per_km, request=None):
    """يحدّث سعر الكيلومتر العام — قيمة واحدة للنظام كله."""
    config = get_pricing_config(user)

    before = config.price_per_km
    config.price_per_km = price_per_km
    config.full_clean()
    config.save()

    record(
        user,
        "pricing_config.update",
        target=config,
        details={
            "price_per_km": {"from": _audit_value(before), "to": _audit_value(price_per_km)}
        },
        request=request,
    )

    logger.info(
        "PricingConfig updated (price_per_km=%s, by=%s)", config.price_per_km, user.id
    )
    return config
