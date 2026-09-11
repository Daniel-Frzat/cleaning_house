"""
Contractor Verification Tests — Contractors Domain (Change Set §6، Infra §5)

يغطي: التحقق الشكلي من الـABN، الملكية (المقاول يرى مستنداته وحده)،
دورة الاعتماد/الرفض الإدارية، إلزامية سبب الرفض، وصحة is_contractor_eligible
عبر كل التوليفات.
"""

import json
import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user
from apps.contractors.models import (
    BusinessRegistration,
    ContractorProfile,
    InsuranceDocument,
    VerificationStatus,
)
from apps.contractors.services import verification as vsvc

VALID_ABN = "12345678901"
VALID_REGISTRATION = {"abn": VALID_ABN, "business_name": "Sparkle Co"}


# ------------------------------------------------------------
# أدوات
# ------------------------------------------------------------
@pytest.fixture
def client():
    return Client()


def make_user(phone, role=ConfirmedRole.CONTRACTOR):
    return User.objects.create_user(phone=phone, role=role)


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


def post(client, url, payload, **extra):
    return client.post(
        url, data=json.dumps(payload), content_type="application/json", **extra
    )


def patch(client, url, payload, **extra):
    return client.patch(
        url, data=json.dumps(payload), content_type="application/json", **extra
    )


def today():
    return timezone.localdate()


def future_date(days=365):
    return (today() + timedelta(days=days)).isoformat()


@pytest.fixture
def contractor_a(db):
    user = make_user("+61400005001")
    ContractorProfile.objects.create(user=user, business_name="A Co")
    return user


@pytest.fixture
def contractor_b(db):
    user = make_user("+61400005002")
    ContractorProfile.objects.create(user=user, business_name="B Co")
    return user


@pytest.fixture
def customer(db):
    return make_user("+61400005003", role=ConfirmedRole.CUSTOMER)


@pytest.fixture
def admin_user(db):
    return make_user("+61400005004", role=ConfirmedRole.ADMIN)


def profile_of(user):
    return ContractorProfile.objects.get(user=user)


def submit_registration(client, user, payload=None):
    r = post(
        client,
        "/api/contractor/business-registration",
        payload or VALID_REGISTRATION,
        **auth(user),
    )
    assert r.status_code == 201, r.content
    return r.json()


def submit_insurance(client, user, expiry=None):
    r = post(
        client,
        "/api/contractor/insurance",
        {"document_reference": "POL-123", "expiry_date": expiry or future_date()},
        **auth(user),
    )
    assert r.status_code == 201, r.content
    return r.json()


# ============================================================
# 1) التحقق الشكلي من الـABN
# ============================================================
@pytest.mark.django_db
def test_valid_abn_is_accepted(client, contractor_a):
    body = submit_registration(client, contractor_a)

    assert body["abn"] == VALID_ABN
    assert body["business_name"] == "Sparkle Co"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad_abn",
    [
        "1234567890",      # 10 أرقام
        "123456789012",    # 12 رقمًا
        "abcdefghijk",     # حروف
        "1234567890a",     # رقم وحرف
        "",                # فارغ
        "123 456 789 01",  # فراغات
    ],
)
def test_invalid_abn_is_rejected(client, contractor_a, bad_abn):
    r = post(
        client,
        "/api/contractor/business-registration",
        {**VALID_REGISTRATION, "abn": bad_abn},
        **auth(contractor_a),
    )

    assert r.status_code == 422, r.content
    assert BusinessRegistration.objects.count() == 0


@pytest.mark.django_db
def test_abn_validation_is_format_only_not_a_registry_lookup(client, contractor_a):
    """
    ⚠️ 11 رقمًا يكفي — لا فحص checksum ولا استعلام سجل حكومي (§6).
       رقم غير حقيقي تمامًا يُقبل شكلًا، والمراجعة البشرية هي الحَكَم.
    """
    body = submit_registration(
        client, contractor_a, {**VALID_REGISTRATION, "abn": "00000000000"}
    )

    assert body["abn"] == "00000000000"
    assert body["status"] == VerificationStatus.PENDING


@pytest.mark.django_db
def test_submission_starts_pending_with_no_reviewer(client, contractor_a):
    body = submit_registration(client, contractor_a)

    assert body["status"] == VerificationStatus.PENDING
    assert body["reviewed_by_id"] is None
    assert body["reviewed_at"] is None
    assert body["rejection_reason"] is None


@pytest.mark.django_db
def test_insurance_submission_starts_pending(client, contractor_a):
    body = submit_insurance(client, contractor_a)

    assert body["status"] == VerificationStatus.PENDING
    assert body["document_reference"] == "POL-123"
    assert body["reviewed_by_id"] is None


@pytest.mark.django_db
def test_multiple_submissions_allowed_over_time(client, contractor_a):
    """إعادة التقديم بعد رفض، وتجديد التأمين — التعدد مسموح عمدًا."""
    submit_registration(client, contractor_a)
    submit_registration(client, contractor_a, {**VALID_REGISTRATION, "abn": "99999999999"})
    submit_insurance(client, contractor_a)
    submit_insurance(client, contractor_a)

    assert BusinessRegistration.objects.filter(contractor=profile_of(contractor_a)).count() == 2
    assert InsuranceDocument.objects.filter(contractor=profile_of(contractor_a)).count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("role", [ConfirmedRole.CUSTOMER, ConfirmedRole.ADMIN])
def test_non_contractor_cannot_submit(client, db, role):
    user = make_user("+61400005100", role=role)

    reg = post(client, "/api/contractor/business-registration", VALID_REGISTRATION, **auth(user))
    ins = post(
        client,
        "/api/contractor/insurance",
        {"document_reference": "X", "expiry_date": future_date()},
        **auth(user),
    )

    assert reg.status_code == 403, reg.content
    assert ins.status_code == 403, ins.content
    assert BusinessRegistration.objects.count() == 0


@pytest.mark.django_db
def test_contractor_without_profile_gets_404(client, db):
    """لا مستند بلا ملف مقاول."""
    user = make_user("+61400005101")

    r = post(client, "/api/contractor/business-registration", VALID_REGISTRATION, **auth(user))

    assert r.status_code == 404, r.content


# ============================================================
# 2) الملكية — كل مقاول يرى مستنداته وحده
# ============================================================
@pytest.mark.django_db
def test_contractor_lists_only_own_registrations(client, contractor_a, contractor_b):
    submit_registration(client, contractor_a, {**VALID_REGISTRATION, "business_name": "A Co"})
    submit_registration(client, contractor_b, {**VALID_REGISTRATION, "business_name": "B Co"})

    r = client.get("/api/contractor/business-registration", **auth(contractor_b))

    assert r.status_code == 200
    names = {x["business_name"] for x in r.json()}
    assert names == {"B Co"}


@pytest.mark.django_db
def test_contractor_lists_only_own_insurance(client, contractor_a, contractor_b):
    submit_insurance(client, contractor_a)
    submit_insurance(client, contractor_b)

    r = client.get("/api/contractor/insurance", **auth(contractor_a))

    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["contractor_id"] == str(profile_of(contractor_a).id)


@pytest.mark.django_db
def test_submission_routes_take_no_contractor_id(client, contractor_a, contractor_b):
    """
    🔒 المسارات ذاتية: لا معرّف يُمرَّر، فلا سبيل للتقديم نيابةً عن غيرك.
    """
    post(
        client,
        "/api/contractor/business-registration",
        {**VALID_REGISTRATION, "contractor_id": str(profile_of(contractor_b).id)},
        **auth(contractor_a),
    )

    # نُسب إلى المقاول صاحب التوكن، لا إلى المعرّف المُرسل
    assert BusinessRegistration.objects.filter(contractor=profile_of(contractor_a)).count() == 1
    assert BusinessRegistration.objects.filter(contractor=profile_of(contractor_b)).count() == 0


@pytest.mark.django_db
def test_contractor_cannot_review_own_submission(client, contractor_a):
    """المقاول لا يعتمد نفسه — المراجعة إدارية حصرًا."""
    reg = submit_registration(client, contractor_a)

    r = patch(
        client,
        f"/api/admin/business-registration/{reg['id']}",
        {"status": "VERIFIED"},
        **auth(contractor_a),
    )

    assert r.status_code == 403, r.content
    assert BusinessRegistration.objects.get(pk=reg["id"]).status == VerificationStatus.PENDING


# ============================================================
# 3) دورة المراجعة الإدارية
# ============================================================
@pytest.mark.django_db
def test_admin_approves_business_registration(client, contractor_a, admin_user):
    reg = submit_registration(client, contractor_a)

    r = patch(
        client,
        f"/api/admin/business-registration/{reg['id']}",
        {"status": "VERIFIED"},
        **auth(admin_user),
    )

    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == VerificationStatus.VERIFIED
    assert body["reviewed_by_id"] == str(admin_user.id)
    assert body["reviewed_at"] is not None
    assert body["rejection_reason"] is None


@pytest.mark.django_db
def test_admin_rejects_with_reason(client, contractor_a, admin_user):
    reg = submit_registration(client, contractor_a)

    r = patch(
        client,
        f"/api/admin/business-registration/{reg['id']}",
        {"status": "REJECTED", "rejection_reason": "ABN does not match business name."},
        **auth(admin_user),
    )

    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == VerificationStatus.REJECTED
    assert body["rejection_reason"] == "ABN does not match business name."
    assert body["reviewed_by_id"] == str(admin_user.id)


@pytest.mark.django_db
def test_admin_approves_and_rejects_insurance(client, contractor_a, admin_user):
    approved = submit_insurance(client, contractor_a)
    rejected = submit_insurance(client, contractor_a)

    ok = patch(
        client, f"/api/admin/insurance/{approved['id']}", {"status": "VERIFIED"}, **auth(admin_user)
    )
    no = patch(
        client,
        f"/api/admin/insurance/{rejected['id']}",
        {"status": "REJECTED", "rejection_reason": "Policy expired on submission."},
        **auth(admin_user),
    )

    assert ok.status_code == 200 and ok.json()["status"] == VerificationStatus.VERIFIED
    assert no.status_code == 200 and no.json()["status"] == VerificationStatus.REJECTED


@pytest.mark.django_db
def test_admin_lists_pending_of_both_kinds(client, contractor_a, contractor_b, admin_user):
    submit_registration(client, contractor_a)
    submit_insurance(client, contractor_b)
    reviewed = submit_registration(client, contractor_b, {**VALID_REGISTRATION, "abn": "99999999999"})
    patch(
        client,
        f"/api/admin/business-registration/{reviewed['id']}",
        {"status": "VERIFIED"},
        **auth(admin_user),
    )

    r = client.get("/api/admin/verifications/pending", **auth(admin_user))

    assert r.status_code == 200, r.content
    body = r.json()
    assert len(body["business_registrations"]) == 1  # المعتمد لم يعد معلّقًا
    assert len(body["insurance_documents"]) == 1


@pytest.mark.django_db
def test_pending_list_is_empty_when_nothing_awaits(client, admin_user):
    r = client.get("/api/admin/verifications/pending", **auth(admin_user))

    assert r.status_code == 200
    assert r.json() == {"business_registrations": [], "insurance_documents": []}


@pytest.mark.django_db
def test_review_unknown_id_returns_404(client, admin_user):
    r = patch(
        client,
        f"/api/admin/business-registration/{uuid.uuid4()}",
        {"status": "VERIFIED"},
        **auth(admin_user),
    )

    assert r.status_code == 404


@pytest.mark.django_db
def test_review_cannot_set_status_back_to_pending(client, contractor_a, admin_user):
    """PENDING ليست قرار مراجعة — لا يُعاد إليها سجل."""
    reg = submit_registration(client, contractor_a)

    r = patch(
        client,
        f"/api/admin/business-registration/{reg['id']}",
        {"status": "PENDING"},
        **auth(admin_user),
    )

    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_approval_clears_any_previous_rejection_reason(client, contractor_a, admin_user):
    """لا يبقى تعليل رفض على سجل صار معتمدًا."""
    reg = submit_registration(client, contractor_a)
    patch(
        client,
        f"/api/admin/business-registration/{reg['id']}",
        {"status": "REJECTED", "rejection_reason": "Wrong ABN."},
        **auth(admin_user),
    )

    r = patch(
        client,
        f"/api/admin/business-registration/{reg['id']}",
        {"status": "VERIFIED"},
        **auth(admin_user),
    )

    assert r.status_code == 200
    assert r.json()["rejection_reason"] is None


# ============================================================
# 4) الرفض يتطلب سببًا — 400
# ============================================================
@pytest.mark.django_db
def test_rejecting_without_reason_returns_400(client, contractor_a, admin_user):
    """
    🔒 المواصفة تنص على 400 صراحةً لهذه الحالة (وليس 422 كعرف المشروع).
    """
    reg = submit_registration(client, contractor_a)

    r = patch(
        client,
        f"/api/admin/business-registration/{reg['id']}",
        {"status": "REJECTED"},
        **auth(admin_user),
    )

    assert r.status_code == 400, r.content
    assert r.json()["code"] == "rejection_reason_required"
    # لم يُرفض السجل فعليًا
    assert BusinessRegistration.objects.get(pk=reg["id"]).status == VerificationStatus.PENDING


@pytest.mark.django_db
@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_rejecting_with_blank_reason_returns_400(client, contractor_a, admin_user, blank):
    """السبب الفارغ أو المسافات البيضاء ليس سببًا."""
    reg = submit_registration(client, contractor_a)

    r = patch(
        client,
        f"/api/admin/business-registration/{reg['id']}",
        {"status": "REJECTED", "rejection_reason": blank},
        **auth(admin_user),
    )

    assert r.status_code == 400, r.content
    assert BusinessRegistration.objects.get(pk=reg["id"]).status == VerificationStatus.PENDING


@pytest.mark.django_db
def test_rejecting_insurance_without_reason_returns_400(client, contractor_a, admin_user):
    ins = submit_insurance(client, contractor_a)

    r = patch(
        client, f"/api/admin/insurance/{ins['id']}", {"status": "REJECTED"}, **auth(admin_user)
    )

    assert r.status_code == 400, r.content
    assert InsuranceDocument.objects.get(pk=ins["id"]).status == VerificationStatus.PENDING


@pytest.mark.django_db
def test_rejection_reason_enforced_at_model_level(db, contractor_a):
    """
    🔒 الإنفاذ ليس في الـAPI وحدها: أي مسار كتابة يمر بـfull_clean يُمنع.
    """
    from django.core.exceptions import ValidationError

    registration = BusinessRegistration(
        contractor=profile_of(contractor_a),
        abn=VALID_ABN,
        business_name="X",
        status=VerificationStatus.REJECTED,
    )

    with pytest.raises(ValidationError):
        registration.full_clean()


@pytest.mark.django_db
def test_rejection_reason_enforced_in_service_layer(db, contractor_a, admin_user):
    reg = vsvc.submit_business_registration(contractor_a, VALID_ABN, "X")

    with pytest.raises(vsvc.MissingRejectionReasonError):
        vsvc.review_business_registration(admin_user, reg.id, VerificationStatus.REJECTED)


# ============================================================
# 5) صلاحيات المراجعة — ADMIN فقط
# ============================================================
@pytest.mark.django_db
def test_non_admin_forbidden_on_all_review_endpoints(
    client, contractor_a, contractor_b, customer
):
    reg = submit_registration(client, contractor_a)
    ins = submit_insurance(client, contractor_a)

    for label, actor in (("contractor", contractor_b), ("customer", customer)):
        calls = [
            client.get("/api/admin/verifications/pending", **auth(actor)),
            patch(
                client,
                f"/api/admin/business-registration/{reg['id']}",
                {"status": "VERIFIED"},
                **auth(actor),
            ),
            patch(
                client, f"/api/admin/insurance/{ins['id']}", {"status": "VERIFIED"}, **auth(actor)
            ),
        ]
        for r in calls:
            assert r.status_code == 403, f"{label} -> {r.status_code}"
            assert r.json()["code"] == "admin_role_required"

    # لا شيء تغيّر
    assert BusinessRegistration.objects.get(pk=reg["id"]).status == VerificationStatus.PENDING
    assert InsuranceDocument.objects.get(pk=ins["id"]).status == VerificationStatus.PENDING


@pytest.mark.django_db
def test_unauthenticated_requests_return_401(client, contractor_a):
    reg = submit_registration(client, contractor_a)

    assert post(client, "/api/contractor/business-registration", VALID_REGISTRATION).status_code == 401
    assert client.get("/api/contractor/business-registration").status_code == 401
    assert client.get("/api/contractor/insurance").status_code == 401
    assert client.get("/api/admin/verifications/pending").status_code == 401
    assert patch(
        client, f"/api/admin/business-registration/{reg['id']}", {"status": "VERIFIED"}
    ).status_code == 401


# ============================================================
# 6) is_contractor_eligible — كل التوليفات
# ============================================================
def _verify(admin_user, instance_kind, instance):
    if instance_kind == "reg":
        return vsvc.review_business_registration(
            admin_user, instance.id, VerificationStatus.VERIFIED
        )
    return vsvc.review_insurance_document(
        admin_user, instance.id, VerificationStatus.VERIFIED
    )


@pytest.mark.django_db
def test_eligible_false_when_no_records(db, contractor_a):
    assert vsvc.is_contractor_eligible(profile_of(contractor_a)) is False


@pytest.mark.django_db
def test_eligible_false_when_both_pending(db, contractor_a):
    vsvc.submit_business_registration(contractor_a, VALID_ABN, "X")
    vsvc.submit_insurance_document(contractor_a, "POL-1", today() + timedelta(days=30))

    assert vsvc.is_contractor_eligible(profile_of(contractor_a)) is False


@pytest.mark.django_db
def test_eligible_false_when_only_registration_verified(db, contractor_a, admin_user):
    reg = vsvc.submit_business_registration(contractor_a, VALID_ABN, "X")
    _verify(admin_user, "reg", reg)
    vsvc.submit_insurance_document(contractor_a, "POL-1", today() + timedelta(days=30))

    assert vsvc.is_contractor_eligible(profile_of(contractor_a)) is False


@pytest.mark.django_db
def test_eligible_false_when_only_insurance_verified(db, contractor_a, admin_user):
    vsvc.submit_business_registration(contractor_a, VALID_ABN, "X")
    ins = vsvc.submit_insurance_document(contractor_a, "POL-1", today() + timedelta(days=30))
    _verify(admin_user, "ins", ins)

    assert vsvc.is_contractor_eligible(profile_of(contractor_a)) is False


@pytest.mark.django_db
def test_eligible_false_when_insurance_verified_but_expired(db, contractor_a, admin_user):
    """
    ⚠️ الحالة المحورية: الوثيقة معتمدة لكنها منتهية — غير مؤهَّل.
       الانتهاء يسري لحظيًا بلا أي مهمة خلفية (Infra §5 بند مفتوح).
    """
    reg = vsvc.submit_business_registration(contractor_a, VALID_ABN, "X")
    _verify(admin_user, "reg", reg)
    ins = vsvc.submit_insurance_document(contractor_a, "POL-1", today() - timedelta(days=1))
    _verify(admin_user, "ins", ins)

    assert vsvc.is_contractor_eligible(profile_of(contractor_a)) is False


@pytest.mark.django_db
def test_eligible_true_when_both_verified_and_insurance_current(
    db, contractor_a, admin_user
):
    reg = vsvc.submit_business_registration(contractor_a, VALID_ABN, "X")
    _verify(admin_user, "reg", reg)
    ins = vsvc.submit_insurance_document(contractor_a, "POL-1", today() + timedelta(days=30))
    _verify(admin_user, "ins", ins)

    assert vsvc.is_contractor_eligible(profile_of(contractor_a)) is True


@pytest.mark.django_db
def test_eligible_true_when_insurance_expires_today(db, contractor_a, admin_user):
    """الحد: expiry_date >= today — الوثيقة المنتهية اليوم ما زالت سارية."""
    reg = vsvc.submit_business_registration(contractor_a, VALID_ABN, "X")
    _verify(admin_user, "reg", reg)
    ins = vsvc.submit_insurance_document(contractor_a, "POL-1", today())
    _verify(admin_user, "ins", ins)

    assert vsvc.is_contractor_eligible(profile_of(contractor_a)) is True


@pytest.mark.django_db
def test_eligible_false_when_registration_rejected(db, contractor_a, admin_user):
    reg = vsvc.submit_business_registration(contractor_a, VALID_ABN, "X")
    vsvc.review_business_registration(
        admin_user, reg.id, VerificationStatus.REJECTED, "Bad ABN."
    )
    ins = vsvc.submit_insurance_document(contractor_a, "POL-1", today() + timedelta(days=30))
    _verify(admin_user, "ins", ins)

    assert vsvc.is_contractor_eligible(profile_of(contractor_a)) is False


@pytest.mark.django_db
def test_eligible_false_when_insurance_rejected(db, contractor_a, admin_user):
    reg = vsvc.submit_business_registration(contractor_a, VALID_ABN, "X")
    _verify(admin_user, "reg", reg)
    ins = vsvc.submit_insurance_document(contractor_a, "POL-1", today() + timedelta(days=30))
    vsvc.review_insurance_document(
        admin_user, ins.id, VerificationStatus.REJECTED, "Policy invalid."
    )

    assert vsvc.is_contractor_eligible(profile_of(contractor_a)) is False


@pytest.mark.django_db
def test_latest_record_wins_newer_rejection_revokes_eligibility(
    db, contractor_a, admin_user
):
    """
    ⚠️ "الأحدث" يحكم: سجل مرفوض أحدث يُبطل الأهلية رغم وجود معتمد أقدم.
    """
    old = vsvc.submit_business_registration(contractor_a, VALID_ABN, "Old")
    _verify(admin_user, "reg", old)
    ins = vsvc.submit_insurance_document(contractor_a, "POL-1", today() + timedelta(days=30))
    _verify(admin_user, "ins", ins)
    assert vsvc.is_contractor_eligible(profile_of(contractor_a)) is True

    new = vsvc.submit_business_registration(contractor_a, "99999999999", "New")
    vsvc.review_business_registration(
        admin_user, new.id, VerificationStatus.REJECTED, "Mismatch."
    )

    assert vsvc.is_contractor_eligible(profile_of(contractor_a)) is False


@pytest.mark.django_db
def test_latest_record_wins_renewal_restores_eligibility(db, contractor_a, admin_user):
    """تجديد التأمين بعد الانتهاء يعيد الأهلية."""
    reg = vsvc.submit_business_registration(contractor_a, VALID_ABN, "X")
    _verify(admin_user, "reg", reg)
    expired = vsvc.submit_insurance_document(contractor_a, "OLD", today() - timedelta(days=5))
    _verify(admin_user, "ins", expired)
    assert vsvc.is_contractor_eligible(profile_of(contractor_a)) is False

    renewed = vsvc.submit_insurance_document(contractor_a, "NEW", today() + timedelta(days=365))
    _verify(admin_user, "ins", renewed)

    assert vsvc.is_contractor_eligible(profile_of(contractor_a)) is True


@pytest.mark.django_db
def test_eligibility_is_computed_not_stored(db, contractor_a):
    """
    ⚠️ الأهلية دالة محسوبة لا حقل مخزَّن — لو خُزِّنت لكذبت في اليوم
       التالي لانتهاء التأمين (لا مهمة خلفية تحدّثها — بند مفتوح).
    """
    field_names = {f.name for f in ContractorProfile._meta.get_fields()}

    assert "is_eligible" not in field_names
    assert "eligible" not in field_names
    assert callable(vsvc.is_contractor_eligible)


@pytest.mark.django_db
def test_eligibility_handles_none_profile(db):
    assert vsvc.is_contractor_eligible(None) is False


# ============================================================
# 7) حدود النطاق
# ============================================================
def test_no_business_registry_adapter_is_wired():
    """
    ⚠️ المرحلة يدوية بالكامل: الـadapter التجريدي يبقى دون استخدام،
       ولا أي استيراد لسجل حكومي أو خدمة تحقق خارجية.
    """
    import ast
    import inspect

    from apps.contractors import models
    from apps.contractors.api import admin_contractors, profile as profile_api
    from apps.contractors.services import verification

    for mod in (models, verification, profile_api, admin_contractors):
        tree = ast.parse(inspect.getsource(mod))

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)

        for name in imported:
            low = name.lower()
            assert "adapter" not in low, f"{mod.__name__} imports adapter: {name}"
            assert "registry" not in low, f"{mod.__name__} imports registry: {name}"


def test_no_reverification_scheduling_in_this_phase():
    """
    ⚠️ لا جدولة إعادة تحقق ولا مهمة Celery لانتهاء الصلاحية
       (Infra §5 — "policy إعادة التحقق" بند مفتوح صراحةً).
    """
    import ast
    import inspect

    from apps.contractors.services import verification

    tree = ast.parse(inspect.getsource(verification))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)

    for name in imported:
        assert "celery" not in name.lower(), f"celery wired: {name}"

    identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for foreign in ("shared_task", "periodic_task", "crontab", "apply_async", "delay"):
        assert foreign not in identifiers, f"scheduling primitive referenced: {foreign}"


def test_no_file_upload_for_insurance():
    """⚠️ document_reference نص، لا حقل ملف (Infra §7 شأن منفصل)."""
    field = InsuranceDocument._meta.get_field("document_reference")

    assert field.get_internal_type() == "CharField"
    names = {f.name for f in InsuranceDocument._meta.get_fields()}
    for file_ish in ("file", "document", "upload", "attachment"):
        assert file_ish not in names


def test_api_layer_does_not_touch_orm_directly():
    """كل الوصول عبر طبقة الخدمة — لا .objects في طبقة الـAPI."""
    import inspect

    from apps.contractors.api import admin_contractors, profile as profile_api

    for mod in (profile_api, admin_contractors):
        src = inspect.getsource(mod)
        assert ".objects." not in src, f"{mod.__name__} touches the ORM directly"
