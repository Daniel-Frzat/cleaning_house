"""
Cleaning House — Base Settings (shared across all environments)

⚠️ هذا الملف يغطي فقط البنية التحتية (Phase 0 — Foundation).
لا يحتوي أي Business Logic أو Domain Models.
لا تُضف هنا أي إعداد خاص بـProvider خارجي غير محسوم (راجع Open Decisions).
"""

from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="", cast=lambda v: [s.strip() for s in v.split(",") if s.strip()])

# ------------------------------------------------------------
# Applications
# ------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "ninja_jwt",
]

# apps.accounts — Identity Domain.
# Phase 1 / Step 1: User Model المخصص (phone كمعرّف أساسي).
# Phase 1: User + OTPVerification + SocialAccount.
LOCAL_APPS = [
    "apps.accounts",
    # Properties & Address Domain — PropertyAddress كيان منفصل (Change Set قسم 20)
    "apps.properties",
    # Service Catalog & Pricing Domain — ServiceType + PricingConfig
    # (Change Set §36.2، §5). التسعير لكل خدمة، وسعر الكيلومتر عام.
    "apps.services",
    # Contractor Profile Domain — ContractorProfile (بيانات يستهلكها
    # Dispatch لاحقًا §36.7؛ لا منطق مطابقة ولا adapters هنا).
    "apps.contractors",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ------------------------------------------------------------
# Database — يُعرَّف لكل بيئة على حدة (راجع dev.py / staging.py / production.py)
# قرار: SQLite محليًا للتطوير فقط، PostgreSQL في Staging/Production
# (راجع Change Set — قسم 4B)
# ------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ------------------------------------------------------------
# Authentication — Custom User Model (Identity Domain, Phase 1)
# ⚠️ قرار أحادي الاتجاه في Django: يجب ضبطه قبل أول migration للمشروع.
# المعرّف الأساسي هو phone (OTP-based auth) — لا يوجد حقل username.
# ------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

# ------------------------------------------------------------
# Internationalization
# ------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ------------------------------------------------------------
# Static files
# ------------------------------------------------------------
STATIC_URL = "static/"

# ------------------------------------------------------------
# JWT Configuration (Auth Foundation — Phase 0)
# ------------------------------------------------------------
from datetime import timedelta

NINJA_JWT = {
    "SIGNING_KEY": config("JWT_SIGNING_KEY"),
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=config("JWT_ACCESS_TOKEN_LIFETIME_MIN", default=15, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_TOKEN_LIFETIME_DAYS", default=7, cast=int)),
}

# ------------------------------------------------------------
# Provider Adapters
# ------------------------------------------------------------
# SMS Gateway Provider ما زال قرارًا مفتوحًا (🟢 غير معطِّل).
# الافتراضي هنا adapter تطويري يطبع الرمز في الـconsole فقط.
# عند حسم الـProvider: يُستبدل هذا المسار فقط — دون تعديل كود الـDomain.
SMS_ADAPTER = config(
    "SMS_ADAPTER",
    default="adapters.sms.dev_console.DevConsoleSMSAdapter",
)

# صمّام أمان: DevConsoleSMSAdapter يرفض العمل عند DEBUG=False إلا إذا
# فُعّل هذا الخيار صراحةً (مطلوب في بيئة الاختبارات الآلية).
SMS_DEV_ALLOW_INSECURE = config("SMS_DEV_ALLOW_INSECURE", default=False, cast=bool)

# ------------------------------------------------------------
# Social Auth (Sign in with Apple / Google — Phase 1)
# ------------------------------------------------------------
# التحقق الفعلي من التوكنات يتطلب إعدادات تشغيلية (Apple Developer keys،
# Google OAuth client IDs) تُدار عبر متغيرات بيئة وهي خارج نطاق هذه الخطوة.
# الافتراضي هنا adapter وهمي للاختبار/التطوير فقط. عند تجهيز الاعتمادات
# يُستبدل هذا المسار فقط — دون تعديل كود الـDomain.
SOCIAL_AUTH_ADAPTER = config(
    "SOCIAL_AUTH_ADAPTER",
    default="adapters.social_auth.fake.FakeSocialAuthAdapter",
)

# صمّام أمان: FakeSocialAuthAdapter يرفض العمل عند DEBUG=False إلا إذا
# فُعّل هذا الخيار صراحةً (مطلوب في بيئة الاختبارات الآلية).
SOCIAL_AUTH_ALLOW_FAKE = config("SOCIAL_AUTH_ALLOW_FAKE", default=False, cast=bool)

# ------------------------------------------------------------
# OTP Policy (Identity Domain)
# ------------------------------------------------------------
# ⚠️ القيم أدناه "defaults آمنة بانتظار تأكيد Product Owner"
#    (defaults pending product-owner confirmation).
#    وجود Rate Limiting/Expiry/Max Attempts هو متطلب أمني إلزامي حسب
#    الـChange Set، لكن الأرقام نفسها لم تُعتمد كقاعدة عمل نهائية.
#    لا تُعامل هذه الأرقام كـBusiness Rule محسومة.
OTP_EXPIRY_SECONDS = config("OTP_EXPIRY_SECONDS", default=300, cast=int)  # 5 دقائق
OTP_MAX_ATTEMPTS = config("OTP_MAX_ATTEMPTS", default=5, cast=int)
OTP_RESEND_COOLDOWN_SECONDS = config("OTP_RESEND_COOLDOWN_SECONDS", default=60, cast=int)
OTP_CODE_LENGTH = config("OTP_CODE_LENGTH", default=6, cast=int)

# ------------------------------------------------------------
# Celery / Redis (Background Jobs infra only — no tasks with
# business logic yet; Dispatch expiry / Escrow checks / etc.
# سيُضافون في مراحلهم الخاصة حسب Domain)
# ------------------------------------------------------------
CELERY_BROKER_URL = config("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = config("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# ------------------------------------------------------------
# Logging (قسم 24 من المرجع — Observability)
# ------------------------------------------------------------
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)  # ينشئ المجلد تلقائيًا إن لم يكن موجودًا

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} | {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOGS_DIR / "app.log",
            "encoding": "utf-8",
            "maxBytes": 1024 * 1024 * 5,  # 5MB
            "backupCount": 5,
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}