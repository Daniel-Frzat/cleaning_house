"""
Back-office — سجل تحقق المقاول، وتدقيق مراجعات التحقق وطفرات الكتالوج.
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.contractors.models import BusinessRegistration, InsuranceDocument, VerificationStatus
from apps.contractors.services import verification as vsvc
from tests.backoffice import auth, make_admin, make_contractor, make_user

VALID_ABN = "51824753556"


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin(db):
    return make_admin(email="reviewer@example.com")


def send(client, method, url, body, **headers):
    return getattr(client, method)(
        url, data=json.dumps(body), content_type="application/json", **headers
    )


def registration(profile, status=VerificationStatus.PENDING, **extra):
    return BusinessRegistration.objects.create(
        contractor=profile, abn=VALID_ABN, business_name="Sparkle Co", status=status, **extra
    )


def insurance(profile, status=VerificationStatus.PENDING, days=365):
    return InsuranceDocument.objects.create(
        contractor=profile,
        document_reference="POL-1",
        expiry_date=timezone.localdate() + timedelta(days=days),
        status=status,
    )


# ============================================================
# سجل التحقق الكامل
# ============================================================
@pytest.mark.django_db
def test_verification_history_lists_every_submission(client, admin):
    _, profile = make_contractor()
    _, other = make_contractor()
    old = registration(profile, VerificationStatus.REJECTED, rejection_reason="Wrong name")
    old.created_at = timezone.now() - timedelta(days=3)
    old.save()
    current = registration(profile, VerificationStatus.VERIFIED)
    ins = insurance(profile, VerificationStatus.VERIFIED)
    registration(other)  # سجل مقاول آخر لا يظهر

    r = client.get(f"/api/admin/contractors/{profile.id}/verifications", **auth(admin))

    assert r.status_code == 200, r.content
    body = r.json()
    assert body["contractor_id"] == str(profile.id)
    assert body["eligible"] is True
    assert [x["id"] for x in body["business_registrations"]] == [str(current.id), str(old.id)]
    assert body["business_registrations"][1]["rejection_reason"] == "Wrong name"
    assert [x["id"] for x in body["insurance_documents"]] == [str(ins.id)]


@pytest.mark.django_db
def test_verification_history_empty(client, admin):
    _, profile = make_contractor()
    body = client.get(f"/api/admin/contractors/{profile.id}/verifications", **auth(admin)).json()
    assert body == {
        "contractor_id": str(profile.id),
        "eligible": False,
        "business_registrations": [],
        "insurance_documents": [],
    }


@pytest.mark.django_db
def test_existing_contractor_admin_shapes_unchanged(client, admin):
    _, profile = make_contractor()
    r = client.get("/api/admin/contractors", **auth(admin))
    assert r.status_code == 200
    assert isinstance(r.json(), list)  # قائمة عارية كما كانت — لا {"count","items"}
    r = client.get("/api/admin/verifications/pending", **auth(admin))
    assert set(r.json()) == {"business_registrations", "insurance_documents"}


# ============================================================
# تدقيق المراجعات
# ============================================================
@pytest.mark.django_db
def test_business_registration_review_is_audited(client, admin):
    _, profile = make_contractor()
    reg = registration(profile)

    r = send(
        client,
        "patch",
        f"/api/admin/business-registration/{reg.id}",
        {"status": "REJECTED", "rejection_reason": "ABN mismatch"},
        REMOTE_ADDR="10.9.8.7",
        **auth(admin),
    )

    assert r.status_code == 200, r.content
    entry = AuditLog.objects.get(action="business_registration.review")
    assert entry.actor == admin
    assert entry.actor_label == "reviewer@example.com"
    assert entry.target_type == "BusinessRegistration"
    assert entry.target_id == str(reg.id)
    assert entry.details == {
        "from": "PENDING",
        "to": "REJECTED",
        "contractor_id": str(profile.id),
        "rejection_reason": "ABN mismatch",
    }
    assert entry.ip_address == "10.9.8.7"


@pytest.mark.django_db
def test_insurance_review_is_audited(client, admin):
    _, profile = make_contractor()
    doc = insurance(profile)

    r = send(client, "patch", f"/api/admin/insurance/{doc.id}", {"status": "VERIFIED"}, **auth(admin))

    assert r.status_code == 200, r.content
    entry = AuditLog.objects.get(action="insurance_document.review")
    assert entry.details["to"] == "VERIFIED"
    assert entry.details["rejection_reason"] is None


@pytest.mark.django_db
def test_refused_review_leaves_no_audit(client, admin):
    _, profile = make_contractor()
    reg = registration(profile, VerificationStatus.VERIFIED)

    r = send(
        client, "patch", f"/api/admin/business-registration/{reg.id}", {"status": "REJECTED", "rejection_reason": "x"}, **auth(admin)
    )

    assert r.status_code == 409
    assert not AuditLog.objects.exists()


@pytest.mark.django_db
def test_review_service_still_works_without_request(admin):
    """📌 request اختياري — المستدعون القدامى (بلا request) لا ينكسرون."""
    _, profile = make_contractor()
    reg = registration(profile)
    vsvc.review_business_registration(admin, reg.id, VerificationStatus.VERIFIED)
    entry = AuditLog.objects.get(action="business_registration.review")
    assert entry.ip_address is None


# ============================================================
# تدقيق الكتالوج والتسعير
# ============================================================
SERVICE = {
    "name": "Deep Clean",
    "description": "Everything",
    "room_price": "50.00",
    "base_price": "90.00",
}


@pytest.mark.django_db
def test_catalog_mutations_are_audited(client, admin):
    h = auth(admin)
    r = send(client, "post", "/api/admin/services", SERVICE, **h)
    assert r.status_code in (200, 201), r.content
    service_id = r.json()["id"]

    created = AuditLog.objects.get(action="service_type.create")
    assert created.target_type == "ServiceType"
    assert created.target_id == service_id
    assert created.details["room_price"] == "50.00"

    r = send(client, "patch", f"/api/admin/services/{service_id}", {"room_price": "55.00"}, **h)
    assert r.status_code == 200, r.content
    updated = AuditLog.objects.get(action="service_type.update")
    assert updated.details == {"changes": {"room_price": {"from": "50.00", "to": "55.00"}}}

    r = client.delete(f"/api/admin/services/{service_id}", **h)
    assert r.status_code in (200, 204), r.content
    deactivated = AuditLog.objects.get(action="service_type.deactivate")
    assert deactivated.details == {"was_active": True}

    r = send(client, "patch", "/api/admin/pricing-config", {"price_per_km": "2.50"}, **h)
    assert r.status_code == 200, r.content
    pricing = AuditLog.objects.get(action="pricing_config.update")
    assert pricing.target_type == "PricingConfig"
    assert pricing.target_id == "1"
    assert pricing.details["price_per_km"]["to"] == "2.50"


@pytest.mark.django_db
def test_rejected_catalog_mutation_leaves_no_audit(client):
    customer = make_user()
    r = send(client, "post", "/api/admin/services", SERVICE, **auth(customer))
    assert r.status_code == 403
    assert not AuditLog.objects.exists()
