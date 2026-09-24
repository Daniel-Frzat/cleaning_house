"""Development environment settings."""

from .base import *  # noqa

DEBUG = True

# ------------------------------------------------------------
# Database — SQLite للتطوير المحلي فقط (Change Set — قسم 4B)
# ------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
