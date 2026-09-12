"""
يحدد هذا الملف أي إعدادات بيئة يجب تحميلها بناءً على متغير DJANGO_ENV.
القيم المسموحة: dev | staging | production

⚠️ DJANGO_ENV **إلزامي بلا default** عمدًا.

   السبب من واقعة حقيقية: عند النشر على Railway بلا متغيرات بيئة، كان
   الـdefault القديم ("dev") يجعل التطبيق يحمّل إعدادات التطوير بصمت —
   SQLite، وبلا تقوية أمنية — ثم يسقط لاحقًا على SECRET_KEY برسالة
   تُشير إلى المتغير الخطأ. الفشل الصامت في اختيار البيئة أخطر من
   الفشل الصاخب في مفتاح واحد.

   القاعدة نفسها المطبَّقة على SECRET_KEY: ما لا يمكن تخمينه بأمان
   يجب أن يُعلَن صراحةً، وإلا يسقط الإقلاع فورًا برسالة مفهومة.

⚠️ لا تُضف default هنا "لتسهيل التشغيل". بيئة الاختبارات تُصرّح عن
   نفسها في pytest.ini (DJANGO_ENV=dev)، والتطوير المحلي في .env.
"""

from decouple import config
from django.core.exceptions import ImproperlyConfigured

VALID_ENVIRONMENTS = ("dev", "staging", "production")

try:
    DJANGO_ENV = config("DJANGO_ENV")
except Exception as exc:  # decouple.UndefinedValueError
    raise ImproperlyConfigured(
        "DJANGO_ENV is not set. Declare it explicitly as an environment "
        f"variable — one of: {', '.join(VALID_ENVIRONMENTS)}.\n"
        "  • Local development: add DJANGO_ENV=dev to your .env file.\n"
        "  • Railway/staging/production: set it in the service's Variables "
        "tab, together with SECRET_KEY, JWT_SIGNING_KEY and the DB_* values "
        "(see .env.example for the full required list).\n"
        "There is deliberately no default: silently falling back to dev "
        "settings in a production deployment is more dangerous than failing "
        "to start."
    ) from exc

if DJANGO_ENV not in VALID_ENVIRONMENTS:
    raise ImproperlyConfigured(
        f"DJANGO_ENV has an unrecognised value: {DJANGO_ENV!r}. "
        f"Expected one of: {', '.join(VALID_ENVIRONMENTS)}."
    )

if DJANGO_ENV == "production":
    from .production import *  # noqa
elif DJANGO_ENV == "staging":
    from .staging import *  # noqa
else:
    from .dev import *  # noqa
