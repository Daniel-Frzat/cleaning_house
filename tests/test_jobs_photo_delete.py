"""
إزالة صورة قبل الإنجاز — تقرير التطبيق B21.

🔒 نفس بوابة الرفع: المقاول المُسنَد، والمهمة قيد التنفيذ. بعد mark-done
   الصور دليل يؤكد عليه العميل فلا تُمس.
"""

import uuid

import pytest

from apps.jobs.models import JobPhoto
from tests.test_jobs import (  # noqa: F401
    auth, client, contractor, customer, job, make_photo_file, other_contractor, prop, service_type,
)


def upload(client, user, job, photo_type="BEFORE"):
    r = client.post(
        f"/api/contractor/jobs/{job.id}/photos?photo_type={photo_type}",
        {"file": make_photo_file()},
        **auth(user),
    )
    assert r.status_code == 201, r.content
    return r.json()["id"]


def delete(client, user, job, photo_id):
    return client.delete(f"/api/contractor/jobs/{job.id}/photos/{photo_id}", **auth(user))


@pytest.mark.django_db
def test_assigned_contractor_removes_a_photo(client, contractor, job):
    user, _ = contractor
    photo_id = upload(client, user, job)

    r = delete(client, user, job, photo_id)
    assert r.status_code == 204, r.content
    assert not JobPhoto.objects.filter(pk=photo_id).exists()


@pytest.mark.django_db
def test_storage_file_is_deleted_after_commit(client, contractor, job, monkeypatch, django_capture_on_commit_callbacks):
    from apps.jobs.adapters.fake_adapter import FakeStorageAdapter

    user, _ = contractor
    photo_id = upload(client, user, job)
    key = JobPhoto.objects.get(pk=photo_id).storage_key
    deleted = []
    monkeypatch.setattr(FakeStorageAdapter, "delete", lambda self, k: deleted.append(k))

    with django_capture_on_commit_callbacks(execute=True):
        assert delete(client, user, job, photo_id).status_code == 204
    assert deleted == [key]


@pytest.mark.django_db
def test_storage_failure_does_not_fail_the_request(client, contractor, job, monkeypatch, django_capture_on_commit_callbacks):
    from apps.jobs.adapters.fake_adapter import FakeStorageAdapter

    user, _ = contractor
    photo_id = upload(client, user, job)

    def boom(self, key):
        raise RuntimeError("provider down")

    monkeypatch.setattr(FakeStorageAdapter, "delete", boom)
    with django_capture_on_commit_callbacks(execute=True):
        assert delete(client, user, job, photo_id).status_code == 204
    assert not JobPhoto.objects.filter(pk=photo_id).exists()


@pytest.mark.django_db
def test_other_contractor_cannot_remove(client, contractor, other_contractor, job):
    user, _ = contractor
    photo_id = upload(client, user, job)
    stranger, _ = other_contractor
    assert delete(client, stranger, job, photo_id).status_code == 403
    assert JobPhoto.objects.filter(pk=photo_id).exists()


@pytest.mark.django_db
def test_customer_cannot_remove(client, contractor, customer, job):
    user, _ = contractor
    photo_id = upload(client, user, job)
    assert delete(client, customer, job, photo_id).status_code == 403


@pytest.mark.django_db
def test_unknown_photo_or_photo_of_another_job_is_404(client, contractor, job):
    user, _ = contractor
    r = delete(client, user, job, uuid.uuid4())
    assert r.status_code == 404 and r.json()["code"] == "photo_not_found"


@pytest.mark.django_db
def test_photos_are_frozen_after_mark_done(client, contractor, job):
    user, _ = contractor
    before = upload(client, user, job, "BEFORE")
    upload(client, user, job, "AFTER")
    r = client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(user))
    assert r.status_code == 200, r.content

    r = delete(client, user, job, before)
    assert r.status_code == 409 and r.json()["code"] == "job_not_accepting_photos"
    assert JobPhoto.objects.filter(pk=before).exists()


@pytest.mark.django_db
def test_removing_the_last_before_photo_blocks_mark_done_until_replaced(client, contractor, job):
    user, _ = contractor
    before = upload(client, user, job, "BEFORE")
    upload(client, user, job, "AFTER")
    assert delete(client, user, job, before).status_code == 204

    r = client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(user))
    assert r.status_code >= 400 and r.json()["code"] == "missing_proof_photos"

    upload(client, user, job, "BEFORE")
    assert client.post(f"/api/contractor/jobs/{job.id}/mark-done", **auth(user)).status_code == 200
