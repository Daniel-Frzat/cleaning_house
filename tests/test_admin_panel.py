"""
لوحة Django Admin — كل نموذج مسجَّل يُفتح، والأقفال تصمد، والأفعال تمر
عبر طبقة الخدمة وتُسجَّل في سجل التدقيق.
"""

from datetime import timedelta
from decimal import Decimal

import re

import pytest
from django.contrib import admin
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth.models import Group, Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from ninja_jwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from apps.accounts.models import (
    AdminLoginChallenge,
    OTPVerification,
    SocialAccount,
    SocialProvider,
    TrustedDevice,
    User,
    UserStatus,
)
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.audit.models import AuditLog
from apps.audit.services.audit import record
from apps.bookings.models import (
    Booking,
    BookingQuote,
    BookingServiceSelection,
    BookingStatus,
    DispatchOffer,
    DispatchOfferStatus,
)
from apps.contractors.models import (
    BusinessRegistration,
    ContractorCurrentLocation,
    ContractorProfile,
    InsuranceDocument,
    VerificationStatus,
)
from apps.jobs.models import Job, JobLocation, JobPhoto, JobStatus, PhotoType
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.payouts.models import Payout, PayoutStatus
from apps.properties.models import Property, PropertyAddress
from apps.services.models import PricingConfig, ServiceType
from apps.support.models import SupportCategory, SupportRequest, SupportStatus

VALID_ABN = "51824753556"


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        phone="+61400000001", email="root@example.com", password="RootPass123!"
    )


@pytest.fixture
def su_client(superuser):
    client = Client()
    client.force_login(superuser)
    return client


@pytest.fixture
def staff_admin(db):
    """أدمن غير superuser بكل صلاحيات النماذج — ما يمنعه هو قواعد اللوحة."""
    user = User.objects.create_user(
        phone="+61400000009", email="staff@example.com", password="StaffPass123!",
        role=ConfirmedRole.ADMIN,
    )
    user.user_permissions.set(Permission.objects.all())
    return User.objects.get(pk=user.pk)


@pytest.fixture
def staff_client(staff_admin):
    client = Client()
    client.force_login(staff_admin)
    return client


@pytest.fixture
def world(superuser):
    """كائن حقيقي واحد على الأقل لكل نموذج مسجَّل."""
    now = timezone.now()
    customer = User.objects.create_user(phone="+61400000002", email="cust@example.com", full_name="Cara Customer")
    worker = User.objects.create_user(
        phone="+61400000003", email="work@example.com", full_name="Wes Worker",
        role=ConfirmedRole.CONTRACTOR,
    )
    profile = ContractorProfile.objects.create(user=worker, business_name="Sparkle Co", suburb="Sydney", state="NSW")
    registration = BusinessRegistration.objects.create(contractor=profile, abn=VALID_ABN, business_name="Sparkle Co")
    insurance = InsuranceDocument.objects.create(
        contractor=profile, document_reference="POL-1", expiry_date=timezone.localdate() + timedelta(days=365)
    )
    service = ServiceType.objects.create(name="Standard clean", room_price=Decimal("20"), base_price=Decimal("50"))
    PricingConfig.objects.create()
    prop = Property.objects.create(owner=customer, property_type="HOUSE", label="Home")
    PropertyAddress.objects.create(
        property=prop, street_address="1 Test St", suburb="Sydney", state="NSW", postcode="2000"
    )
    quote = BookingQuote.objects.create(
        customer=customer, property=prop, service_snapshot=[{"name": "Standard clean", "room_count": 2}],
        services_total=Decimal("90"), maximum_total=Decimal("150"), pricing_version=1,
        expires_at=now + timedelta(hours=1),
    )
    booking = Booking.objects.create(
        customer=customer, property=prop, status=BookingStatus.CONFIRMED, quote=quote,
        max_total=Decimal("150"), computed_price=Decimal("120.50"), assigned_contractor=profile,
        access_notes="Key under the mat", payment_method_reference="pm_secret_token",
    )
    BookingServiceSelection.objects.create(booking=booking, service_type=service, room_count=2)
    DispatchOffer.objects.create(
        booking=booking, contractor=profile, status=DispatchOfferStatus.ACCEPTED,
        distance_km=Decimal("5"), total_amount=Decimal("120.50"), contractor_earnings=Decimal("120.50"),
        travel_fee=Decimal("10"), services_total=Decimal("110.50"), expires_at=now + timedelta(hours=1),
    )
    Payment.objects.create(
        booking=booking, amount=Decimal("120.50"), method=PaymentMethod.CARD, status=PaymentStatus.SUCCEEDED,
        provider_reference="pi_ref_1", action_payload={"client_secret": "cs_super_secret"},
    )
    job = Job.objects.create(booking=booking, status=JobStatus.COMPLETED)
    JobPhoto.objects.create(job=job, photo_type=PhotoType.BEFORE, storage_key="jobs/1/before.jpg", uploaded_by=worker)
    Payout.objects.create(booking=booking, contractor=worker, amount=Decimal("120.50"), status=PayoutStatus.SUCCEEDED)
    SupportRequest.objects.create(user=customer, category=SupportCategory.OTHER, message="Please help")
    OTPVerification.objects.create(
        phone=customer.phone, code_hash="hash-never-shown", expires_at=now + timedelta(minutes=5), max_attempts=5
    )
    SocialAccount.objects.create(user=customer, provider=SocialProvider.GOOGLE, provider_user_id="sub-1")
    TrustedDevice.objects.create(
        user=superuser, token_hash="device-hash", label="Office laptop", expires_at=now + timedelta(days=30)
    )
    record(superuser, "test.seed", target=booking)
    tokens = issue_tokens_for_user(customer)
    outstanding = OutstandingToken.objects.get(user=customer)
    BlacklistedToken.objects.create(token=OutstandingToken.objects.create(
        user=customer, jti="old-jti", token="old", expires_at=now + timedelta(days=1)
    ))
    Group.objects.create(name="Operations")
    return {
        "customer": customer,
        "worker": worker,
        "profile": profile,
        "registration": registration,
        "insurance": insurance,
        "booking": booking,
        "tokens": tokens,
        "outstanding": outstanding,
    }


def _hidden_inputs(client, url):
    """حقول النموذج المخفية (إدارة الـinlines ومعرّفاتها) كما تعرضها الصفحة."""
    body = client.get(url).content.decode()
    data = {}
    for tag in re.findall(r"<input[^>]*>", body):
        if 'type="hidden"' not in tag:
            continue
        name = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if name and name.group(1) != "csrfmiddlewaretoken":
            data[name.group(1)] = value.group(1) if value else ""
    return data


def _url(model, action, *args):
    opts = model._meta
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_{action}", args=args)


# ============================================================
# 1) كل نموذج مسجَّل يُفتح
# ============================================================
@pytest.mark.django_db
def test_every_registered_model_renders(su_client, superuser, world):
    request = type("R", (), {"user": superuser})()
    for model, model_admin in admin.site._registry.items():
        changelist = su_client.get(_url(model, "changelist"))
        assert changelist.status_code == 200, model

        obj = model._default_manager.first()
        assert obj is not None, f"no fixture object for {model}"
        change = su_client.get(_url(model, "change", obj.pk))
        assert change.status_code == 200, model

        add = su_client.get(_url(model, "add"))
        expected = 200 if model_admin.has_add_permission(request) else 403
        assert add.status_code == expected, model


@pytest.mark.django_db
def test_changelist_search_and_filters(su_client, world):
    checks = [
        (Booking, "?q=cust&status__exact=CONFIRMED"),
        (Payment, "?q=pi_ref&status__exact=SUCCEEDED"),
        (Payout, "?q=+614&status__exact=SUCCEEDED"),
        (User, "?q=Cara&role__exact=CUSTOMER"),
        (BusinessRegistration, "?status__exact=PENDING"),
        (SupportRequest, "?status__exact=SUBMITTED&q=help"),
        (AuditLog, "?q=test.seed"),
    ]
    for model, query in checks:
        assert su_client.get(_url(model, "changelist") + query).status_code == 200, model


@pytest.mark.django_db
def test_booking_page_links_related_records_and_hides_secrets(su_client, world):
    booking = world["booking"]
    body = su_client.get(_url(Booking, "change", booking.pk)).content.decode()
    assert _url(Payment, "change", booking.payment.pk) in body
    assert _url(Job, "change", booking.job.pk) in body
    assert _url(Payout, "change", booking.payout.pk) in body
    assert _url(User, "change", world["customer"].pk) in body
    assert "AUD 120.50" in body
    assert "pm_secret_token" not in body

    payment_body = su_client.get(_url(Payment, "change", booking.payment.pk)).content.decode()
    assert "cs_super_secret" not in payment_body


@pytest.mark.django_db
def test_secrets_never_rendered(su_client, world):
    otp = OTPVerification.objects.first()
    assert "hash-never-shown" not in su_client.get(_url(OTPVerification, "change", otp.pk)).content.decode()

    device_page = su_client.get(_url(User, "change", TrustedDevice.objects.first().user_id)).content.decode()
    assert "Office laptop" in device_page
    assert "device-hash" not in device_page

    raw_refresh = world["tokens"]["refresh"]
    token_page = su_client.get(_url(OutstandingToken, "change", world["outstanding"].pk)).content.decode()
    assert raw_refresh not in token_page


# ============================================================
# 2) الأقفال
# ============================================================
LOCKED_MODELS = [
    Booking, BookingServiceSelection, DispatchOffer, BookingQuote,
    BusinessRegistration, InsuranceDocument, Job, JobPhoto, Payment, Payout,
    OTPVerification, AuditLog,
]


@pytest.mark.django_db
@pytest.mark.parametrize("model", LOCKED_MODELS, ids=lambda m: m.__name__)
def test_read_only_models_refuse_add_change_delete(su_client, world, model):
    obj = model._default_manager.first()
    assert su_client.get(_url(model, "add")).status_code == 403
    assert su_client.post(_url(model, "change", obj.pk), {}).status_code == 403
    assert su_client.get(_url(model, "delete", obj.pk)).status_code == 403
    assert model._default_manager.filter(pk=obj.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "model", [ContractorProfile, SupportRequest, PricingConfig, ServiceType, Property, SocialAccount],
    ids=lambda m: m.__name__,
)
def test_models_without_delete(staff_client, world, model):
    obj = model._default_manager.first()
    assert staff_client.get(_url(model, "delete", obj.pk)).status_code == 403


@pytest.mark.django_db
def test_pricing_config_is_a_singleton(su_client, world):
    assert su_client.get(_url(PricingConfig, "add")).status_code == 403


@pytest.mark.django_db
def test_private_models_are_not_registered():
    for model in (JobLocation, ContractorCurrentLocation, AdminLoginChallenge, TrustedDevice):
        assert not admin.site.is_registered(model), model


@pytest.mark.django_db
def test_support_status_change_is_audited_and_resolved_is_final(su_client, world):
    ticket = SupportRequest.objects.first()
    url = _url(SupportRequest, "change", ticket.pk)
    assert su_client.post(url, {"status": SupportStatus.RESOLVED}).status_code == 302
    ticket.refresh_from_db()
    assert ticket.status == SupportStatus.RESOLVED
    entry = AuditLog.objects.get(action="support_request.status")
    assert entry.details == {"from": "SUBMITTED", "to": "RESOLVED"}

    # بعد الحل: الحقل للقراءة، والفعل يرفض إعادة الفتح عبر clean()
    su_client.post(url, {"status": SupportStatus.UNDER_REVIEW})
    su_client.post(
        _url(SupportRequest, "changelist"),
        {"action": "mark_under_review", ACTION_CHECKBOX_NAME: [ticket.pk], "index": 0},
    )
    ticket.refresh_from_db()
    assert ticket.status == SupportStatus.RESOLVED


# ============================================================
# 3) سجل التدقيق
# ============================================================
@pytest.mark.django_db
def test_audit_log_hidden_from_non_superuser_staff(staff_client, world):
    entry = AuditLog.objects.first()
    assert staff_client.get(_url(AuditLog, "changelist")).status_code == 403
    assert staff_client.get(_url(AuditLog, "change", entry.pk)).status_code == 403
    index = staff_client.get(reverse("admin:index")).content.decode()
    assert "Audit log" not in index
    # الموظف يرى بقية اللوحة
    assert staff_client.get(_url(Booking, "changelist")).status_code == 200


# ============================================================
# 4) مراجعة الوثائق عبر طبقة الخدمة
# ============================================================
@pytest.mark.django_db
def test_approve_action_goes_through_service_and_audits(su_client, superuser, world):
    registration = world["registration"]
    response = su_client.post(
        _url(BusinessRegistration, "changelist"),
        {"action": "approve_selected", ACTION_CHECKBOX_NAME: [registration.pk], "index": 0},
    )
    assert response.status_code == 302
    registration.refresh_from_db()
    assert registration.status == VerificationStatus.VERIFIED
    assert registration.reviewed_by == superuser
    assert registration.reviewed_at is not None
    entry = AuditLog.objects.get(action="business_registration.approve")
    assert entry.actor == superuser
    assert entry.target_id == str(registration.pk)


@pytest.mark.django_db
def test_reject_action_requires_a_reason(su_client, superuser, world):
    insurance = world["insurance"]
    url = _url(InsuranceDocument, "changelist")

    # الخطوة الأولى: صفحة وسيطة تطلب السبب، ولا تغيير بعد
    page = su_client.post(url, {"action": "reject_selected", ACTION_CHECKBOX_NAME: [insurance.pk], "index": 0})
    assert page.status_code == 200
    assert "Rejection reason" in page.content.decode()
    insurance.refresh_from_db()
    assert insurance.status == VerificationStatus.PENDING

    # سبب فارغ يُرفض
    blank = su_client.post(
        url,
        {"action": "reject_selected", ACTION_CHECKBOX_NAME: [insurance.pk], "confirm_reject": "yes", "reason": "  "},
    )
    assert blank.status_code == 200
    insurance.refresh_from_db()
    assert insurance.status == VerificationStatus.PENDING
    assert not AuditLog.objects.filter(action="insurance_document.reject").exists()

    done = su_client.post(
        url,
        {
            "action": "reject_selected",
            ACTION_CHECKBOX_NAME: [insurance.pk],
            "confirm_reject": "yes",
            "reason": "Policy number unreadable",
        },
    )
    assert done.status_code == 302
    insurance.refresh_from_db()
    assert insurance.status == VerificationStatus.REJECTED
    assert insurance.rejection_reason == "Policy number unreadable"
    assert insurance.reviewed_by == superuser
    entry = AuditLog.objects.get(action="insurance_document.reject")
    assert entry.details["reason"] == "Policy number unreadable"


@pytest.mark.django_db
def test_review_decisions_stay_final(su_client, world):
    registration = world["registration"]
    url = _url(BusinessRegistration, "changelist")
    su_client.post(url, {"action": "approve_selected", ACTION_CHECKBOX_NAME: [registration.pk], "index": 0})
    su_client.post(
        url,
        {"action": "reject_selected", ACTION_CHECKBOX_NAME: [registration.pk], "confirm_reject": "yes", "reason": "x"},
    )
    registration.refresh_from_db()
    assert registration.status == VerificationStatus.VERIFIED
    assert AuditLog.objects.filter(action__startswith="business_registration.").count() == 1


# ============================================================
# 5) تصدير CSV
# ============================================================
@pytest.mark.django_db
@pytest.mark.parametrize("model", [Booking, Payment, Payout], ids=lambda m: m.__name__)
def test_csv_export(su_client, world, model):
    obj = model._default_manager.first()
    response = su_client.post(
        _url(model, "changelist"),
        {"action": "export_csv", ACTION_CHECKBOX_NAME: [obj.pk], "index": 0},
    )
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    body = b"".join(response.streaming_content).decode()
    lines = body.strip().splitlines()
    assert len(lines) == 2
    assert str(obj.pk) in body or world["booking"].public_reference in body
    for secret in ("pm_secret_token", "Key under the mat", "cs_super_secret"):
        assert secret not in body
    assert AuditLog.objects.filter(action__endswith=".export").exists()


# ============================================================
# 6) حسابات المستخدمين
# ============================================================
@pytest.mark.django_db
def test_non_superuser_cannot_change_roles(staff_client, world):
    customer = world["customer"]
    url = _url(User, "change", customer.pk)
    page = staff_client.get(url)
    assert page.status_code == 200
    body = page.content.decode()
    assert 'name="role"' not in body
    assert 'name="is_superuser"' not in body

    response = staff_client.post(
        url,
        {
            **_hidden_inputs(staff_client, url),
            "phone": customer.phone,
            "full_name": "Renamed",
            "email": customer.email,
            "role": ConfirmedRole.ADMIN,
            "is_superuser": "on",
        },
    )
    assert response.status_code == 302
    customer.refresh_from_db()
    assert customer.role == ConfirmedRole.CUSTOMER
    assert not customer.is_staff and not customer.is_superuser
    assert customer.full_name == "Renamed"


@pytest.mark.django_db
def test_non_superuser_cannot_edit_other_admin_accounts(staff_client, superuser):
    url = _url(User, "change", superuser.pk)
    assert staff_client.post(url, {"phone": superuser.phone, "full_name": "Hacked"}).status_code == 403
    assert staff_client.get(reverse("admin:auth_user_password_change", args=[superuser.pk])).status_code == 403
    superuser.refresh_from_db()
    assert superuser.full_name != "Hacked"


@pytest.mark.django_db
def test_non_superuser_cannot_create_admin_accounts(staff_client):
    staff_client.post(
        _url(User, "add"),
        {
            "phone": "+61400000077",
            "full_name": "New",
            "email": "new@example.com",
            "role": ConfirmedRole.ADMIN,
            "status": UserStatus.ACTIVE,
            "password1": "VeryStrongPass123!",
            "password2": "VeryStrongPass123!",
        },
    )
    created = User.objects.get(phone="+61400000077")
    assert created.role == ConfirmedRole.CUSTOMER
    assert not created.is_staff


@pytest.mark.django_db
def test_superuser_sees_is_superuser_and_is_staff_is_read_only(su_client, world):
    body = su_client.get(_url(User, "change", world["customer"].pk)).content.decode()
    assert 'name="is_superuser"' in body
    assert 'name="role"' in body
    assert 'name="is_staff"' not in body
    assert 'name="status"' not in body  # الحالة عبر الأفعال وحدها
    assert "Failed login attempts" in body


@pytest.mark.django_db
def test_suspend_action_blacklists_tokens_and_audits(staff_client, staff_admin, world):
    customer = world["customer"]
    response = staff_client.post(
        _url(User, "changelist"),
        {"action": "suspend_accounts", ACTION_CHECKBOX_NAME: [customer.pk], "index": 0},
    )
    assert response.status_code == 302
    customer.refresh_from_db()
    assert customer.status == UserStatus.SUSPENDED
    assert BlacklistedToken.objects.filter(token=world["outstanding"]).exists()
    entry = AuditLog.objects.get(action="user.suspend")
    assert entry.actor == staff_admin
    assert entry.details["tokens_revoked"] == 1

    staff_client.post(
        _url(User, "changelist"),
        {"action": "activate_accounts", ACTION_CHECKBOX_NAME: [customer.pk], "index": 0},
    )
    customer.refresh_from_db()
    assert customer.status == UserStatus.ACTIVE
    assert AuditLog.objects.filter(action="user.activate").exists()


@pytest.mark.django_db
def test_suspend_refuses_admin_accounts_for_non_superuser(staff_client, superuser):
    staff_client.post(
        _url(User, "changelist"),
        {"action": "suspend_accounts", ACTION_CHECKBOX_NAME: [superuser.pk], "index": 0},
    )
    superuser.refresh_from_db()
    assert superuser.status == UserStatus.ACTIVE
    assert not AuditLog.objects.filter(action="user.suspend").exists()


@pytest.mark.django_db
def test_superuser_can_suspend_other_admin_but_not_self(su_client, superuser, staff_admin):
    url = _url(User, "changelist")
    su_client.post(url, {"action": "suspend_accounts", ACTION_CHECKBOX_NAME: [staff_admin.pk, superuser.pk], "index": 0})
    staff_admin.refresh_from_db()
    superuser.refresh_from_db()
    assert staff_admin.status == UserStatus.SUSPENDED
    assert superuser.status == UserStatus.ACTIVE


@pytest.mark.django_db
def test_revoke_trusted_devices_action(su_client, superuser, world):
    su_client.post(
        _url(User, "changelist"),
        {"action": "revoke_devices", ACTION_CHECKBOX_NAME: [superuser.pk], "index": 0},
    )
    device = TrustedDevice.objects.get(user=superuser)
    assert device.revoked_at is not None
    assert AuditLog.objects.filter(action="user.revoke_devices").exists()


@pytest.mark.django_db
def test_admin_password_reset_is_temporary_and_revokes_sessions(su_client, staff_admin):
    OutstandingToken.objects.create(
        user=staff_admin, jti="staff-jti", token="t", expires_at=timezone.now() + timedelta(days=1)
    )
    response = su_client.post(
        reverse("admin:auth_user_password_change", args=[staff_admin.pk]),
        {"password1": "BrandNewPass456!", "password2": "BrandNewPass456!"},
    )
    assert response.status_code == 302
    staff_admin.refresh_from_db()
    assert staff_admin.check_password("BrandNewPass456!")
    assert staff_admin.must_change_password is True
    assert staff_admin.password_changed_at is not None
    assert BlacklistedToken.objects.filter(token__jti="staff-jti").exists()
    assert AuditLog.objects.filter(action="user.password_reset").exists()


@pytest.mark.django_db
def test_user_delete_is_superuser_only(staff_client, su_client, superuser, world):
    customer = world["customer"]
    assert staff_client.get(_url(User, "delete", customer.pk)).status_code == 403
    assert su_client.get(_url(User, "delete", superuser.pk)).status_code == 403


# ============================================================
# 7) الكتالوج عبر طبقة الخدمة
# ============================================================
@pytest.mark.django_db
def test_service_type_edit_goes_through_service_and_audits(su_client, world):
    service = ServiceType.objects.first()
    response = su_client.post(
        _url(ServiceType, "change", service.pk),
        {"name": service.name, "description": "", "is_active": "on", "room_price": "25.00", "base_price": "50.00"},
    )
    assert response.status_code == 302
    service.refresh_from_db()
    assert service.room_price == Decimal("25.00")
    assert AuditLog.objects.filter(action="service_type.update").exists()

    su_client.post(
        _url(ServiceType, "changelist"),
        {"action": "deactivate_selected", ACTION_CHECKBOX_NAME: [service.pk], "index": 0},
    )
    service.refresh_from_db()
    assert service.is_active is False


@pytest.mark.django_db
def test_service_type_add_goes_through_service(su_client):
    response = su_client.post(
        _url(ServiceType, "add"),
        {"name": "Deep clean", "description": "", "is_active": "on", "room_price": "30.00", "base_price": "60.00"},
    )
    assert response.status_code == 302
    assert ServiceType.objects.filter(name="Deep clean").exists()
    assert AuditLog.objects.filter(action="service_type.create").exists()


@pytest.mark.django_db
def test_property_with_active_booking_cannot_be_deactivated(su_client, world):
    booking = world["booking"]
    Job.objects.filter(booking=booking).update(status=JobStatus.IN_PROGRESS)
    prop = booking.property
    url = _url(Property, "change", prop.pk)
    response = su_client.post(
        url,
        {
            **_hidden_inputs(su_client, url),
            "label": prop.label,
            "property_type": prop.property_type,
            "address-0-street_address": prop.address.street_address,
            "address-0-suburb": prop.address.suburb,
            "address-0-state": prop.address.state,
            "address-0-postcode": prop.address.postcode,
        },
    )
    assert response.status_code == 200  # أُعيد النموذج بخطأ
    assert "active bookings" in response.content.decode()
    prop.refresh_from_db()
    assert prop.is_active is True

    # بلا حجز نشط يمر التعطيل
    Job.objects.filter(booking=booking).update(status=JobStatus.COMPLETED)
    response = su_client.post(
        url,
        {
            **_hidden_inputs(su_client, url),
            "label": prop.label,
            "property_type": prop.property_type,
            "address-0-street_address": prop.address.street_address,
            "address-0-suburb": prop.address.suburb,
            "address-0-state": prop.address.state,
            "address-0-postcode": prop.address.postcode,
        },
    )
    assert response.status_code == 302
    prop.refresh_from_db()
    assert prop.is_active is False


# ============================================================
# 8) لا N+1 في القوائم
# ============================================================
@pytest.mark.django_db
def test_changelists_do_not_query_per_row(su_client, world):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    models = [
        Booking, Payment, Payout, Job, JobPhoto, DispatchOffer, BookingQuote, ContractorProfile,
        BusinessRegistration, InsuranceDocument, Property, SupportRequest, User, AuditLog,
    ]

    def counts():
        result = {}
        for model in models:
            with CaptureQueriesContext(connection) as ctx:
                assert su_client.get(_url(model, "changelist")).status_code == 200
            result[model] = len(ctx.captured_queries)
        return result

    before = counts()

    # نسخ إضافية من كل سلسلة: عميل وعقار وحجز ودفعة ومهمة ودفعة مقاول...
    now = timezone.now()
    for i in range(4):
        customer = User.objects.create_user(phone=f"+6141000000{i}")
        worker = User.objects.create_user(phone=f"+6142000000{i}", role=ConfirmedRole.CONTRACTOR)
        profile = ContractorProfile.objects.create(user=worker, business_name=f"Co {i}")
        BusinessRegistration.objects.create(contractor=profile, abn=VALID_ABN, business_name=f"Co {i}")
        InsuranceDocument.objects.create(
            contractor=profile, document_reference=f"P{i}", expiry_date=timezone.localdate() + timedelta(days=30)
        )
        prop = Property.objects.create(owner=customer, property_type="HOUSE")
        PropertyAddress.objects.create(
            property=prop, street_address="2 St", suburb="Sydney", state="NSW", postcode="2000"
        )
        BookingQuote.objects.create(
            customer=customer, property=prop, service_snapshot=[], services_total=1,
            maximum_total=2, pricing_version=1, expires_at=now,
        )
        booking = Booking.objects.create(
            customer=customer, property=prop, assigned_contractor=profile, computed_price=Decimal("10")
        )
        DispatchOffer.objects.create(booking=booking, contractor=profile, expires_at=now)
        Payment.objects.create(booking=booking, amount=Decimal("10"), method=PaymentMethod.CARD)
        job = Job.objects.create(booking=booking)
        JobPhoto.objects.create(job=job, photo_type=PhotoType.AFTER, storage_key="k", uploaded_by=worker)
        Payout.objects.create(booking=booking, contractor=worker, amount=Decimal("10"))
        SupportRequest.objects.create(user=customer, category=SupportCategory.OTHER, message="m", booking=booking)
        record(customer, "test.more", target=booking)

    after = counts()
    for model in models:
        assert after[model] == before[model], (model, before[model], after[model])
