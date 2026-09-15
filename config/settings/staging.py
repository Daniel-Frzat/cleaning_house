"""Staging environment settings."""

from .base import *  # noqa
from .database import build_databases

DEBUG = False

# ------------------------------------------------------------
# Database — PostgreSQL (Staging Server) — Change Set قسم 4B
# ------------------------------------------------------------
# نفس منطق الإنتاج: DATABASE_URL أولًا، ثم متغيرات DB_* المنفصلة.
DATABASES = build_databases()