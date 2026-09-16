"""Development environment settings."""

from .base import *  # noqa

DEBUG = True

# 📌 التطوير المحلي بلا مزوّد اتجاهات: يُسمح بالارتداد إلى haversine حتى
#    يعمل التدفّق كاملًا. الإنتاج يبقى على الافتراضي False — لا ارتداد
#    ضمني دون سياسة معلنة (§9).
DISPATCH_ALLOW_HAVERSINE_FALLBACK = True

# ------------------------------------------------------------
# Database — SQLite للتطوير المحلي فقط (Change Set — قسم 4B)
# ------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}