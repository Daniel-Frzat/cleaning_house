"""
Dual-role tests — Identity + Contractors

حساب واحد يعمل زبونًا وعاملًا معًا: الانضمام، الصلاحيات، حالة الاعتماد،
والأوضاع المتاحة.

📌 السيناريوهات الأربعة المطلوبة:
     1) زبون فقط
     2) طلب عامل قيد المراجعة
     3) مستخدم معتمد بالصلاحيتين
     4) عامل تتوقف صلاحياته بعد تسجيل الدخول

🔒 المحور الأمني: تغيير الوضع في الواجهة لا يمنح صلاحية، والاعتماد شرط
   مستقل عن الصلاحية.
"""

import datetime
import json

import pytest
from django.test import Client

from apps.accounts.models import User, UserStatus
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.contractors.models import (
    BusinessRegistration,
    ContractorProfile,
    InsuranceDocument,
    VerificationStatus,
)
from apps.contractors.services.verification import (
    ContractorStatus,
    get_contractor_status,
)


@pytest.fixture
def client():
    return Client()


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


def post(client, url, payload, **extra):
    return client.post(
        url, data=json.dumps(payload), content_type="application/json", **extra
    )


PROFILE = {
    "business_name": "Sparkle Co",
    "street_address": "5 Pitt St",
    "suburb": "Sydney",
    "state": "NSW",
    "postcode": "2000",
    "latitude": "-33.868800",
    "longitude": "151.209300",
}


@pytest.fixture
def customer(db):
    return User.objects.create_user(phone="+61400220001", role=ConfirmedRole.CUSTOMER)


def approve_docs(profile, *, registration=VerificationStatus.VERIFIED,
                 insurance=VerificationStatus.VERIFIED, expired=False):
    BusinessRegistration.objects.create(
        contractor=profile, abn="12345678901", business_name="Sparkle Co",
        status=registration,
        rejection_reason="no" if registration == VerificationStatus.REJECTED else None,
    )
    expiry = datetime.date.today() + datetime.timedelta(days=-1 if expired else 300)
    InsuranceDocument.objects.create(
        contractor=profile, document_reference="POL-1", expiry_date=expiry,
        status=insurance,
        rejection_reason="no" if insurance == VerificationStatus.REJECTED else None,
    )


# ============================================================
# السيناريو 1 — زبون فقط
# ============================================================
@pytest.mark.django_db
def test_customer_only_sees_one_mode(client, customer):
    body = client.get("/api/auth/me", **auth(customer)).json()

    assert body["roles"] == [ConfirmedRole.CUSTOMER]
    assert body["contractor_status"] == ContractorStatus.NONE
    assert body["available_modes"] == ["CUSTOMER"]


@pytest.mark.django_db
def test_customer_only_cannot_touch_contractor_endpoints(client, customer):
    """لا صلاحية عامل ⇒ مسارات العامل مغلقة."""
    assert client.get("/api/contractor/profile", **auth(customer)).status_code == 403


# ============================================================
# السيناريو 2 — طلب قيد المراجعة
# ============================================================
@pytest.mark.django_db
def test_joining_as_contractor_keeps_the_same_account(client, customer):
    """
    📌 "Work with Cleano": نفس user_id ونفس الرقم، بلا حساب جديد.
    """
    before = customer.id

    r = post(client, "/api/contractor/profile", PROFILE, **auth(customer))

    assert r.status_code == 201, r.content
    customer.refresh_from_db()
    assert customer.id == before
    assert customer.is_contractor is True
    # ⚠️ الدور الأساسي لم يتغيّر
    assert customer.role == ConfirmedRole.CUSTOMER


@pytest.mark.django_db
def test_pending_application_reports_both_modes_but_not_approved(client, customer):
    post(client, "/api/contractor/profile", PROFILE, **auth(customer))

    body = client.get("/api/auth/me", **auth(customer)).json()

    assert set(body["roles"]) == {ConfirmedRole.CUSTOMER, ConfirmedRole.CONTRACTOR}
    assert body["contractor_status"] == ContractorStatus.PENDING
    assert body["available_modes"] == ["CUSTOMER", "CONTRACTOR"]


@pytest.mark.django_db
def test_customer_access_survives_joining_as_contractor(client, customer):
    """🔒 الحجوزات والعقارات لا تُفقد — البند الأهم في الطلب."""
    post(client, "/api/contractor/profile", PROFILE, **auth(customer))

    assert client.get("/api/properties", **auth(customer)).status_code == 200
    assert client.get("/api/bookings", **auth(customer)).status_code == 200


@pytest.mark.django_db
def test_second_profile_for_the_same_account_is_refused(client, customer):
    """⚠️ منع تكرار إنشاء ملف عامل لنفس الحساب."""
    post(client, "/api/contractor/profile", PROFILE, **auth(customer))

    r = post(client, "/api/contractor/profile", PROFILE, **auth(customer))

    assert r.status_code == 409, r.content
    assert ContractorProfile.objects.filter(user=customer).count() == 1


@pytest.mark.django_db
def test_rejected_document_reports_action_required(client, customer):
    post(client, "/api/contractor/profile", PROFILE, **auth(customer))
    profile = ContractorProfile.objects.get(user=customer)
    approve_docs(profile, registration=VerificationStatus.REJECTED)

    body = client.get("/api/auth/me", **auth(customer)).json()

    assert body["contractor_status"] == ContractorStatus.ACTION_REQUIRED


@pytest.mark.django_db
def test_expired_insurance_reports_action_required(client, customer):
    post(client, "/api/contractor/profile", PROFILE, **auth(customer))
    profile = ContractorProfile.objects.get(user=customer)
    approve_docs(profile, expired=True)

    body = client.get("/api/auth/me", **auth(customer)).json()

    assert body["contractor_status"] == ContractorStatus.ACTION_REQUIRED


# ============================================================
# السيناريو 3 — معتمد بالصلاحيتين
# ============================================================
@pytest.mark.django_db
def test_approved_dual_user_reports_approved(client, customer):
    post(client, "/api/contractor/profile", PROFILE, **auth(customer))
    approve_docs(ContractorProfile.objects.get(user=customer))

    body = client.get("/api/auth/me", **auth(customer)).json()

    assert set(body["roles"]) == {ConfirmedRole.CUSTOMER, ConfirmedRole.CONTRACTOR}
    assert body["contractor_status"] == ContractorStatus.APPROVED
    assert body["available_modes"] == ["CUSTOMER", "CONTRACTOR"]


@pytest.mark.django_db
def test_approved_dual_user_can_use_both_sides(client, customer):
    post(client, "/api/contractor/profile", PROFILE, **auth(customer))
    approve_docs(ContractorProfile.objects.get(user=customer))

    # جانب الزبون
    assert client.get("/api/properties", **auth(customer)).status_code == 200
    # جانب العامل
    assert client.get("/api/contractor/profile", **auth(customer)).status_code == 200
    assert client.patch(
        "/api/contractor/profile/availability",
        data=json.dumps({"availability_status": "AVAILABLE"}),
        content_type="application/json",
        **auth(customer),
    ).status_code == 200


# ============================================================
# السيناريو 4 — توقّف الصلاحيات بعد الدخول
# ============================================================
@pytest.mark.django_db
def test_suspension_takes_effect_on_the_existing_token(client, customer):
    """
    🔒 الصلاحية تُقرأ من قاعدة البيانات في كل طلب لا من التوكن.

    فإيقاف الحساب يسري فورًا على جلسة مفتوحة، بلا إبطال توكنات.
    """
    post(client, "/api/contractor/profile", PROFILE, **auth(customer))
    approve_docs(ContractorProfile.objects.get(user=customer))
    headers = auth(customer)  # توكن أُصدر والحساب نشط

    assert client.get("/api/auth/me", **headers).status_code == 200

    customer.status = UserStatus.SUSPENDED
    customer.is_active = False
    customer.save(update_fields=["status", "is_active"])

    # نفس التوكن القديم
    assert client.get("/api/auth/me", **headers).status_code == 401


@pytest.mark.django_db
def test_suspended_contractor_status_is_reported(customer):
    """الحالة تُعاد SUSPENDED لا APPROVED — مشتقّة لحظيًا."""
    profile = ContractorProfile.objects.create(user=customer, business_name="X")
    approve_docs(profile)
    customer.is_contractor = True
    customer.status = UserStatus.SUSPENDED
    customer.save(update_fields=["is_contractor", "status"])

    assert get_contractor_status(customer) == ContractorStatus.SUSPENDED


@pytest.mark.django_db
def test_revoking_the_capability_closes_contractor_endpoints(client, customer):
    post(client, "/api/contractor/profile", PROFILE, **auth(customer))
    assert client.get("/api/contractor/profile", **auth(customer)).status_code == 200

    customer.is_contractor = False
    customer.save(update_fields=["is_contractor"])

    assert client.get("/api/contractor/profile", **auth(customer)).status_code == 403
    # جانب الزبون لم يتأثر
    assert client.get("/api/properties", **auth(customer)).status_code == 200


# ============================================================
# 🔒 الحدود الأمنية
# ============================================================
@pytest.mark.django_db
def test_admin_is_exclusive_and_cannot_join_as_contractor(client, db):
    """🔒 ADMIN حصري: لا يُجمع مع زبون ولا عامل."""
    admin = User.objects.create_user(phone="+61400220009", role=ConfirmedRole.ADMIN)

    body = client.get("/api/auth/me", **auth(admin)).json()
    assert body["roles"] == [ConfirmedRole.ADMIN]
    assert body["available_modes"] == []

    r = post(client, "/api/contractor/profile", PROFILE, **auth(admin))
    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_capability_cannot_be_granted_through_the_profile_endpoint(client, customer):
    """
    🔒 is_contractor ليس حقلًا مقبولًا من العميل — يُمنح بإنشاء الملف فقط.
    """
    r = post(
        client, "/api/auth/me" if False else "/api/contractor/profile",
        {**PROFILE, "is_contractor": True, "role": "ADMIN"},
        **auth(customer),
    )

    assert r.status_code == 201, r.content
    customer.refresh_from_db()
    assert customer.role == ConfirmedRole.CUSTOMER  # لم يُصعَّد


@pytest.mark.django_db
def test_capability_is_not_settable_via_profile_patch(client, customer):
    """تعديل الملف الشخصي لا يمنح صلاحية العامل."""
    r = client.patch(
        "/api/auth/me",
        data=json.dumps({"full_name": "X", "is_contractor": True}),
        content_type="application/json",
        **auth(customer),
    )

    assert r.status_code == 200
    customer.refresh_from_db()
    assert customer.is_contractor is False


@pytest.mark.django_db
def test_pending_contractor_cannot_be_dispatched_work(client, customer):
    """
    🔒 الصلاحية ≠ الاعتماد: عامل قيد المراجعة لا يدخل قائمة المرشحين.
    """
    from decimal import Decimal

    from apps.bookings.services.dispatch import find_candidates
    from apps.bookings.models import Booking, BookingServiceSelection, BookingStatus
    from apps.contractors.models import AvailabilityStatus
    from apps.properties.models import Property, PropertyAddress, PropertyType
    from apps.services.models import ServiceType

    post(client, "/api/contractor/profile", PROFILE, **auth(customer))
    profile = ContractorProfile.objects.get(user=customer)
    profile.availability_status = AvailabilityStatus.AVAILABLE
    profile.save(update_fields=["availability_status"])
    # وثائق قيد المراجعة لا معتمدة
    approve_docs(profile, registration=VerificationStatus.PENDING,
                 insurance=VerificationStatus.PENDING)

    other = User.objects.create_user(phone="+61400220050", role=ConfirmedRole.CUSTOMER)
    prop = Property.objects.create(owner=other, property_type=PropertyType.HOUSE)
    PropertyAddress.objects.create(
        property=prop, street_address="1 St", suburb="Sydney", state="NSW",
        postcode="2000", latitude=Decimal("-33.87"), longitude=Decimal("151.21"))
    svc = ServiceType.objects.create(
        name="Gen", room_price=Decimal("10.00"), base_price=Decimal("10.00"))
    booking = Booking.objects.create(
        customer=other, property=prop, status=BookingStatus.PENDING)
    BookingServiceSelection.objects.create(
        booking=booking, service_type=svc, room_count=1)

    assert find_candidates(booking) == []


@pytest.mark.django_db
def test_available_modes_never_includes_a_role_the_user_lacks(client, customer):
    """
    📌 السويتش عرضٌ لا صلاحية: الفرونت يتحقق أن وضعه المحفوظ ما زال هنا.
    """
    body = client.get("/api/auth/me", **auth(customer)).json()
    assert "CONTRACTOR" not in body["available_modes"]

    post(client, "/api/contractor/profile", PROFILE, **auth(customer))

    body = client.get("/api/auth/me", **auth(customer)).json()
    assert "CONTRACTOR" in body["available_modes"]


@pytest.mark.django_db
def test_login_response_carries_the_same_dual_role_fields(client, customer):
    """رد الدخول و/auth/me متطابقان — مُسلسِل واحد."""
    import apps.accounts.services.otp as otp_module
    from apps.accounts.services import otp as otp_svc

    post(client, "/api/contractor/profile", PROFILE, **auth(customer))

    real = otp_module.generate_code
    cap = {}

    def spy(n):
        code = real(n)
        cap["code"] = code
        return code

    otp_module.generate_code = spy
    otp_svc.generate_and_send(customer.phone)
    otp_module.generate_code = real

    r = post(client, "/api/auth/otp/verify",
             {"phone": customer.phone, "code": cap["code"]})

    user_block = r.json()["user"]
    me_block = client.get("/api/auth/me", **auth(customer)).json()

    assert user_block == me_block
    assert set(user_block["roles"]) == {
        ConfirmedRole.CUSTOMER, ConfirmedRole.CONTRACTOR
    }
