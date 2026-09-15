"""
Dispatch progress tests — Booking Domain

موضوع الملف: التمييز بين "ما زلنا نبحث" و"لا يوجد عامل متاح".

⚠️ قبل هذه الإضافة كان الحجز PENDING في الحالتين معًا، فلم تملك الواجهة
   أي وسيلة لتعرف أيهما تعرض.

⚠️ ملاحظة تقنية: الإسناد يجري في transaction.on_commit الذي لا يعمل داخل
   معاملة الاختبار — من دون django_capture_on_commit_callbacks تمرّ هذه
   الاختبارات دون أن تفحص شيئًا.
"""

import pytest

from apps.bookings.models import (
    BookingStatus,
    DispatchOffer,
    DispatchStatus,
)
from apps.bookings.services import offers as offers_svc
from apps.bookings.services.dispatch import assign_next_contractor

from .test_bookings_dispatch import (  # noqa: F401 - fixtures معاد استخدامها
    FAR,
    NEAR,
    auth,
    client,
    create_booking,
    customer,
    general,
    make_contractor,
    pricing,
    prop,
)


# ============================================================
# 1) الحالة الابتدائية
# ============================================================
@pytest.mark.django_db
def test_enum_has_exactly_three_states():
    """⚠️ لا CANCELLED ولا EXPIRED — الحقل يصف البحث لا دورة حياة الحجز."""
    assert set(DispatchStatus.values) == {"SEARCHING", "NO_CONTRACTOR", "ASSIGNED"}


@pytest.mark.django_db
def test_a_fresh_booking_starts_searching(customer, prop, general, pricing):
    """القيمة الافتراضية تفترض البحث، لا اليأس."""
    booking = create_booking(customer, prop, general)

    assert booking.dispatch_status == DispatchStatus.SEARCHING


# ============================================================
# 2) التمييز نفسه — صلب الموضوع
# ============================================================
@pytest.mark.django_db
def test_no_candidates_marks_no_contractor(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """📌 لا مقاول أصلًا ← NO_CONTRACTOR."""
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    booking.refresh_from_db()
    assert not DispatchOffer.objects.filter(booking=booking).exists()
    assert booking.dispatch_status == DispatchStatus.NO_CONTRACTOR


@pytest.mark.django_db
def test_an_offer_keeps_it_searching(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """📌 عرض نشط ← SEARCHING، وليس NO_CONTRACTOR."""
    make_contractor("+61400008101", coords=NEAR)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    booking.refresh_from_db()
    assert DispatchOffer.objects.filter(booking=booking).count() == 1
    assert booking.dispatch_status == DispatchStatus.SEARCHING


@pytest.mark.django_db
def test_the_two_states_are_actually_different(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """
    📌 جوهر المطلب: حجزان كلاهما PENDING، ومع ذلك يتمايزان.

    لو كان dispatch_status ثابتًا لسقط هذا الاختبار وحده.
    """
    lonely = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    make_contractor("+61400008102", coords=NEAR)
    served = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    lonely.refresh_from_db()
    served.refresh_from_db()

    assert lonely.status == served.status == BookingStatus.PENDING
    assert lonely.dispatch_status != served.dispatch_status


# ============================================================
# 3) الطابع الزمني
# ============================================================
@pytest.mark.django_db
def test_every_attempt_is_stamped_even_a_failed_one(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """محاولة بلا مرشّحين محاولة مع ذلك — عرض «نبحث منذ ...» يحتاج مرجعًا."""
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    booking.refresh_from_db()
    assert booking.dispatch_status == DispatchStatus.NO_CONTRACTOR
    assert booking.last_dispatch_attempt_at is not None


@pytest.mark.django_db
def test_a_later_attempt_moves_the_stamp_forward(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    booking.refresh_from_db()
    first = booking.last_dispatch_attempt_at

    make_contractor("+61400008103", coords=NEAR)
    assign_next_contractor(booking)

    booking.refresh_from_db()
    assert booking.last_dispatch_attempt_at > first


# ============================================================
# 4) التعافي والقبول
# ============================================================
@pytest.mark.django_db
def test_no_contractor_is_not_a_dead_end(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """📌 الحقل يصف آخر محاولة لا حكمًا نهائيًا: مقاول جديد يعيده للبحث."""
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    booking.refresh_from_db()
    assert booking.dispatch_status == DispatchStatus.NO_CONTRACTOR

    make_contractor("+61400008104", coords=NEAR)
    assign_next_contractor(booking)

    booking.refresh_from_db()
    assert booking.dispatch_status == DispatchStatus.SEARCHING


@pytest.mark.django_db
def test_acceptance_ends_the_search(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    contractor_user, _ = make_contractor("+61400008105", coords=NEAR)
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)

    with django_capture_on_commit_callbacks(execute=True):
        offers_svc.accept_offer(contractor_user, offer.id)

    booking.refresh_from_db()
    assert booking.status == BookingStatus.CONFIRMED
    assert booking.dispatch_status == DispatchStatus.ASSIGNED


@pytest.mark.django_db
def test_a_decline_with_a_successor_stays_searching(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    first_user, _ = make_contractor("+61400008106", coords=NEAR)
    make_contractor("+61400008107", coords=FAR)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)

    offers_svc.decline_offer(first_user, offer.id)

    booking.refresh_from_db()
    assert booking.dispatch_status == DispatchStatus.SEARCHING


@pytest.mark.django_db
def test_the_last_decline_exhausts_the_candidates(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """📌 رفض الأخير ← NO_CONTRACTOR، والحجز يبقى PENDING."""
    only_user, _ = make_contractor("+61400008108", coords=NEAR)

    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    offer = DispatchOffer.objects.get(booking=booking)

    offers_svc.decline_offer(only_user, offer.id)

    booking.refresh_from_db()
    assert booking.dispatch_status == DispatchStatus.NO_CONTRACTOR
    assert booking.status == BookingStatus.PENDING


# ============================================================
# 5) ⚠️ حقل عرض لا قرار — القرار المفتوح #16
# ============================================================
@pytest.mark.django_db
def test_no_contractor_does_not_touch_the_booking_status(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """⚠️ لا إلغاء تلقائي: القرار المفتوح #16 لم يُحسم هنا."""
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    booking.refresh_from_db()
    assert booking.status == BookingStatus.PENDING
    assert booking.assigned_contractor_id is None
    assert booking.computed_price is None


@pytest.mark.django_db
def test_no_contractor_does_not_trigger_a_retry(
    customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """⚠️ لا إعادة محاولة مجدولة — لا عرض يظهر من تلقاء نفسه."""
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    make_contractor("+61400008109", coords=NEAR)

    booking.refresh_from_db()
    assert DispatchOffer.objects.filter(booking=booking).count() == 0
    assert booking.dispatch_status == DispatchStatus.NO_CONTRACTOR


# ============================================================
# 6) ما تراه الواجهة
# ============================================================
@pytest.mark.django_db
def test_the_api_exposes_both_fields(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    booking = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    body = client.get(f"/api/bookings/{booking.id}", **auth(customer)).json()

    assert body["dispatch_status"] == DispatchStatus.NO_CONTRACTOR
    assert body["last_dispatch_attempt_at"] is not None


@pytest.mark.django_db
def test_the_customer_can_tell_the_two_apart_over_the_api(
    client, customer, prop, general, pricing, django_capture_on_commit_callbacks
):
    """📌 الغرض كله، من طرف العميل هذه المرة."""
    lonely = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )
    make_contractor("+61400008110", coords=NEAR)
    served = create_booking(
        customer, prop, general, capture=django_capture_on_commit_callbacks
    )

    rows = {
        row["id"]: row for row in client.get("/api/bookings", **auth(customer)).json()
    }

    assert rows[str(lonely.id)]["status"] == rows[str(served.id)]["status"] == "PENDING"
    assert rows[str(lonely.id)]["dispatch_status"] == "NO_CONTRACTOR"
    assert rows[str(served.id)]["dispatch_status"] == "SEARCHING"
