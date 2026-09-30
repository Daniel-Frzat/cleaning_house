"""
S3StorageAdapter — تخزين حقيقي متوافق مع S3 (Cloudflare R2 أو AWS S3).

🔒 الـbucket خاص: لا روابط دائمة. كل عرض لصورة برابط موقَّع مؤقت
   (STORAGE_SIGNED_URL_SECONDS، افتراضيًا ساعة) — Security Architecture §23.
🔒 المفاتيح عشوائية (uuid) تحت path_hint، فلا تُخمَّن ولا تكشف اسم الملف
   الأصلي.

الإعداد (R2):
    JOB_STORAGE_ADAPTER_CLASS=apps.jobs.adapters.s3_adapter.S3StorageAdapter
    STORAGE_S3_BUCKET=qcleano-photos
    STORAGE_S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
    STORAGE_S3_ACCESS_KEY_ID=…
    STORAGE_S3_SECRET_ACCESS_KEY=…
    STORAGE_S3_REGION=auto
AWS S3: نفس المتغيرات بلا ENDPOINT_URL، و REGION=ap-southeast-2 مثلًا.
"""

import logging
import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import BaseStorageProviderAdapter, StorageUnavailableError, StorageUploadResult

logger = logging.getLogger(__name__)

_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "application/pdf": ".pdf",
}


class S3StorageAdapter(BaseStorageProviderAdapter):
    provider_name = "s3"

    def __init__(self):
        import boto3
        from botocore.config import Config

        self.bucket = getattr(settings, "STORAGE_S3_BUCKET", "") or ""
        access_key = getattr(settings, "STORAGE_S3_ACCESS_KEY_ID", "") or ""
        secret_key = getattr(settings, "STORAGE_S3_SECRET_ACCESS_KEY", "") or ""
        if not (self.bucket and access_key and secret_key):
            raise ImproperlyConfigured(
                "S3 storage needs STORAGE_S3_BUCKET, STORAGE_S3_ACCESS_KEY_ID and STORAGE_S3_SECRET_ACCESS_KEY."
            )
        self.signed_url_seconds = int(getattr(settings, "STORAGE_SIGNED_URL_SECONDS", 3600))
        self.client = boto3.client(
            "s3",
            endpoint_url=getattr(settings, "STORAGE_S3_ENDPOINT_URL", "") or None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=getattr(settings, "STORAGE_S3_REGION", "") or "auto",
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=30,
            ),
        )

    def _errors(self):
        from botocore.exceptions import BotoCoreError, ClientError

        return (BotoCoreError, ClientError)

    def upload(self, file_bytes, content_type, path_hint):
        key = f"{path_hint.strip('/')}/{uuid.uuid4().hex}{_EXTENSIONS.get(content_type, '')}"
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=file_bytes, ContentType=content_type)
        except self._errors() as exc:
            logger.exception("Storage upload failed (key=%s)", key)
            raise StorageUnavailableError("The file could not be stored.") from exc
        return StorageUploadResult(storage_key=key, signed_url=self.get_signed_url(key))

    def get_signed_url(self, storage_key):
        # 📌 التوقيع حساب محلي بلا شبكة — آمن داخل قوائم الصور
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": storage_key},
            ExpiresIn=self.signed_url_seconds,
        )

    def delete(self, storage_key):
        try:
            self.client.delete_object(Bucket=self.bucket, Key=storage_key)
        except self._errors() as exc:
            raise StorageUnavailableError("The file could not be deleted.") from exc
