"""
تخزين الصور الحقيقي — S3StorageAdapter (Cloudflare R2 / AWS S3).

الروابط الموقَّعة تُحسب بـboto3 الحقيقي (حساب محلي بلا شبكة). الرفع والحذف
مستبدلان بدوال محلية.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from apps.jobs.adapters.base import StorageUnavailableError
from apps.jobs.adapters.s3_adapter import S3StorageAdapter
from apps.jobs.models import JobPhoto
from tests.helpers import JPEG_BYTES
from tests.test_jobs import auth, client, contractor, customer, job, make_photo_file, prop, service_type  # noqa: F401


@pytest.fixture
def r2(settings):
    settings.JOB_STORAGE_ADAPTER_CLASS = "apps.jobs.adapters.s3_adapter.S3StorageAdapter"
    settings.STORAGE_S3_BUCKET = "qcleano-photos"
    settings.STORAGE_S3_ENDPOINT_URL = "https://abc123.r2.cloudflarestorage.com"
    settings.STORAGE_S3_ACCESS_KEY_ID = "test-access"
    settings.STORAGE_S3_SECRET_ACCESS_KEY = "test-secret"
    settings.STORAGE_S3_REGION = "auto"
    settings.STORAGE_SIGNED_URL_SECONDS = 3600
    return settings


class RecordingClient:
    """يغلّف عميل boto3 الحقيقي: التوقيع حقيقي، والرفع والحذف يُسجَّلان محليًا."""

    def __init__(self, real, stored, failures):
        self.real, self.stored, self.failures = real, stored, failures

    def generate_presigned_url(self, *args, **kwargs):
        return self.real.generate_presigned_url(*args, **kwargs)

    def put_object(self, **kwargs):
        if "put" in self.failures:
            raise self.failures["put"]
        self.stored[kwargs["Key"]] = kwargs
        return {}

    def delete_object(self, **kwargs):
        self.stored.pop(kwargs["Key"], None)
        return {}


@pytest.fixture
def uploads(monkeypatch):
    import boto3

    stored, failures = {}, {}
    real_client = boto3.client
    monkeypatch.setattr(boto3, "client", lambda *a, **k: RecordingClient(real_client(*a, **k), stored, failures))
    return type("Uploads", (), {"stored": stored, "failures": failures})


def test_missing_settings_fail_loudly(settings):
    settings.STORAGE_S3_BUCKET = ""
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured):
        S3StorageAdapter()


def test_signed_url_is_private_and_temporary(r2):
    url = S3StorageAdapter().get_signed_url("jobs/123/before/abc.jpg")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.hostname.endswith("r2.cloudflarestorage.com")
    assert "qcleano-photos" in url and "jobs/123/before/abc.jpg" in url
    assert query["X-Amz-Expires"] == ["3600"] and "X-Amz-Signature" in query


def test_upload_stores_with_random_key_and_type(r2, uploads):
    result = S3StorageAdapter().upload(JPEG_BYTES, "image/jpeg", "jobs/42/before")
    assert result.storage_key.startswith("jobs/42/before/") and result.storage_key.endswith(".jpg")
    put = uploads.stored[result.storage_key]
    assert put["Bucket"] == "qcleano-photos" and put["ContentType"] == "image/jpeg" and put["Body"] == JPEG_BYTES
    assert result.signed_url and "X-Amz-Signature" in result.signed_url


def test_provider_errors_become_storage_unavailable(r2, uploads):
    uploads.failures["put"] = EndpointConnectionError(endpoint_url="https://abc123.r2.cloudflarestorage.com")
    with pytest.raises(StorageUnavailableError):
        S3StorageAdapter().upload(JPEG_BYTES, "image/jpeg", "jobs/1/before")


@pytest.mark.django_db
def test_photo_upload_through_the_api(client, contractor, job, r2, uploads):
    user, _ = contractor
    r = client.post(f"/api/contractor/jobs/{job.id}/photos?photo_type=BEFORE", {"file": make_photo_file()},
                    **auth(user))
    assert r.status_code == 201, r.content
    photo = JobPhoto.objects.get(pk=r.json()["id"])
    assert photo.storage_key in uploads.stored
    assert "X-Amz-Signature" in r.json()["signed_url"]


@pytest.mark.django_db
def test_storage_down_is_503_and_leaves_no_row(client, contractor, job, r2, uploads):
    uploads.failures["put"] = ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "PutObject")
    user, _ = contractor
    r = client.post(f"/api/contractor/jobs/{job.id}/photos?photo_type=BEFORE", {"file": make_photo_file()},
                    **auth(user))
    assert r.status_code == 503 and r.json()["code"] == "photo_storage_unavailable"
    assert not JobPhoto.objects.filter(job=job).exists()


@pytest.mark.django_db
def test_removed_photo_is_deleted_from_storage(client, contractor, job, r2, uploads,
                                                django_capture_on_commit_callbacks):
    user, _ = contractor
    r = client.post(f"/api/contractor/jobs/{job.id}/photos?photo_type=BEFORE", {"file": make_photo_file()},
                    **auth(user))
    key = JobPhoto.objects.get(pk=r.json()["id"]).storage_key
    with django_capture_on_commit_callbacks(execute=True):
        client.delete(f"/api/contractor/jobs/{job.id}/photos/{r.json()['id']}", **auth(user))
    assert key not in uploads.stored
