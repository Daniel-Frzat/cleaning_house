"""
إدارة حسابات المستخدمين من لوحة التحكم — Back-office (ADMIN).

يغطي: القائمة والتفاصيل، وإيقاف الحساب وإعادة تفعيله.

🔒 حسابات الأدمن خارج هذا الملف كليًا: إدارتها للـSuperuser وحده عبر
   مسار منفصل. أي محاولة لإيقاف أدمن من هنا تُرفض (AdminAccountError).

🔒 الإيقاف يُبطل كل refresh tokens للحساب (القائمة السوداء)، ويجعل
   ملف المقاول UNAVAILABLE. الـaccess tokens القائمة تُرفض فورًا لأن
   ActiveUserJWTAuth يقرأ status من قاعدة البيانات في كل طلب.
"""

import logging

from django.db import transaction
from django.db.models import Count, Q

from apps.audit.services.audit import record
from apps.audit.services.backoffice import assert_admin, filter_date_range

from ..models import User, UserStatus

logger = logging.getLogger(__name__)


class UserAdminError(Exception):
    code = "user_admin_error"


class UserNotFoundError(UserAdminError):
    code = "user_not_found"


class AdminAccountError(UserAdminError):
    """الهدف حساب أدمن — يُدار من مسار الـSuperuser وحده."""

    code = "admin_account"


class InvalidUserStatusTransitionError(UserAdminError):
    code = "invalid_status_transition"


def list_users(
    actor,
    role=None,
    status=None,
    is_contractor=None,
    q=None,
    joined_from=None,
    joined_to=None,
):
    """كل الحسابات — الأحدث انضمامًا أولًا."""
    assert_admin(actor)

    qs = User.objects.select_related("contractor_profile").order_by("-date_joined")
    if role:
        qs = qs.filter(role=role)
    if status:
        qs = qs.filter(status=status)
    if is_contractor is not None:
        qs = qs.filter(is_contractor=is_contractor)
    if q:
        term = q.strip()
        qs = qs.filter(
            Q(phone__icontains=term) | Q(email__icontains=term) | Q(full_name__icontains=term)
        )
    return filter_date_range(qs, "date_joined", joined_from, joined_to)


def get_user(actor, user_id):
    """حساب واحد مع عدّادات الحجوزات والعقارات."""
    assert_admin(actor)

    user = (
        User.objects.select_related("contractor_profile")
        .prefetch_related("social_accounts")
        .annotate(
            bookings_count=Count("bookings", distinct=True),
            properties_count=Count("properties", distinct=True),
        )
        .filter(pk=user_id)
        .first()
    )
    if user is None:
        raise UserNotFoundError("User not found.")
    return user


def summary_counts():
    """
    إجمالي العملاء والعمال — بنفس تعريف has_customer_access/has_contractor_access.

    📌 الحساب ثنائي الصفة (زبون + عامل) يُعدّ في الاثنين.
    """
    from ..roles import ConfirmedRole

    return User.objects.aggregate(
        customers=Count("id", filter=Q(role=ConfirmedRole.CUSTOMER)),
        contractors=Count(
            "id",
            filter=(Q(role=ConfirmedRole.CONTRACTOR) | Q(is_contractor=True))
            & ~Q(role=ConfirmedRole.ADMIN),
        ),
    )


def contractor_status_of(user):
    from apps.contractors.services.verification import get_contractor_status

    return get_contractor_status(user)


def _lock_target(user_id):
    user = User.objects.select_for_update().filter(pk=user_id).first()
    if user is None:
        raise UserNotFoundError("User not found.")
    if user.has_admin_access():
        raise AdminAccountError(
            "Administrator accounts are managed by a superuser, not here."
        )
    return user


def revoke_all_refresh_tokens(user):
    """يضع كل refresh token سارٍ للحساب في القائمة السوداء (services/sessions.py)."""
    from .sessions import revoke_refresh_tokens

    return revoke_refresh_tokens(user)


@transaction.atomic
def suspend_user(actor, user_id, reason, request=None):
    """
    يوقف حسابًا (status=SUSPENDED).

    ⚠️ لا يلغي حجوزات ولا يسحب عروضًا: أثر الإيقاف على العمل الجاري قرار
       منتج غير محسوم (الإلغاء بند مفتوح #12). الإيقاف يمنع الدخول وحده،
       ويُخرج المقاول من الترشيح بجعل ملفه UNAVAILABLE.
    """
    assert_admin(actor)
    user = _lock_target(user_id)

    if user.status == UserStatus.SUSPENDED:
        raise InvalidUserStatusTransitionError("This account is already suspended.")

    previous = user.status
    user.status = UserStatus.SUSPENDED
    user.save(update_fields=["status", "updated_at"])

    revoked = revoke_all_refresh_tokens(user)

    profile_made_unavailable = False
    profile = getattr(user, "contractor_profile", None)
    if profile is not None:
        from apps.contractors.models import AvailabilityStatus

        if profile.availability_status != AvailabilityStatus.UNAVAILABLE:
            profile.availability_status = AvailabilityStatus.UNAVAILABLE
            profile.save(update_fields=["availability_status", "updated_at"])
            profile_made_unavailable = True

    record(
        actor,
        "user.suspend",
        target=user,
        details={
            "from": previous,
            "to": UserStatus.SUSPENDED,
            "reason": reason,
            "refresh_tokens_revoked": revoked,
            "contractor_profile_made_unavailable": profile_made_unavailable,
        },
        request=request,
    )
    logger.info("User suspended (user_id=%s, by=%s)", user.id, actor.id)
    return user


@transaction.atomic
def reactivate_user(actor, user_id, request=None):
    """
    يعيد حسابًا موقوفًا/معطّلًا إلى ACTIVE.

    ⚠️ لا يعيد ملف المقاول AVAILABLE: الإتاحة فعل يخص المقاول وحده.
    """
    assert_admin(actor)
    user = _lock_target(user_id)

    if user.status == UserStatus.ACTIVE:
        raise InvalidUserStatusTransitionError("This account is already active.")

    previous = user.status
    user.status = UserStatus.ACTIVE
    user.save(update_fields=["status", "updated_at"])

    record(
        actor,
        "user.reactivate",
        target=user,
        details={"from": previous, "to": UserStatus.ACTIVE},
        request=request,
    )
    logger.info("User reactivated (user_id=%s, by=%s)", user.id, actor.id)
    return user
