"""Production environment settings."""

from .base import *  # noqa
from .database import build_databases

DEBUG = False

# ------------------------------------------------------------
# Database — PostgreSQL (Production Server) — Change Set قسم 4B
# ------------------------------------------------------------
# يقرأ DATABASE_URL إن وُجدت (ما تحقنه Railway/Heroku عند ربط قاعدة
# بيانات)، وإلا يرجع إلى متغيرات DB_* المنفصلة. راجع database.py.
DATABASES = build_databases()

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

# بروكسي Railway واحد يضيف IP العميل إلى X-Forwarded-For.
NUM_TRUSTED_PROXIES = config("NUM_TRUSTED_PROXIES", default=1, cast=int)

SECURE_SSL_REDIRECT = True
# 🔒 فحص صحة Railway يصل داخليًا عبر HTTP وبالـhostname healthcheck.railway.app:
#    بدون هذين يُرفض بـ400 (ALLOWED_HOSTS) أو يُحوَّل بـ301 (SSL redirect)،
#    فيفشل الفحص ولا يُفعَّل أي نشر جديد أبدًا.
SECURE_REDIRECT_EXEMPT = [r"^api/health"]
if "healthcheck.railway.app" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, "healthcheck.railway.app"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# ملفات Admin الساكنة مضغوطة ومُعنونة بالبصمة (WhiteNoise).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}