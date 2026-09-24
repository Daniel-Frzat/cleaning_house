"""
Access notes tests — Booking Domain

ملاحظات وصول يكتبها العميل بنفسه لكل زيارة: "المفتاح تحت السجادة"،
"الكلب في الحديقة"، "الجرس معطّل".

🔒 الموضوع الأمني: قد تحوي مكان مفتاح المنزل. لا تُكشف إلا للمقاول
   المُسنَد بعد القبول — لا لمقاول عُرض عليه الحجز ولم يقبل، ولا للإدارة.

📌 يُملأ يدويًا من العميل: لا يُشتق من العنوان، ولا يُورَّث من حجز سابق،
   ولا يُولَّد من أي مصدر.
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.bookings.models import (
    ACCESS_NOTES_MAX_LENGTH,
    Booking,
    DispatchOffer,
)
from apps.bookings.services import bookings as booking_svc
from apps.bookings.services import offers as offers_svc
from apps.contractors.models import (
    AvailabilityStatus,
    BusinessRegistration,
    ContractorProfile,
    InsuranceDocument,
    VerificationStatus,
)
from apps.jobs.services import jobs as jobs_svc
from apps.properties.models import Property, PropertyAddress, PropertyType
from apps.services.models import PricingConfig, ServiceType

NOTES = "Key is under the pot. Dog in the back yard — do not let him out."


@pytest.fixture
def client():
    return Client()


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


@pytest.fixture
def customer(db):
    return User.objects.create_user(phone="+61400330001",
                                    role=ConfirmedRole.CUSTOMER)


@pytest.fixture
def admin(db):
    return User.objects.create_user(phone="+61499999993",
                                    role=ConfirmedRole.ADMIN)


@pytest.fixture
def prop(customer):
    p = Property.objects.create(owner=customer, label="Home",
                                property_type=PropertyType.HOUSE)
    PropertyAddress.objects.create(
        property=p, street_address="12 George St", suburb="Sydney",
        state="NSW", postcode="2000",
        latitude=Decimal("-33.868800"), longitude=Decimal("151.209300"))
    return p


@pytest.fixture
def service(db):
    return ServiceType.objects.create(name="Regular Cleaning",
                                      room_price=Decimal("45.00"),
                                      base_price=Decimal("80.00"))


@pytest.fixture
def pricing(db):
    cfg, _ = PricingConfig.objects.get_or_create(pk=PricingConfig.SINGLETON_PK)
    cfg.price_per_km = Decimal("2.00")
    cfg.save()
    return cfg


def make_contractor(admin, phone="+61400330002", coords=("-33.878800",
                                                         "151.209300")):
    """coords افتراضيًا قريبة؛ الاختبارات التي تحتاج مقاولًا ثانيًا
    تُبعده عمدًا ليكون العرض الأول للأقرب بلا لبس."""
    user = User.objects.create_user(phone=phone, role=ConfirmedRole.CONTRACTOR)
    profile = ContractorProfile.objects.create(
        user=user, business_name="Sparkle",
        latitude=Decimal(coords[0]), longitude=Decimal(coords[1]),
        availability_status=AvailabilityStatus.AVAILABLE)
    BusinessRegistration.objects.create(
        contractor=profile, abn="51824753556", business_name="Co",
        status=VerificationStatus.VERIFIED, reviewed_by=admin,
        reviewed_at=timezone.now())
    InsuranceDocument.objects.create(
        contractor=profile, document_reference="P1",
        expiry_date=timezone.localdate() + timedelta(days=365),
        status=VerificationStatus.VERIFIED, reviewed_by=admin,
        reviewed_at=timezone.now())
    return user, profile


def when():
    return (timezone.now() + timedelta(days=4)).replace(
        hour=23, minute=0, second=0, microsecond=0)


def create(customer, prop, service, notes=NOTES, capture=None):
    kwargs = dict(
        property_id=prop.id,
        service_selections=[{"service_type_id": service.id, "room_count": 1}],
        scheduled_at=when(),
        access_notes=notes,
    )
    if capture is None:
        return booking_svc.create_booking(customer, **kwargs)
    with capture(execute=True):
        return booking_svc.create_booking(customer, **kwargs)


# ============================================================
# 1) يكتبه العميل بنفسه
# ============================================================
@pytest.mark.django_db
def test_the_customer_writes_it_and_it_is_stored_verbatim(customer, prop, service):
    booking = create(customer, prop, service)

    assert Booking.objects.get(pk=booking.pk).access_notes == NOTES


@pytest.mark.django_db
def test_it_arrives_through_the_api_exactly_as_typed(client, customer, prop,
                                                     service):
    payload = {
        "property_id": str(prop.id),
        "service_selections": [{"service_type_id": str(service.id),
                                "room_count": 1}],
        "scheduled_at": when().isoformat(),
        "access_notes": NOTES,
    }

    r = client.post("/api/bookings", data=json.dumps(payload),
                    content_type="application/json", **auth(customer))

    assert r.status_code == 201, r.content
    assert r.json()["access_notes"] == NOTES


@pytest.mark.django_db
def test_it_is_optional(client, customer, prop, service):
    """📌 أغلب الزيارات لا تحتاج تعليمات — الحقل اختياري لا إلزامي."""
    payload = {
        "property_id": str(prop.id),
        "service_selections": [{"service_type_id": str(service.id),
                                "room_count": 1}],
        "scheduled_at": when().isoformat(),
    }

    r = client.post("/api/bookings", data=json.dumps(payload),
                    content_type="application/json", **auth(customer))

    assert r.status_code == 201, r.content
    assert r.json()["access_notes"] == ""


@pytest.mark.django_db
def test_nothing_generates_it(customer, prop, service):
    """
    📌 يدويّ بحت: لا يُشتق من العنوان ولا من أي حقل آخر.

    لو اشتُقّ يومًا من العنوان لصار هذا الحقل غير فارغ.
    """
    booking = create(customer, prop, service, notes="")

    assert booking.access_notes == ""
    assert prop.address.street_address not in (booking.access_notes or "x")


@pytest.mark.django_db
def test_it_is_not_inherited_by_the_next_booking(customer, prop, service):
    """⚠️ لكل زيارة ملاحظاتها — لا يُورَّث من حجز سابق."""
    create(customer, prop, service, notes=NOTES)

    second = create(customer, prop, service, notes="")

    assert second.access_notes == ""


@pytest.mark.django_db
def test_whitespace_is_trimmed(customer, prop, service):
    booking = create(customer, prop, service, notes="   Ring twice.   ")

    assert booking.access_notes == "Ring twice."


@pytest.mark.django_db
def test_too_long_is_rejected(customer, prop, service):
    """تعليمات وصول لا رسالة — الحدّ يمنع استعماله قناة تواصل."""
    with pytest.raises(ValidationError):
        create(customer, prop, service, notes="x" * (ACCESS_NOTES_MAX_LENGTH + 1))


@pytest.mark.django_db
def test_the_limit_boundary_is_accepted(customer, prop, service):
    booking = create(customer, prop, service, notes="x" * ACCESS_NOTES_MAX_LENGTH)

    assert len(booking.access_notes) == ACCESS_NOTES_MAX_LENGTH


# ============================================================
# 2) 🔒 من يراه — صلب الموضوع
# ============================================================
@pytest.mark.django_db
def test_the_owner_sees_their_own_notes(client, customer, prop, service):
    """العميل كتبه، فله أن يراجعه."""
    booking = create(customer, prop, service)

    body = client.get(f"/api/bookings/{booking.id}", **auth(customer)).json()

    assert body["access_notes"] == NOTES


@pytest.mark.django_db
def test_the_assigned_contractor_sees_it_on_the_job(
    client, customer, admin, prop, service, pricing,
    django_capture_on_commit_callbacks,
):
    """📌 الغرض كله: العامل يحتاجه عند الوصول."""
    cu, _ = make_contractor(admin)
    booking = create(customer, prop, service,
                     capture=django_capture_on_commit_callbacks)
    offer = DispatchOffer.objects.get(booking=booking)
    with django_capture_on_commit_callbacks(execute=True):
        offers_svc.accept_offer(cu, offer.id)

    body = client.get(f"/api/bookings/{booking.id}/job", **auth(cu)).json()

    assert body["access_notes"] == NOTES


@pytest.mark.django_db
def test_an_unassigned_contractor_never_sees_it(
    client, customer, admin, prop, service, pricing,
    django_capture_on_commit_callbacks,
):
    """
    🔒 مقاول آخر لا يصل إلى المهمة أصلًا (404 موحّد).

    ⚠️ هذا ما يمنع كشف مكان المفتاح لمن لن يزور المنزل أبدًا.
    """
    cu, _ = make_contractor(admin)
    other, _ = make_contractor(admin, phone="+61400330003",
                               coords=("-34.068800", "151.209300"))
    booking = create(customer, prop, service,
                     capture=django_capture_on_commit_callbacks)
    offer = DispatchOffer.objects.get(booking=booking)
    with django_capture_on_commit_callbacks(execute=True):
        offers_svc.accept_offer(cu, offer.id)

    r = client.get(f"/api/bookings/{booking.id}/job", **auth(other))

    assert r.status_code == 404, r.content
    assert NOTES not in r.content.decode()


@pytest.mark.django_db
def test_the_admin_does_not_get_the_notes(
    client, customer, admin, prop, service, pricing,
    django_capture_on_commit_callbacks,
):
    """
    🔒 الإدارة ترى المهمة ولا ترى ملاحظات الوصول.

    الإدارة ليست المقاول المُسنَد، ولا تحتاج مكان المفتاح لتدير النظام.
    """
    cu, _ = make_contractor(admin)
    booking = create(customer, prop, service,
                     capture=django_capture_on_commit_callbacks)
    offer = DispatchOffer.objects.get(booking=booking)
    with django_capture_on_commit_callbacks(execute=True):
        offers_svc.accept_offer(cu, offer.id)

    body = client.get(f"/api/bookings/{booking.id}/job", **auth(admin)).json()

    assert body["access_notes"] is None


@pytest.mark.django_db
def test_the_customer_reading_the_job_does_not_get_them_echoed(
    client, customer, admin, prop, service, pricing,
    django_capture_on_commit_callbacks,
):
    """العميل يقرؤه من حجزه، لا من المهمة — سطح واحد لا اثنان."""
    cu, _ = make_contractor(admin)
    booking = create(customer, prop, service,
                     capture=django_capture_on_commit_callbacks)
    offer = DispatchOffer.objects.get(booking=booking)
    with django_capture_on_commit_callbacks(execute=True):
        offers_svc.accept_offer(cu, offer.id)

    body = client.get(f"/api/bookings/{booking.id}/job",
                      **auth(customer)).json()

    assert body["access_notes"] is None


@pytest.mark.django_db
def test_the_offer_does_not_carry_the_notes(
    customer, admin, prop, service, pricing,
    django_capture_on_commit_callbacks,
):
    """
    🔒 قبل القبول لا كشف: العرض نفسه لا يحمل الملاحظات.

    ⚠️ هذا هو الفرق بين "من سيزور المنزل" و"من قد يرفض".
    """
    make_contractor(admin)
    booking = create(customer, prop, service,
                     capture=django_capture_on_commit_callbacks)

    offer = DispatchOffer.objects.get(booking=booking)

    assert not hasattr(offer, "access_notes")
    assert NOTES not in str(offer.__dict__)


# ============================================================
# 3) دورة حياة المهمة
# ============================================================
@pytest.mark.django_db
def test_the_contractor_still_sees_them_after_starting(
    client, customer, admin, prop, service, pricing,
    django_capture_on_commit_callbacks,
):
    """يحتاجها أثناء العمل لا عند الوصول فقط."""
    cu, _ = make_contractor(admin)
    booking = create(customer, prop, service,
                     capture=django_capture_on_commit_callbacks)
    offer = DispatchOffer.objects.get(booking=booking)
    with django_capture_on_commit_callbacks(execute=True):
        offers_svc.accept_offer(cu, offer.id)
    job = client.get(f"/api/bookings/{booking.id}/job", **auth(cu)).json()

    started = client.post(f"/api/contractor/jobs/{job['id']}/start",
                          **auth(cu)).json()

    assert started["access_notes"] == NOTES


@pytest.mark.django_db
def test_is_assigned_contractor_is_the_single_source_of_truth(
    customer, admin, prop, service, pricing,
    django_capture_on_commit_callbacks,
):
    """
    🔒 قرار الكشف يقرأ نفس دالة الملكية — لا منطق ثانٍ في طبقة الـAPI.
    """
    cu, _ = make_contractor(admin)
    other, _ = make_contractor(admin, phone="+61400330004",
                               coords=("-34.068800", "151.209300"))
    booking = create(customer, prop, service,
                     capture=django_capture_on_commit_callbacks)
    offer = DispatchOffer.objects.get(booking=booking)
    with django_capture_on_commit_callbacks(execute=True):
        offers_svc.accept_offer(cu, offer.id)

    job = jobs_svc.get_job_by_booking_id(cu, str(booking.id))

    assert jobs_svc.is_assigned_contractor(cu, job) is True
    assert jobs_svc.is_assigned_contractor(other, job) is False
    assert jobs_svc.is_assigned_contractor(customer, job) is False
    assert jobs_svc.is_assigned_contractor(admin, job) is False
