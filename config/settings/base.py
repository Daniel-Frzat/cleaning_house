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
    # موقع إدارة مخصّص بدخول على خطوتين (كلمة سر + رمز SMS) — config/admin_site.py
    "config.admin_apps.CleaningHouseAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "ninja_jwt",
    # قائمة سوداء لتوكنات refresh — تُمكّن تسجيل الخروج وتدوير التوكن
    # (refresh القديم يُبطَل فور استخدامه).
    "ninja_jwt.token_blacklist",
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
    # Booking Domain — Booking + BookingServiceSelection (Change Set §36.1، §20).
    # هيكل فقط: لا إسناد ولا عروض ولا حساب سعر في هذه المرحلة.
    "apps.bookings",
    # Payment Domain — شحن مباشر لحظة تأكيد الحجز (§36.4، §8؛ Infra §2).
    # لا escrow ولا authorize/capture — المزوّد الفعلي قرار مفتوح.
    "apps.payments",
    # Job Execution Domain — Job + JobPhoto (§20، §36.3؛ Infra §7).
    # لا إلغاء (بند مفتوح #12)، ولا تخزين ملفات حقيقي.
    "apps.jobs",
    # Payout Domain — Payout (§36.5). دفع فوري لكل حجز، صفر عمولة،
    # لا تجميع ولا دفعات مجمَّعة (قرار محسوم).
    "apps.payouts",
    # Support Domain — SupportRequest (قرار MVP). قناة باتجاه واحد خلف
    # شاشتَي Contact support و Report an issue: لا ردود ولا مرفقات.
    "apps.support",
    # سجل أفعال الإدارة — للإضافة فقط
    "apps.audit",
    # الإشعارات: أجهزة FCM، قائمة الإشعارات، الرسائل العامة
    "apps.notifications",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # ملفات لوحة Admin الساكنة تحت gunicorn — بلا خادم ملفات منفصل.
    "whitenoise.middleware.WhiteNoiseMiddleware",
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
        "DIRS": [BASE_DIR / "templates"],
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

# سياسة كلمات السر — تسري على كلمات سر الإدارة (العملاء بلا كلمات سر)
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
     "OPTIONS": {"user_attributes": ("phone", "email", "full_name")}},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ------------------------------------------------------------
# دخول الإدارة — بريد/هاتف + كلمة سر + رمز SMS (قرار PO — 2026-09-25)
# ------------------------------------------------------------
ADMIN_MAX_FAILED_LOGINS = config("ADMIN_MAX_FAILED_LOGINS", default=5, cast=int)
ADMIN_LOCKOUT_MINUTES = config("ADMIN_LOCKOUT_MINUTES", default=15, cast=int)
# الجهاز الذي اجتاز رمز SMS يُعفى منه هذه المدة (كلمة السر تبقى مطلوبة)
ADMIN_TRUSTED_DEVICE_DAYS = config("ADMIN_TRUSTED_DEVICE_DAYS", default=30, cast=int)

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
# collectstatic يجمع هنا، و WhiteNoise يقدّمها (لوحة Admin فقط — الواجهة API).
STATIC_ROOT = BASE_DIR / "staticfiles"

# ------------------------------------------------------------
# JWT Configuration (Auth Foundation — Phase 0)
# ------------------------------------------------------------
from datetime import timedelta

NINJA_JWT = {
    "SIGNING_KEY": config("JWT_SIGNING_KEY"),
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=config("JWT_ACCESS_TOKEN_LIFETIME_MIN", default=15, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_TOKEN_LIFETIME_DAYS", default=7, cast=int)),
    # 🔒 تدوير: كل تجديد يعيد refresh جديدًا ويُبطل القديم، فالتوكن المسروق
    #    يصلح مرة واحدة على الأكثر.
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

# ------------------------------------------------------------
# Provider Adapters
# ------------------------------------------------------------
# مزوّد SMS: ClickSend متاح الآن كتنفيذ فعلي، والافتراضي يبقى الـadapter
# التطويري الذي يطبع الرمز في الـconsole. التبديل بمتغيّر بيئة وحده:
#   SMS_ADAPTER=adapters.sms.clicksend.ClickSendAdapter
# لا تعديل كود في طبقة الـDomain عند التبديل.
SMS_ADAPTER = config(
    "SMS_ADAPTER",
    default="adapters.sms.dev_console.DevConsoleSMSAdapter",
)

# صمّام أمان: DevConsoleSMSAdapter يرفض العمل عند DEBUG=False إلا إذا
# فُعّل هذا الخيار صراحةً (مطلوب في بيئة الاختبارات الآلية).
SMS_DEV_ALLOW_INSECURE = config("SMS_DEV_ALLOW_INSECURE", default=False, cast=bool)

# --- ClickSend (يُقرأ فقط عندما يكون SMS_ADAPTER هو ClickSendAdapter) ---
# 🔒 لا قيمة حقيقية هنا إطلاقًا: الاعتماد يأتي من البيئة، والافتراضي فارغ.
#    ClickSendAdapter يسقط بـImproperlyConfigured إن بقي أيٌّ منها فارغًا،
#    فلا إرسال باعتماد فارغ ولا فشل صامت.
# ⚠️ Sender ID لا يُختار هنا — أيًّا كانت القيمة فهي قرار تشغيلي خارجي.
CLICKSEND_USERNAME = config("CLICKSEND_USERNAME", default="")
CLICKSEND_API_KEY = config("CLICKSEND_API_KEY", default="")
CLICKSEND_SENDER_ID = config("CLICKSEND_SENDER_ID", default="")

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
# Payment Provider (Payment Domain — §36.4، §8؛ Infra §2)
# ------------------------------------------------------------
# 🟡 مزوّد الدفع (PSP) قرار مفتوح. لا تنفيذ حقيقي في كود الإنتاج.
# الافتراضي هنا adapter وهمي للتطوير/الاختبار فقط. عند حسم المزوّد
# يُستبدل هذا المسار فقط — دون تعديل كود الـDomain.
PAYMENT_PROVIDER_ADAPTER_CLASS = config(
    "PAYMENT_PROVIDER_ADAPTER_CLASS",
    default="apps.payments.adapters.fake_adapter.FakePaymentAdapter",
)

# صمّام أمان: FakePaymentAdapter يرفض العمل عند DEBUG=False إلا إذا فُعّل
# هذا الخيار صراحةً (مطلوب في بيئة الاختبارات الآلية) — نفس نمط
# SMS_DEV_ALLOW_INSECURE و SOCIAL_AUTH_ALLOW_FAKE.
PAYMENTS_ALLOW_FAKE_ADAPTER = config(
    "PAYMENTS_ALLOW_FAKE_ADAPTER", default=False, cast=bool
)

# ------------------------------------------------------------
# Storage Provider (Jobs Domain — Infra §7)
# ------------------------------------------------------------
# 🟢 مزوّد التخزين (S3 أو مشابه) قرار مفتوح. لا تنفيذ حقيقي في كود الإنتاج.
# الافتراضي هنا adapter وهمي لا يخزّن شيئًا — للتطوير/الاختبار فقط.
JOB_STORAGE_ADAPTER_CLASS = config(
    "JOB_STORAGE_ADAPTER_CLASS",
    default="apps.jobs.adapters.fake_adapter.FakeStorageAdapter",
)

# صمّام أمان: FakeStorageAdapter يهمل محتوى الملفات، فيرفض العمل عند
# DEBUG=False إلا إذا فُعّل هذا الخيار صراحةً — نفس نمط
# PAYMENTS_ALLOW_FAKE_ADAPTER و SMS_DEV_ALLOW_INSECURE.
JOBS_ALLOW_FAKE_STORAGE_ADAPTER = config(
    "JOBS_ALLOW_FAKE_STORAGE_ADAPTER", default=False, cast=bool
)

# ------------------------------------------------------------
# Payout Provider (Payout Domain — §36.5)
# ------------------------------------------------------------
# 🟡 مزوّد الدفع للمقاولين قرار مفتوح. لا تنفيذ حقيقي في كود الإنتاج.
# الافتراضي هنا adapter وهمي لا يحوّل أي مبلغ — للتطوير/الاختبار فقط.
PAYOUT_PROVIDER_ADAPTER_CLASS = config(
    "PAYOUT_PROVIDER_ADAPTER_CLASS",
    default="apps.payouts.adapters.fake_adapter.FakePayoutAdapter",
)

# صمّام أمان: FakePayoutAdapter يرفض العمل عند DEBUG=False إلا إذا فُعّل
# هذا الخيار صراحةً — نفس نمط PAYMENTS_ALLOW_FAKE_ADAPTER.
PAYOUTS_ALLOW_FAKE_ADAPTER = config(
    "PAYOUTS_ALLOW_FAKE_ADAPTER", default=False, cast=bool
)

# ------------------------------------------------------------
# Directions Provider (§9 — route distance and ETA)
# ------------------------------------------------------------
# 🔴 مزوّد الاتجاهات (Google Directions / Mapbox) قرار مفتوح. haversine
#    يعطي مسافة خط مستقيم فقط — لا مسار شوارع ولا زمن وصول.
DIRECTIONS_PROVIDER_ADAPTER_CLASS = config(
    "DIRECTIONS_PROVIDER_ADAPTER_CLASS",
    default="adapters.directions.fake.FakeDirectionsAdapter",
)

# صمّام أمان: FakeDirectionsAdapter يشتق الـETA من سرعة مفترضة، فيرفض
# العمل عند DEBUG=False إلا إذا فُعّل هذا الخيار صراحةً.
DIRECTIONS_ALLOW_FAKE_ADAPTER = config(
    "DIRECTIONS_ALLOW_FAKE_ADAPTER", default=False, cast=bool
)

# ⚠️ الارتداد إلى haversine حين يتعذّر المسار (§9).
#    القرار صريح لا صامت: العرض يخزّن distance_source، فيظهر في تدقيق
#    الإدارة بأي أساس حُسب السعر. الافتراضي False — الإنتاج لا يرتدّ
#    ضمنيًا إلى مسافة خط مستقيم دون سياسة معلنة.
DISPATCH_ALLOW_HAVERSINE_FALLBACK = config(
    "DISPATCH_ALLOW_HAVERSINE_FALLBACK", default=False, cast=bool
)

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
# حدود الطلب: الحد اليومي لكل رقم يمنع تجاوز حد المحاولات بطلب رموز
# متتالية، والحد الساعي لكل IP يمنع استنزاف رصيد SMS. 0 = بلا حد.
OTP_MAX_REQUESTS_PER_PHONE_PER_DAY = config(
    "OTP_MAX_REQUESTS_PER_PHONE_PER_DAY", default=10, cast=int
)
OTP_MAX_REQUESTS_PER_IP_PER_HOUR = config(
    "OTP_MAX_REQUESTS_PER_IP_PER_HOUR", default=20, cast=int
)


def _parse_otp_test_numbers(raw):
    """
    "+61400000001:123456,+61400000002:654321" → {phone: code}.

    🧪 أرقام اختبار برمز ثابت ولا ترسل SMS — لتجربة الدخول قبل التعاقد مع
       مزوّد SMS. تنطبق على هذه الأرقام وحدها؛ كل رقم آخر يبقى محميًا.
    ⚠️ استخدم أرقامًا غير مملوكة لأحد، ولا تمنح حساباتها صلاحية ADMIN.
    """
    from django.core.exceptions import ImproperlyConfigured

    numbers = {}
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        phone, sep, code = entry.partition(":")
        phone, code = phone.strip(), code.strip()
        if not sep or not phone or not code.isdigit() or len(code) != OTP_CODE_LENGTH:
            raise ImproperlyConfigured(
                f"OTP_TEST_NUMBERS entry {entry!r} must look like "
                f"+61400000001:{'1' * OTP_CODE_LENGTH} (code of OTP_CODE_LENGTH digits)."
            )
        numbers[phone] = code
    return numbers


OTP_TEST_NUMBERS = _parse_otp_test_numbers(config("OTP_TEST_NUMBERS", default=""))

# ⚠️ وضع التجريب قبل التعاقد مع مزوّد SMS (قرار PO — 2026-09-25):
#   OTP_TEST_NUMBERS_ALLOW_ADMIN: يسمح لأرقام الاختبار أن تكون العامل الثاني
#     لدخول الأدمن أيضًا (كلمة السر تبقى مطلوبة). مغلق افتراضيًا.
#   OTP_TEST_MODE_UNTIL: تاريخ (YYYY-MM-DD) تتوقف بعده أرقام الاختبار كلها
#     تلقائيًا — حتى لو نُسي حذفها. فارغ = بلا تاريخ انتهاء.
OTP_TEST_NUMBERS_ALLOW_ADMIN = config("OTP_TEST_NUMBERS_ALLOW_ADMIN", default=False, cast=bool)
OTP_TEST_MODE_UNTIL = config("OTP_TEST_MODE_UNTIL", default="")

# عدد البروكسيات الموثوقة أمام التطبيق — لاستخراج IP العميل الحقيقي من
# X-Forwarded-For (يُستخدم في حد طلبات OTP). 0 = REMOTE_ADDR مباشرةً.
# ⚠️ لا ترفعه فوق العدد الحقيقي: الترويسة يمكن للعميل تزويرها.
NUM_TRUSTED_PROXIES = config("NUM_TRUSTED_PROXIES", default=0, cast=int)

# ------------------------------------------------------------
# Dispatch & Scheduling (قرارات Product Owner — 2026-09-24)
# ------------------------------------------------------------
# أقصى مسافة بين المقاول والعقار لإرسال عرض. بدونها قد يُرسل حجز في
# سيدني لمقاول في بيرث، وتُضاف كلفة 3000+ كم على السعر.
DISPATCH_MAX_DISTANCE_KM = config("DISPATCH_MAX_DISTANCE_KM", default=50, cast=int)
# أقل مهلة بين الآن وموعد الزيارة عند الإنشاء وإعادة الجدولة. قبول عرض
# بعد فوات الموعد مرفوض دائمًا بغض النظر عن هذه القيمة.
BOOKING_MIN_LEAD_MINUTES = config("BOOKING_MIN_LEAD_MINUTES", default=120, cast=int)
# ساعات العمل 07:00–19:00 على الطلب الفوري (قرار PO — 2026-09-26)
ON_DEMAND_ENFORCE_BUSINESS_HOURS = config("ON_DEMAND_ENFORCE_BUSINESS_HOURS", default=True, cast=bool)
# مسافة قبول "وصلت" من العقار بالأمتار (+ حتى 100 م من دقة GPS المُبلَّغة)
JOB_ARRIVAL_RADIUS_M = config("JOB_ARRIVAL_RADIUS_M", default=300, cast=int)

# ------------------------------------------------------------
# Job photos — حدود الرفع
# ------------------------------------------------------------
JOB_PHOTO_MAX_BYTES = config("JOB_PHOTO_MAX_BYTES", default=10 * 1024 * 1024, cast=int)
JOB_PHOTO_MAX_PER_JOB = config("JOB_PHOTO_MAX_PER_JOB", default=30, cast=int)

# ------------------------------------------------------------
# Push Notifications — Firebase Cloud Messaging (قرار PO — 2026-09-25)
# ------------------------------------------------------------
PUSH_ADAPTER = config("PUSH_ADAPTER", default="adapters.push_notification.fcm.FCMPushAdapter")
# حساب الخدمة: المحتوى كاملًا (الاستضافة) أو مسار الملف (محليًا). سرّ — لا يُرفع.
FIREBASE_CREDENTIALS_JSON = config("FIREBASE_CREDENTIALS_JSON", default="")
FIREBASE_CREDENTIALS_FILE = config("FIREBASE_CREDENTIALS_FILE", default="")
# صمّام FakePushAdapter عند DEBUG=False (الاختبارات فقط)
PUSH_ALLOW_FAKE_ADAPTER = config("PUSH_ALLOW_FAKE_ADAPTER", default=False, cast=bool)
# "inline" بلا عامل خلفي (الافتراضي)، أو "celery" حين يعمل عامل Celery + Redis
NOTIFICATIONS_DELIVERY = config("NOTIFICATIONS_DELIVERY", default="inline")
NOTIFICATIONS_MAX_PUSH_ATTEMPTS = config("NOTIFICATIONS_MAX_PUSH_ATTEMPTS", default=5, cast=int)
NOTIFICATIONS_RETENTION_DAYS = config("NOTIFICATIONS_RETENTION_DAYS", default=90, cast=int)
NOTIFICATIONS_STALE_DEVICE_DAYS = config("NOTIFICATIONS_STALE_DEVICE_DAYS", default=270, cast=int)
# قنوات Android حسب الأولوية — تُعدَّل حين يحدّد تطبيق الموبايل أسماءه
NOTIFICATION_ANDROID_CHANNELS = {
    "HIGH": config("NOTIFICATION_CHANNEL_HIGH", default="offers"),
    "NORMAL": config("NOTIFICATION_CHANNEL_NORMAL", default="general"),
}

# ------------------------------------------------------------
# Celery / Redis
# ------------------------------------------------------------
CELERY_BROKER_URL = config("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = config("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# ------------------------------------------------------------
# Celery Beat — المهام الدورية
# ------------------------------------------------------------
# انتهاء مهلة عروض الإسناد (§36.6): العرض غير المُجاب عليه خلال 60 دقيقة
# يُعامل معاملة الرفض ويُطلق التتابع نفسه. المهمة تكرارية (idempotent)
# حسب Infra §15 — تشغيلها مرتين لا يُنتج تتابعًا مكرَّرًا.
from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    "expire-pending-dispatch-offers": {
        "task": "bookings.expire_pending_offers",
        # كل دقيقة: المهلة 60 دقيقة، فالدقة بالدقيقة كافية ورخيصة
        "schedule": crontab(minute="*"),
    },
    # شبكة أمان للآثار الجانبية بعد التأكيد (مهمة/شحن/دفع للمقاول)
    "repair-confirmed-bookings": {
        "task": "bookings.repair_confirmed_bookings",
        "schedule": crontab(minute="*/5"),
    },
    "retry-pending-notifications": {
        "task": "notifications.retry_pending",
        "schedule": crontab(minute="*/5"),
    },
    "cleanup-notifications": {
        "task": "notifications.cleanup",
        "schedule": crontab(hour=3, minute=30),
    },
}

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
