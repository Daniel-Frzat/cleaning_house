"""
يحدد هذا الملف أي إعدادات بيئة يجب تحميلها بناءً على متغير DJANGO_ENV.
القيم المسموحة: dev (افتراضي) | staging | production
"""

from decouple import config

DJANGO_ENV = config("DJANGO_ENV", default="dev")

if DJANGO_ENV == "production":
    from .production import *  # noqa
elif DJANGO_ENV == "staging":
    from .staging import *  # noqa
else:
    from .dev import *  # noqa