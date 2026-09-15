"""
Database configuration helper — staging/production.

📌 سبب الوجود: منصات النشر (Railway، Heroku، Render…) تحقن رابطًا واحدًا
   مجمّعًا اسمه DATABASE_URL عند ربط قاعدة بيانات. المشروع كان يقرأ
   متغيرات DB_* منفصلة فقط، فربط الخدمة لا يكفي — وكانت النتيجة أخطاء
   متتابعة وغامضة (DB_NAME فارغ → ImproperlyConfigured، ثم DB_PASSWORD
   فارغ → fe_sendauth: no password supplied).

الأولوية:
   1) DATABASE_URL إن وُجدت وغير فارغة  ← مسار المنصات
   2) وإلا متغيرات DB_* المنفصلة        ← المسار الأصلي، بلا كسر

⚠️ التحليل يستخدم urllib.parse القياسية لا حزمة خارجية: الاعتماد على
   dj-database-url لأجل دالة واحدة إضافة تبعية بلا داعٍ.

⚠️ لا قيمة افتراضية للرابط ولا لـDB_NAME/DB_USER/DB_PASSWORD: بيئة إنتاج
   بلا بيانات اتصال يجب أن تفشل عند الإقلاع بصوت عالٍ، لا أن تعمل على
   قاعدة خاطئة.
"""

from urllib.parse import unquote, urlparse

from decouple import config
from django.core.exceptions import ImproperlyConfigured

# محركات مدعومة — المشروع على PostgreSQL، والمخططات الثلاثة شائعة
# في روابط المنصات.
_POSTGRES_SCHEMES = {"postgres", "postgresql", "pgsql"}

POSTGRES_ENGINE = "django.db.backends.postgresql"


def _from_url(url):
    """
    يحوّل DATABASE_URL إلى قاموس إعدادات Django.

    الشكل: postgres://USER:PASSWORD@HOST:PORT/NAME

    🔒 كلمة المرور تُفك ترميزها (unquote): المنصات ترمّز الرموز الخاصة
       (@ : / #) داخل الرابط، وتمريرها مرمَّزة يعني مصادقة فاشلة برسالة
       مضلّلة.
    """
    parsed = urlparse(url)

    if parsed.scheme not in _POSTGRES_SCHEMES:
        raise ImproperlyConfigured(
            f"DATABASE_URL has an unsupported scheme: {parsed.scheme!r}. "
            f"This project runs on PostgreSQL — expected one of: "
            f"{', '.join(sorted(_POSTGRES_SCHEMES))}."
        )

    name = (parsed.path or "").lstrip("/")
    if not name:
        raise ImproperlyConfigured(
            "DATABASE_URL has no database name (the part after the host). "
            "Expected postgres://USER:PASSWORD@HOST:PORT/NAME."
        )

    return {
        "ENGINE": POSTGRES_ENGINE,
        "NAME": name,
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        # المنفذ قد يغيب من الرابط — الافتراضي القياسي لـPostgreSQL
        "PORT": str(parsed.port or "5432"),
    }


def _from_discrete_vars():
    """يبني الإعدادات من متغيرات DB_* المنفصلة — السلوك الأصلي."""
    return {
        "ENGINE": POSTGRES_ENGINE,
        "NAME": config("DB_NAME"),
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
    }


def build_databases():
    """
    يعيد إعداد DATABASES الكامل.

    📌 الرابط الفارغ يُعامل كغير موجود: المنصات قد تُعرّف المتغيّر بقيمة
       خالية حين يفشل حلّ مرجعٍ ما، وقبوله كان سيُنتج إعدادات فارغة
       بصمت بدل الرجوع إلى DB_*.
    """
    url = (config("DATABASE_URL", default="") or "").strip()

    if url:
        return {"default": _from_url(url)}

    return {"default": _from_discrete_vars()}
