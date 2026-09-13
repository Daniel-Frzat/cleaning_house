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

# ============================================================
# ⚠️⚠️ مؤقت للتشخيص — يُزال فور انتهاء الغرض ⚠️⚠️
# ============================================================
# يسجّل ترويسات أول 5 طلبات لـ/api/health لمعرفة ما يصل فعلًا من بروكسي
# Railway (خصوصًا HTTP_X_FORWARDED_PROTO و wsgi.url_scheme).
#
# ⚠️ موضعه أول القائمة إلزامي: SecurityMiddleware هو من يُنفّذ إعادة
#    التوجيه، فلو جاء قبله لانتهى الطلب بـ301 ولم يُسجَّل شيء إطلاقًا.
#
# ⚠️ في production.py وحده — dev.py و staging.py لم تُمسّا.
#
# 🔒 لا يسجّل أي ترويسة Authorization: تُحجب صراحةً أدناه حتى لا يتسرّب
#    توكن JWT إلى سجلات النشر.


class _TempDebugHeadersMiddleware:
    """مؤقت: يطبع ترويسات الطلب مرة واحدة لكل من أول 5 طلبات صحة."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.count = 0

    def __call__(self, request):
        if request.path == "/api/health" and self.count < 5:
            self.count += 1
            import logging

            logger = logging.getLogger("django")
            headers = {
                k: v
                for k, v in request.META.items()
                if (k.startswith("HTTP_") or k in ("wsgi.url_scheme", "SERVER_PROTOCOL"))
                # 🔒 لا نُسرّب بيانات اعتماد في السجل
                and k not in ("HTTP_AUTHORIZATION", "HTTP_COOKIE")
            }
            logger.warning(f"TEMP_DEBUG_HEADERS: {headers}")
        return self.get_response(request)


MIDDLEWARE = [
    "config.settings.production._TempDebugHeadersMiddleware",
    *MIDDLEWARE,  # noqa: F405 — موروثة من base
]