"""
Payout Service — Payout Domain (Change Set §36.5؛ Infra §14/§16)

دفع فوري للمقاول عند تأكيد العميل إنجاز العمل. كل الطفرات تمر من هنا —
طبقة الـAPI لا تستدعي `.objects` مباشرة (راجع §43).

📌 المُحفِّز الوحيد: Job.status == COMPLETED. لا حالة أخرى تُقبل، ولا
   يُستنتج الاستحقاق من Booking.status ولا من Payment — تأكيد العميل هو
   الحدث القانوني الوحيد (§36.3/§36.5).

🔒 التكرارية الصارمة (Infra §14/§16): وجود Payout سابق لنفس الحجز — بأي
   حالة، بما فيها FAILED — يمنع إنشاء ثانٍ **ويمنع استدعاء الـadapter
   مرة أخرى**. الفحص يسبق أي لمس للمزوّد، لا بعده.

⚠️ سياسة غير محسومة (مُعلَّمة صراحةً، نفس ما تُرك مع Payment في §43):
   ماذا يعني فشل الدفع للمقاول؟ لا إعادة محاولة، ولا مهمة دورية، ولا
   إشعار، ولا أي تغيير على Job أو Booking. الدفعة تبقى FAILED والقرار
   متروك لمرحلة لاحقة. لا تُضف سلوكًا افتراضيًا في هذا الملف.

⚠️ لا تجميع: دفعة لكل حجز، فورًا. لا توجد هنا دالة تجمع دفعات ولا
   تشتغل على مجموعة — وهذا قرار محسوم لا نقص.
"""

import logging

from django.db import IntegrityError, transaction

from apps.jobs.models import Job, JobStatus

from ..adapters import get_payout_adapter
from ..models import Payout, PayoutStatus

logger = logging.getLogger(__name__)


class PayoutError(Exception):
    """أصل أخطاء نطاق الدفع للمقاول."""

    code = "payout_error"


class PayoutPermissionError(PayoutError):
    """لا صلاحية لعرض هذه الدفعة."""

    code = "payout_forbidden"


class PayoutNotFoundError(PayoutError):
    code = "payout_not_found"


class JobNotCompletedError(PayoutError):
    """لا دفع قبل تأكيد العميل إنجاز العمل (§36.5)."""

    code = "job_not_completed"


class MissingPriceError(PayoutError):
    """لا دفع بلا لقطة سعر مثبَّتة."""

    code = "booking_has_no_price"


class MissingContractorError(PayoutError):
    """لا دفع بلا مقاول مُسنَد — لا مستحِق."""

    code = "no_assigned_contractor"


class PayoutAlreadyExistsError(PayoutError):
    """الحجز مدفوع سابقًا — حجز واحد = دفعة مقاول واحدة."""

    code = "payout_already_exists"


def build_idempotency_key(booking):
    """
    مفتاح ثابت مشتق من معرّف الحجز.

    ⚠️ ثابت عمدًا (لا وقت ولا عشوائية): إعادة الاستدعاء للحجز نفسه تصل
       المزوّد بالمفتاح نفسه، فلا يتكرر التحويل حتى لو فشل الاتصال ووصل
       الطلب مرتين. بادئة "payout-" تميّزه عن مفتاح الشحن ("booking-")
       حتى لا يتصادم المفتاحان عند مزوّد يخدم الاتجاهين.
    """
    return f"payout-booking-{booking.id}"


@transaction.atomic
def release_payout_for_booking(booking):
    """
    يدفع للمقاول قيمة الحجز كاملة فور تأكيد العميل، ويعيد الـPayout.

    🔒 الشرط الوحيد: للحجز مهمة حالتها COMPLETED.
    🔒 تكراري بصرامة: الدفعة الموجودة تُرجع كما هي بلا استدعاء المزوّد.

    ⚠️ يعيد الدفعة سواء نجحت أو فشلت. لا يلمس Job ولا Booking إطلاقًا.

    Raises:
        JobNotCompletedError:      لا مهمة أو حالتها ليست COMPLETED.
        MissingPriceError:         لا لقطة سعر على الحجز.
        MissingContractorError:    لا مقاول مُسنَد.
        PayoutAlreadyExistsError:  للحجز دفعة سابقة (بأي حالة).
    """
    # 🔒 الفحص التكراري أولًا — قبل أي فحص آخر وقبل لمس المزوّد.
    #    الدفعة الموجودة (حتى الفاشلة) تمنع أي محاولة ثانية.
    existing = Payout.objects.filter(booking=booking).first()
    if existing is not None:
        raise PayoutAlreadyExistsError(
            "This booking already has a payout; it cannot be paid twice."
        )

    # المصدر الوحيد للاستحقاق: تأكيد العميل عبر Job.COMPLETED
    job = Job.objects.filter(booking=booking).only("id", "status").first()
    if job is None or job.status != JobStatus.COMPLETED:
        current = job.status if job is not None else "no job"
        raise JobNotCompletedError(
            f"Job must be COMPLETED before paying the contractor (currently {current})."
        )

    if booking.computed_price is None:
        raise MissingPriceError("Booking has no computed price; nothing to pay out.")

    profile = booking.assigned_contractor
    contractor_user_id = profile.user_id if profile is not None else None
    if contractor_user_id is None:
        raise MissingContractorError(
            "Booking has no assigned contractor; there is no payee."
        )

    payout = Payout(
        booking=booking,
        contractor_id=contractor_user_id,
        amount=booking.computed_price,
        status=PayoutStatus.PENDING,
    )
    payout.full_clean()

    try:
        payout.save()
    except IntegrityError as exc:
        # سباق: أنشأ عاملٌ آخر الدفعة بيننا وبين الفحص أعلاه
        raise PayoutAlreadyExistsError(
            "This booking already has a payout; it cannot be paid twice."
        ) from exc

    adapter = get_payout_adapter()
    result = adapter.payout(
        amount=payout.amount,
        contractor_reference=str(contractor_user_id),
        idempotency_key=build_idempotency_key(booking),
    )

    if result.success:
        payout.status = PayoutStatus.SUCCEEDED
        payout.provider_reference = result.provider_reference
        payout.failure_reason = None
    else:
        payout.status = PayoutStatus.FAILED
        payout.provider_reference = result.provider_reference
        payout.failure_reason = result.failure_reason

    payout.save(
        update_fields=["status", "provider_reference", "failure_reason", "updated_at"]
    )

    logger.info(
        "Payout %s (payout_id=%s, booking_id=%s, contractor_id=%s, amount=%s)",
        payout.status,
        payout.id,
        booking.id,
        contractor_user_id,
        payout.amount,
    )

    # ⚠️ لا Job ولا Booking يُمسّان هنا مهما كانت النتيجة — سياسة مفتوحة.
    return payout


def get_payout_by_booking_id(user, booking_id):
    """
    يعيد دفعة حجز بمعرّف الحجز، بعد فحص الصلاحية.

    🔒 الجلب في طبقة الخدمة لا الـAPI (§43): الحجز غير الموجود والحجز
       بلا دفعة يرفعان الخطأ نفسه، فلا تملك الواجهة ما تميّز به بينهما.
    """
    from apps.bookings.models import Booking

    booking = (
        Booking.objects.filter(pk=booking_id)
        .select_related("assigned_contractor")
        .first()
    )
    if booking is None:
        raise PayoutNotFoundError("No payout found for this booking.")

    payout = Payout.objects.filter(booking=booking).first()
    if payout is None:
        raise PayoutNotFoundError("No payout found for this booking.")

    assert_can_view_payout(user, payout)
    return payout


def assert_can_view_payout(user, payout):
    """
    🔒 من يرى الدفعة: المقاول المستحِق، أو الإدارة.

    ⚠️ العميل **لا** يرى دفعة المقاول: ما يدفعه العميل شأنه (Payment)،
       وما يستلمه المقاول شأن المقاول والإدارة. كشفه للعميل يُظهر بنية
       التسوية بلا سبب عملي.
    """
    from apps.accounts.roles import ConfirmedRole

    if user is None or not user.is_authenticated:
        raise PayoutPermissionError("Authentication required.")

    is_admin = user.role == ConfirmedRole.ADMIN
    is_payee = payout.contractor_id == user.id

    if not (is_admin or is_payee):
        logger.warning(
            "Payout access denied (user_id=%s, payout_id=%s)", user.id, payout.id
        )
        raise PayoutPermissionError("You do not have access to this payout.")
