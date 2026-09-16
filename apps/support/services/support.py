"""
Support Service — Support Domain (MVP)

كل وصول إلى طلبات الدعم يمر من هنا. طبقة الـAPI لا تستدعي `.objects`
مباشرة — نفس النمط المعتمد في بقية النطاقات (§43).

📌 الصلاحية هنا ليست دورًا بل ملكية: أي مستخدم مصادَق عليه يقدّم طلبًا
   ويقرأ طلباته هو. لا بوابة CUSTOMER ولا CONTRACTOR — المقاول يحتاج
   الدعم كما يحتاجه العميل، وحصره بدور واحد كان سيغلق نصف المستخدمين
   خارج شاشة موجودة أصلًا في التطبيق.

🔒 ADMIN وحده يقرأ طلبات الآخرين. ولا مسار API لتغيير الحالة في هذه
   المرحلة: التغيير من لوحة Django (قرار MVP).

⚠️ لا مرفقات ولا ردود — راجع models.py. لا تُضف أيًّا منهما هنا قبل
   حسم مزوّد التخزين وسياسة المحادثة.
"""

import logging

from django.db import transaction

from ..models import SupportRequest, SupportStatus

logger = logging.getLogger(__name__)


class SupportError(Exception):
    """أصل أخطاء نطاق الدعم."""

    code = "support_error"


class SupportPermissionError(SupportError):
    """لا صلاحية لعرض هذا الطلب."""

    code = "support_forbidden"


class SupportNotFoundError(SupportError):
    code = "support_not_found"


class InvalidBookingReferenceError(SupportError):
    """
    الحجز المربوط غير موجود أو ليس للمستخدم.

    🔒 الحالتان خطأ واحد عمدًا: التمييز بينهما يكشف وجود حجز لغير مالكه
       (resource enumeration) — نفس سياسة apps/bookings.
    """

    code = "booking_not_found"


# ------------------------------------------------------------
# فحوص الصلاحية
# ------------------------------------------------------------
def assert_authenticated(user):
    """
    الدعم متاح لكل مستخدم مصادَق عليه — بلا بوابة دور.

    📌 لا has_customer_access هنا: المقاول والعميل كلاهما يفتح طلب دعم،
       والإدارة كذلك.
    """
    if user is None or not user.is_authenticated:
        raise SupportPermissionError("Authentication required.")


def assert_can_view(user, support_request):
    """
    فحص الملكية على مستوى الكائن — نقطة الإنفاذ الوحيدة.

    تُستدعى في كل قراءة حتى لو كان الاستعلام مُرشَّحًا أصلًا (نفس نمط
    payouts/services/payouts.py).
    """
    assert_authenticated(user)

    is_admin = user.has_admin_access()
    is_owner = support_request.user_id == user.id

    if not (is_admin or is_owner):
        logger.warning(
            "Support request access denied (user_id=%s, request_id=%s)",
            user.id,
            support_request.id,
        )
        raise SupportPermissionError("You do not have access to this support request.")


# ------------------------------------------------------------
# الإنشاء
# ------------------------------------------------------------
@transaction.atomic
def create_support_request(user, category, message, booking_id=None):
    """
    ينشئ طلب دعم للمستخدم الحالي.

    ⚠️ status لا يُقبل معاملًا: كل طلب يبدأ SUBMITTED. قبوله من العميل
       كان سيسمح بفتح طلب مُعلَّم RESOLVED.

    🔒 الحجز — إن أُرسل — يُفحص أنه للمستخدم نفسه هنا **و** في
       Model.clean(). الفحص المزدوج مقصود: الأول يعطي رسالة مفهومة،
       والثاني يحرس كل مسار كتابة آخر.
    """
    assert_authenticated(user)

    booking = _resolve_booking(user, booking_id)

    support_request = SupportRequest(
        user=user,
        booking=booking,
        category=category,
        message=message,
        status=SupportStatus.SUBMITTED,
    )
    support_request.full_clean()
    support_request.save()

    logger.info(
        "Support request created (request_id=%s, user_id=%s, category=%s, booking_id=%s)",
        support_request.id,
        user.id,
        category,
        booking_id,
    )

    return support_request


def _resolve_booking(user, booking_id):
    """
    يعيد الحجز المملوك للمستخدم، أو None إن لم يُرسل معرّف.

    الاستيراد داخل الدالة: نمط متبع في المشروع لتفادي دورات الاستيراد
    بين النطاقات.
    """
    if booking_id is None:
        return None

    from apps.bookings.models import Booking

    booking = Booking.objects.filter(pk=booking_id).first()

    # 🔒 غير موجود وغير مملوك → نفس الخطأ.
    if booking is None or booking.customer_id != user.id:
        logger.warning(
            "Support request referenced an inaccessible booking "
            "(user_id=%s, booking_id=%s)",
            user.id,
            booking_id,
        )
        raise InvalidBookingReferenceError("Booking not found.")

    return booking


# ------------------------------------------------------------
# القراءة
# ------------------------------------------------------------
def list_support_requests(user):
    """
    طلبات المستخدم الحالي — والإدارة ترى الجميع.

    📌 الترتيب من Meta (الأحدث أولًا).
    """
    assert_authenticated(user)

    queryset = SupportRequest.objects.select_related("user", "booking")

    if user.has_admin_access():
        return queryset

    return queryset.filter(user=user)


def get_support_request(user, request_id):
    """يعيد طلبًا يملكه المستخدم (أو أي طلب للإدارة)."""
    assert_authenticated(user)

    support_request = (
        SupportRequest.objects.select_related("user", "booking")
        .filter(pk=request_id)
        .first()
    )
    if support_request is None:
        raise SupportNotFoundError("Support request not found.")

    assert_can_view(user, support_request)
    return support_request
