"""Staging environment settings."""

from .base import *  # noqa
from .database import build_databases

DEBUG = False

# ------------------------------------------------------------
# Database — PostgreSQL (Staging Server) — Change Set قسم 4B
# ------------------------------------------------------------
# نفس منطق الإنتاج: DATABASE_URL أولًا، ثم متغيرات DB_* المنفصلة.
DATABASES = build_databases()

# ------------------------------------------------------------
# Security — staging يعمل خلف بروكسي Railway نفسه، فيحتاج التقوية نفسها.
# (بدون SECURE_PROXY_SSL_HEADER تحدث حلقة إعادة توجيه — راجع production.py)
# ------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
NUM_TRUSTED_PROXIES = config("NUM_TRUSTED_PROXIES", default=1, cast=int)
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# HSTS أقصر من الإنتاج وبلا preload: staging قد يُنقل أو يُعاد إنشاؤه.
SECURE_HSTS_SECONDS = 3600

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}