"""Production environment settings."""

from decouple import config

from .base import *  # noqa

DEBUG = False

# ------------------------------------------------------------
# Database — PostgreSQL (Production Server) — Change Set قسم 4B
# ------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME"),
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
    }
}

# ------------------------------------------------------------
# Security hardening إضافية للإنتاج
# ------------------------------------------------------------
# ⚠️ إلزامي خلف بروكسي (Railway): البروكسي ينهي TLS ثم يمرّر الطلب إلى
#    التطبيق عبر HTTP داخليًا. بدون هذا السطر يرى Django الطلب كـHTTP
#    فيعيد توجيهه إلى HTTPS، فيعود عبر البروكسي كـHTTP مجددًا — حلقة
#    لا نهائية تظهر للمستخدم كـERR_TOO_MANY_REDIRECTS.
#
#    السطر لا يُعطّل إعادة التوجيه بل يجعلها صحيحة: يثق بترويسة
#    X-Forwarded-Proto ليعرف البروتوكول الأصلي.
#
# 🔒 الثقة بهذه الترويسة آمنة هنا تحديدًا لأن بروكسي Railway هو المنفذ
#    الوحيد للتطبيق ويضبطها بنفسه. لو صار التطبيق يقبل اتصالًا مباشرًا
#    من الشبكة يومًا، فالترويسة تصبح قابلة للانتحال ويجب مراجعة هذا.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True