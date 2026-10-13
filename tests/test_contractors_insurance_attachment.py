"""
Insurance Attachment Tests — Contractors Domain

يغطي: رفع مرفق (PDF أو صورة) لوثيقة تأمين المقاول، فحص المحتوى لا الاسم،
الحجم، الملكية، تجمّد الوثيقة بعد المراجعة، الاستبدال، وعدم كشف مرجع التخزين.
"""

from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.contractors.models import InsuranceDocument, VerificationStatus
from apps.contractors.services import verification as vsvc
from apps.jobs.adapters.base import StorageUnavailableError
from apps.jobs.adapters.fake_adapter import FAKE_URL_PREFIX

from .test_contractors_verification import (  # noqa: F401 — fixtures
    admin_user,
    auth,
    client,
    contractor_a,
    contractor_b,
    customer,
    future_date,
    post,
)

PDF_BYTES = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def submit_insurance(client, user):
    r = post(
        client,
        "/api/contractor/insurance",
        {"document_reference": "POL-123", "expiry_date": future_date()},
        **auth(user),
    )
    assert r.status_code == 201, r.content
    return r.json()


def upload(client, user, document_id, content, name="policy.pdf", content_type="application/pdf"):
    return client.post(
        f"/api/contractor/insurance/{document_id}/attachment",
        data={"file": SimpleUploadedFile(name, content, content_type=content_type)},
        **auth(user),
    )


@pytest.mark.django_db
def test_new_insurance_document_has_no_attachment(client, contractor_a):
    body = submit_insurance(client, contractor_a)

    assert body["has_attachment"] is False
    assert body["attachment_url"] is None
    assert body["attachment_content_type"] is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "content, expected_type",
    [(PDF_BYTES, "application/pdf"), (JPEG_BYTES, "image/jpeg")],
)
def test_upload_pdf_or_image(client, contractor_a, content, expected_type):
    doc = submit_insurance(client, contractor_a)

    r = upload(client, contractor_a, doc["id"], content)

    assert r.status_code == 200, r.content
    body = r.json()
    assert body["has_attachment"] is True
    assert body["attachment_content_type"] == expected_type
    assert body["attachment_url"].startswith(FAKE_URL_PREFIX)
    # 🔒 مرجع التخزين نفسه لا يُكشف
    assert "attachment_key" not in body

    stored = InsuranceDocument.objects.get(pk=doc["id"])
    assert stored.attachment_key
    assert stored.attachment_content_type == expected_type


@pytest.mark.django_db
def test_attachment_appears_in_own_list(client, contractor_a):
    doc = submit_insurance(client, contractor_a)
    upload(client, contractor_a, doc["id"], PDF_BYTES)

    r = client.get("/api/contractor/insurance", **auth(contractor_a))

    assert r.status_code == 200
    assert r.json()[0]["has_attachment"] is True
    assert r.json()[0]["attachment_url"].startswith(FAKE_URL_PREFIX)


@pytest.mark.django_db
def test_type_is_detected_from_content_not_declared_type(client, contractor_a):
    doc = submit_insurance(client, contractor_a)

    r = upload(
        client, contractor_a, doc["id"], b"<html><script>alert(1)</script></html>",
        name="policy.pdf", content_type="application/pdf",
    )

    assert r.status_code == 400
    assert r.json()["code"] == "unsupported_attachment_format"
    assert InsuranceDocument.objects.get(pk=doc["id"]).attachment_key == ""


@pytest.mark.django_db
def test_empty_file_is_rejected(client, contractor_a):
    doc = submit_insurance(client, contractor_a)

    r = upload(client, contractor_a, doc["id"], b"")

    assert r.status_code == 400


@pytest.mark.django_db
def test_too_large_file_is_rejected(client, contractor_a, settings):
    settings.INSURANCE_ATTACHMENT_MAX_BYTES = 32
    doc = submit_insurance(client, contractor_a)

    r = upload(client, contractor_a, doc["id"], PDF_BYTES)

    assert r.status_code == 400
    assert r.json()["code"] == "attachment_too_large"


@pytest.mark.django_db
def test_reupload_replaces_and_deletes_old_file(client, contractor_a, django_capture_on_commit_callbacks):
    doc = submit_insurance(client, contractor_a)
    upload(client, contractor_a, doc["id"], PDF_BYTES)
    old_key = InsuranceDocument.objects.get(pk=doc["id"]).attachment_key

    with mock.patch("apps.jobs.adapters.fake_adapter.FakeStorageAdapter.delete") as delete:
        with django_capture_on_commit_callbacks(execute=True):
            r = upload(client, contractor_a, doc["id"], JPEG_BYTES, name="p.jpg", content_type="image/jpeg")

    assert r.status_code == 200
    stored = InsuranceDocument.objects.get(pk=doc["id"])
    assert stored.attachment_key != old_key
    assert stored.attachment_content_type == "image/jpeg"
    delete.assert_called_once_with(old_key)


@pytest.mark.django_db
def test_other_contractors_document_is_not_found(client, contractor_a, contractor_b):
    doc = submit_insurance(client, contractor_a)

    r = upload(client, contractor_b, doc["id"], PDF_BYTES)

    assert r.status_code == 404
    assert InsuranceDocument.objects.get(pk=doc["id"]).attachment_key == ""


@pytest.mark.django_db
def test_customer_cannot_upload(client, contractor_a, customer):
    doc = submit_insurance(client, contractor_a)

    r = upload(client, customer, doc["id"], PDF_BYTES)

    assert r.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("status", [VerificationStatus.VERIFIED, VerificationStatus.REJECTED])
def test_reviewed_document_is_frozen(client, contractor_a, status):
    doc = submit_insurance(client, contractor_a)
    InsuranceDocument.objects.filter(pk=doc["id"]).update(status=status)

    r = upload(client, contractor_a, doc["id"], PDF_BYTES)

    assert r.status_code == 409
    assert r.json()["code"] == "insurance_document_not_editable"


@pytest.mark.django_db
def test_storage_failure_returns_503_and_keeps_document_unchanged(client, contractor_a):
    doc = submit_insurance(client, contractor_a)

    with mock.patch(
        "apps.jobs.adapters.fake_adapter.FakeStorageAdapter.upload",
        side_effect=StorageUnavailableError("down"),
    ):
        r = upload(client, contractor_a, doc["id"], PDF_BYTES)

    assert r.status_code == 503
    assert InsuranceDocument.objects.get(pk=doc["id"]).attachment_key == ""


@pytest.mark.django_db
def test_admin_sees_attachment_in_pending_queue(client, contractor_a, admin_user):
    doc = submit_insurance(client, contractor_a)
    upload(client, contractor_a, doc["id"], PDF_BYTES)

    r = client.get("/api/admin/verifications/pending", **auth(admin_user))

    assert r.status_code == 200
    item = r.json()["insurance_documents"][0]
    assert item["has_attachment"] is True
    assert item["attachment_url"].startswith(FAKE_URL_PREFIX)


def test_sniff_attachment_type():
    assert vsvc.sniff_attachment_type(PDF_BYTES) == "application/pdf"
    assert vsvc.sniff_attachment_type(JPEG_BYTES) == "image/jpeg"
    assert vsvc.sniff_attachment_type(b"GIF89a....") is None
