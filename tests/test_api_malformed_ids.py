"""
معرّف مشوَّه في المسار — يجب أن يُرفض بـ422 لا أن يُسقط الخادم.

📌 سبب الوجود: كانت كل المسارات توصّف معرّف المسار بـ`str`، فيصل النص
   المشوَّه إلى الـORM ويرفع ValidationError غير ملتقَط — أي 500 على
   طلب مشوَّه، في 15 من 20 نقطة نهاية. التوصيف بـuuid.UUID ينقل الرفض
   إلى طبقة التحقق فيصير 422 نظيفًا.

⚠️ هذا الملف حارس انحدار: أي مسار جديد يوصّف معرّفه بـ`str` سيسقط
   اختبار test_no_endpoint_declares_a_string_id أدناه.

🔒 لا يفحص هذا الملف الصلاحيات: الرفض هنا يقع قبلها، فالمعرّف المشوَّه
   يُرفض لكل دور بالتساوي ولا يكشف وجود أي مورد.
"""

import json
import re
from pathlib import Path

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services.tokens import issue_tokens_for_user

# نص ليس UUID بأي تأويل
MALFORMED_ID = "NOT-A-UUID"


def make_user(phone, role=ConfirmedRole.CUSTOMER):
    return User.objects.create_user(phone=phone, role=role)


def auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens_for_user(user)['access']}"}


# Fixtures — كتلة أرقام +614000150xx
@pytest.fixture
def client():
    return Client()


@pytest.fixture
def customer(db):
    return make_user("+61400015001")


@pytest.fixture
def contractor_user(db):
    return make_user("+61400015002", role=ConfirmedRole.CONTRACTOR)


@pytest.fixture
def admin_user(db):
    return make_user("+61400015003", role=ConfirmedRole.ADMIN)


def call(client, user, method, url, body=None):
    kwargs = dict(auth(user))
    if body is not None:
        kwargs.update(data=json.dumps(body), content_type="application/json")
    return getattr(client, method)(url, **kwargs)


# (وصف، الدور، الفعل، المسار، الجسم)
# الجسم صحيح حيثما لزم، حتى يكون سبب الـ422 هو المعرّف وحده.
ENDPOINTS = [
    ("retrieve_booking", "customer", "get", f"/api/bookings/{MALFORMED_ID}", None),
    (
        "reschedule_booking",
        "customer",
        "post",
        f"/api/bookings/{MALFORMED_ID}/reschedule",
        {"scheduled_at": "2030-01-01T09:00:00"},
    ),
    ("retrieve_payment", "customer", "get", f"/api/bookings/{MALFORMED_ID}/payment", None),
    ("retrieve_payout", "contractor", "get", f"/api/bookings/{MALFORMED_ID}/payout", None),
    ("retrieve_job", "customer", "get", f"/api/bookings/{MALFORMED_ID}/job", None),
    ("confirm_job", "customer", "post", f"/api/bookings/{MALFORMED_ID}/job/confirm", {}),
    (
        "accept_offer",
        "contractor",
        "post",
        f"/api/contractor/offers/{MALFORMED_ID}/accept",
        {},
    ),
    (
        "decline_offer",
        "contractor",
        "post",
        f"/api/contractor/offers/{MALFORMED_ID}/decline",
        {},
    ),
    ("start_job", "contractor", "post", f"/api/contractor/jobs/{MALFORMED_ID}/start", {}),
    (
        "mark_done",
        "contractor",
        "post",
        f"/api/contractor/jobs/{MALFORMED_ID}/mark-done",
        {},
    ),
    ("retrieve_property", "customer", "get", f"/api/properties/{MALFORMED_ID}", None),
    (
        "update_property",
        "customer",
        "patch",
        f"/api/properties/{MALFORMED_ID}",
        {"label": "Home"},
    ),
    ("delete_property", "customer", "delete", f"/api/properties/{MALFORMED_ID}", None),
    ("retrieve_public_service", "customer", "get", f"/api/services/{MALFORMED_ID}", None),
    ("retrieve_service", "admin", "get", f"/api/admin/services/{MALFORMED_ID}", None),
    (
        "update_service",
        "admin",
        "patch",
        f"/api/admin/services/{MALFORMED_ID}",
        {"name": "X"},
    ),
    ("delete_service", "admin", "delete", f"/api/admin/services/{MALFORMED_ID}", None),
    ("retrieve_contractor", "admin", "get", f"/api/admin/contractors/{MALFORMED_ID}", None),
    (
        "review_business_registration",
        "admin",
        "patch",
        f"/api/admin/business-registration/{MALFORMED_ID}",
        {"status": "VERIFIED"},
    ),
    (
        "review_insurance_document",
        "admin",
        "patch",
        f"/api/admin/insurance/{MALFORMED_ID}",
        {"status": "VERIFIED"},
    ),
    (
        "retrieve_support_request",
        "customer",
        "get",
        f"/api/support-requests/{MALFORMED_ID}",
        None,
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name,role,method,url,body",
    ENDPOINTS,
    ids=[e[0] for e in ENDPOINTS],
)
def test_malformed_id_is_rejected_cleanly(
    client, customer, contractor_user, admin_user, name, role, method, url, body
):
    """⚠️ 422 لا 500: الرفض في طبقة التحقق قبل أن يصل النص إلى الـORM."""
    user = {
        "customer": customer,
        "contractor": contractor_user,
        "admin": admin_user,
    }[role]

    response = call(client, user, method, url, body)

    assert response.status_code == 422, (
        f"{name} returned {response.status_code} for a malformed id "
        f"(expected 422): {response.content}"
    )


def test_no_endpoint_declares_a_string_id():
    """
    ⚠️ حارس انحدار بنيوي: لا مسار يوصّف معرّفه بـ`str`.

    الفحص على المصدر لا على السلوك: نقطة نهاية جديدة تُضاف بـ`str`
    تسقط هنا فورًا، قبل أن يكتشف أحدهم 500 في الإنتاج.
    """
    api_dir = Path(__file__).resolve().parent.parent / "apps"
    offenders = []

    for path in api_dir.glob("*/api/*.py"):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\b(\w*_id): str\b", source):
            line = source[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(api_dir.parent)}:{line} {match.group(0)}")

    assert not offenders, (
        "Path identifiers must be declared as uuid.UUID, not str — a malformed "
        "value otherwise reaches the ORM and raises a 500:\n  "
        + "\n  ".join(offenders)
    )
