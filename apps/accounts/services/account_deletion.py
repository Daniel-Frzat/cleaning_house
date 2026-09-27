"""
حذف الحساب بطلب صاحبه (قرار PO — 2026-09-27).

App Store و Google Play يرفضان تطبيقًا فيه تسجيل دخول بلا حذف للحساب،
فالحذف متاح لكل مستخدم للتطبيق (عميل أو مقاول) بعد خطوة تأكيد.

📌 تجهيل لا حذف صفوف: الحجوزات والدفعات والتحويلات وسجل التدقيق سجلات
   مالية/تشغيلية يجب أن تبقى، ومعظمها PROTECT على المستخدم أصلًا. ما يُمحى
   هو ما يعرّف الشخص: الاسم والهاتف والبريد، وحسابات الدخول الاجتماعي،
   والأجهزة والإشعارات ورموز OTP. الهاتف يتحرر فيمكن التسجيل به من جديد
   كحساب جديد تمامًا.

🔒 لا حذف وعمل جارٍ: حجز مؤكَّد لم يكتمل، أو دفعة قيد المعالجة، أو مهمة
   مُسنَدة للمقاول، أو تحويل أرباح معلّق — كلها ترفض الحذف حتى تنتهي،
   لأن طرفًا آخر (عامل أو عميل أو مال) ينتظرها. الطلبات غير المدفوعة
   تُلغى تلقائيًا، وعروض المقاول المفتوحة تُرفض فينتقل الحجز لغيره.
"""

import logging
import uuid

from django.db import transaction
from django.utils import timezone

from ..models import OTPVerification, User, UserStatus

logger = logging.getLogger(__name__)

CONFIRMATION_PHRASE = "DELETE"
DELETION_REASON = "Customer deleted their account."


class AccountDeletionError(Exception):
    code = "account_deletion_error"


class DeletionConfirmationRequiredError(AccountDeletionError):
    """The request did not carry the confirmation phrase."""

    code = "deletion_confirmation_required"


class AccountDeletionBlockedError(AccountDeletionError):
    """Unfinished bookings, jobs, payments or payouts block deletion until they end."""

    code = "account_deletion_blocked"

    def __init__(self, blockers):
        self.blockers = blockers
        super().__init__(
            "The account has unfinished activity: "
            + ", ".join(b["reason"] for b in blockers)
            + ". It can be deleted once that is finished, or contact support."
        )


class AdminAccountDeletionError(AccountDeletionError):
    """Administrator accounts are managed by a superuser, not deleted from the app."""

    code = "admin_account_not_deletable"


# ------------------------------------------------------------
# ما يمنع الحذف
# ------------------------------------------------------------
def deletion_blockers(user):
    """
    [{reason, booking_id?}] — فارغة تعني أن الحذف ممكن الآن.

    📌 نفس الدالة لشاشة التأكيد (GET) وللحذف نفسه، فلا يختلف ما يُعرض عمّا
       يُفرض.
    """
    from apps.bookings.models import BookingStatus, DispatchOfferStatus
    from apps.jobs.models import JobStatus
    from apps.payments.models import PaymentStatus
    from apps.payouts.models import PayoutStatus

    blockers = []

    # العميل: حجز مؤكَّد لم تكتمل مهمته — عاملٌ في الطريق أو يعمل
    for booking in user.bookings.filter(status=BookingStatus.CONFIRMED).select_related("job"):
        job = getattr(booking, "job", None)
        if job is None or job.status != JobStatus.COMPLETED:
            blockers.append({"reason": "active_booking", "booking_id": booking.id})

    # العميل: طلب لم يُسند لكن دفعته قيد المعالجة أو تنتظر 3-D Secure أو نجحت
    charged = (PaymentStatus.PENDING, PaymentStatus.PROCESSING, PaymentStatus.REQUIRES_ACTION, PaymentStatus.SUCCEEDED)
    for booking in user.bookings.filter(status=BookingStatus.PENDING, payment__status__in=charged):
        blockers.append({"reason": "payment_in_progress", "booking_id": booking.id})

    profile = getattr(user, "contractor_profile", None)
    if profile is not None:
        # المقاول: قَبِل وينتظر دفع العميل، أو مهمة مُسنَدة لم تكتمل
        reserved = profile.dispatch_offers.filter(status=DispatchOfferStatus.ACCEPTED_PENDING_PAYMENT)
        for offer in reserved:
            blockers.append({"reason": "active_job", "booking_id": offer.booking_id})
        for booking in profile.assigned_bookings.filter(status=BookingStatus.CONFIRMED).select_related("job"):
            job = getattr(booking, "job", None)
            if job is None or job.status != JobStatus.COMPLETED:
                blockers.append({"reason": "active_job", "booking_id": booking.id})

    # المقاول: أرباح لم تُحوَّل بعد
    for payout in user.payouts.filter(status=PayoutStatus.PENDING):
        blockers.append({"reason": "payout_pending", "booking_id": payout.booking_id})

    return blockers


def _assert_app_user(user):
    if user.has_admin_access():
        raise AdminAccountDeletionError(
            "Administrator accounts are managed by a superuser and cannot be deleted here."
        )


def get_deletion_status(user):
    _assert_app_user(user)
    blockers = deletion_blockers(user)
    return {"can_delete": not blockers, "blockers": blockers}


# ------------------------------------------------------------
# الحذف
# ------------------------------------------------------------
@transaction.atomic
def delete_account(user, confirmation, request=None):
    """
    يجهّل الحساب ويغلقه نهائيًا. لا تراجع.

    الترتيب: الفحوص كلها أولًا (لا أثر جزئي عند الرفض)، ثم إغلاق العمل
    المفتوح بخدماته المعتادة (إلغاء، رفض عرض — بإشعاراتها وتدقيقها)، ثم
    محو الهوية وإنهاء الجلسات.
    """
    from apps.audit.models import AuditLog
    from apps.audit.services.audit import record
    from apps.bookings.models import BookingStatus, DispatchOfferStatus
    from apps.bookings.services import bookings as bookings_svc
    from apps.bookings.services import offers as offers_svc
    from apps.contractors.models import AvailabilityStatus

    from .sessions import revoke_all_sessions

    _assert_app_user(user)
    if (confirmation or "").strip() != CONFIRMATION_PHRASE:
        raise DeletionConfirmationRequiredError(
            f'Send "confirmation": "{CONFIRMATION_PHRASE}" to delete the account.'
        )

    user = User.objects.select_for_update().get(pk=user.pk)
    blockers = deletion_blockers(user)
    if blockers:
        raise AccountDeletionBlockedError(blockers)

    # 1) الطلبات غير المدفوعة — إلغاء عادي (يغلق عروضها ويبلّغ المقاول)
    for booking_id in list(user.bookings.filter(status=BookingStatus.PENDING).values_list("id", flat=True)):
        bookings_svc.cancel_booking(user, booking_id, reason=DELETION_REASON)

    # 2) المقاول: يخرج من التوزيع، وعروضه المفتوحة تُرفض فتنتقل لغيره
    profile = getattr(user, "contractor_profile", None)
    if profile is not None:
        if profile.availability_status != AvailabilityStatus.UNAVAILABLE:
            profile.availability_status = AvailabilityStatus.UNAVAILABLE
            profile.save(update_fields=["availability_status", "updated_at"])
        for offer_id in list(
            profile.dispatch_offers.filter(status=DispatchOfferStatus.PENDING).values_list("id", flat=True)
        ):
            offers_svc.decline_offer(user, offer_id)

    # 3) العقارات تخرج من الاستعمال — تبقى لأن الحجوزات السابقة تشير إليها
    user.properties.filter(is_active=True).update(is_active=False)

    # 4) ما يعرّف الشخص أو يوصل إليه
    old_phone = user.phone
    user.social_accounts.all().delete()
    user.device_tokens.all().delete()
    user.notifications.all().delete()
    OTPVerification.objects.filter(phone=old_phone).delete()
    revoke_all_sessions(user)

    anonymous = f"deleted:{uuid.uuid4().hex[:24]}"
    user.phone = anonymous
    user.email = None
    user.email_verified = False
    user.full_name = ""
    user.set_unusable_password()
    user.is_active = False
    user.status = UserStatus.DELETED
    user.deleted_at = timezone.now()
    user.save()

    # سجل التدقيق يحفظ اسم المنفّذ نصًا — لا يبقى فيه هاتفه أو بريده
    AuditLog.objects.filter(actor=user).update(actor_label=anonymous)
    record(user, "account.deleted", target=user, request=request)

    logger.info("Account deleted (user_id=%s)", user.pk)
    return user
