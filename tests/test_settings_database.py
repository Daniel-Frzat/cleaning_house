"""
Database configuration tests — config/settings/database.py

يغطي: قراءة DATABASE_URL، الرجوع إلى متغيرات DB_* عند غيابها، وفك
ترميز كلمة المرور.

📌 السبب: هذه الدالة تُقرأ مرة واحدة عند الإقلاع، وخطؤها لا يظهر
   كاستثناء واضح بل كفشل اتصال غامض بالإنتاج — وهو بالضبط ما استغرق
   وقتًا طويلًا عند أول نشر على Railway.

⚠️ الاختبارات تتلاعب بمتغيرات البيئة مباشرةً لأن decouple.config يقرأ
   os.environ أولًا. كل اختبار ينظّف ما غيّره عبر fixture.
"""

import os

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.database import POSTGRES_ENGINE, build_databases

DB_VARS = ("DATABASE_URL", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT")


@pytest.fixture
def clean_env(monkeypatch):
    """يزيل كل متغيرات قاعدة البيانات، فيبدأ كل اختبار من صفحة بيضاء."""
    for name in DB_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def set_discrete(env, **overrides):
    values = {
        "DB_NAME": "railway",
        "DB_USER": "postgres",
        "DB_PASSWORD": "secret",
        "DB_HOST": "postgres.railway.internal",
        "DB_PORT": "5432",
    }
    values.update(overrides)
    for k, v in values.items():
        if v is None:
            env.delenv(k, raising=False)
        else:
            env.setenv(k, v)


# ============================================================
# 1) DATABASE_URL
# ============================================================
def test_database_url_is_parsed_into_django_settings(clean_env):
    clean_env.setenv(
        "DATABASE_URL",
        "postgres://alice:pw123@db.internal:6543/appdb",
    )

    default = build_databases()["default"]

    assert default["ENGINE"] == POSTGRES_ENGINE
    assert default["NAME"] == "appdb"
    assert default["USER"] == "alice"
    assert default["PASSWORD"] == "pw123"
    assert default["HOST"] == "db.internal"
    assert default["PORT"] == "6543"


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "pgsql"])
def test_all_postgres_schemes_are_accepted(clean_env, scheme):
    """المنصات تستخدم أسماء مختلفة للمخطط نفسه."""
    clean_env.setenv("DATABASE_URL", f"{scheme}://u:p@h:5432/n")

    assert build_databases()["default"]["NAME"] == "n"


def test_missing_port_falls_back_to_5432(clean_env):
    clean_env.setenv("DATABASE_URL", "postgres://u:p@h/n")

    assert build_databases()["default"]["PORT"] == "5432"


def test_password_is_url_decoded(clean_env):
    """
    🔒 الرموز الخاصة تصل مرمَّزة في الرابط.

    تمريرها كما هي يعني مصادقة فاشلة برسالة مضلّلة
    (fe_sendauth / password authentication failed).
    """
    clean_env.setenv("DATABASE_URL", "postgres://u:p%40ss%3Aword@h:5432/n")

    assert build_databases()["default"]["PASSWORD"] == "p@ss:word"


def test_username_is_url_decoded(clean_env):
    clean_env.setenv("DATABASE_URL", "postgres://user%40tenant:p@h:5432/n")

    assert build_databases()["default"]["USER"] == "user@tenant"


def test_unsupported_scheme_is_rejected_loudly(clean_env):
    """مشروع PostgreSQL — رابط MySQL خطأ إعداد لا يُبتلع."""
    clean_env.setenv("DATABASE_URL", "mysql://u:p@h:3306/n")

    with pytest.raises(ImproperlyConfigured) as exc:
        build_databases()

    assert "unsupported scheme" in str(exc.value).lower()


def test_url_without_a_database_name_is_rejected(clean_env):
    clean_env.setenv("DATABASE_URL", "postgres://u:p@h:5432")

    with pytest.raises(ImproperlyConfigured) as exc:
        build_databases()

    assert "no database name" in str(exc.value).lower()


# ============================================================
# 2) الرجوع إلى DB_* المنفصلة
# ============================================================
def test_discrete_vars_are_used_when_no_url(clean_env):
    """⚠️ السلوك الأصلي لا يُكسر: النشرات القائمة تعمل كما هي."""
    set_discrete(clean_env)

    default = build_databases()["default"]

    assert default["NAME"] == "railway"
    assert default["USER"] == "postgres"
    assert default["PASSWORD"] == "secret"
    assert default["HOST"] == "postgres.railway.internal"
    assert default["PORT"] == "5432"


def test_host_and_port_keep_their_defaults(clean_env):
    set_discrete(clean_env, DB_HOST=None, DB_PORT=None)

    default = build_databases()["default"]

    assert default["HOST"] == "localhost"
    assert default["PORT"] == "5432"


def test_missing_required_discrete_var_raises(clean_env):
    """
    🔒 لا قيمة افتراضية لاسم القاعدة: بيئة بلا بيانات اتصال يجب أن
       تفشل عند الإقلاع لا أن تعمل على قاعدة خاطئة.
    """
    set_discrete(clean_env, DB_NAME=None)

    with pytest.raises(Exception):
        build_databases()


# ============================================================
# 3) الأولوية والحالات الحدّية
# ============================================================
def test_url_wins_over_discrete_vars(clean_env):
    set_discrete(clean_env, DB_NAME="from_discrete")
    clean_env.setenv("DATABASE_URL", "postgres://u:p@h:5432/from_url")

    assert build_databases()["default"]["NAME"] == "from_url"


def test_empty_url_falls_back_instead_of_breaking(clean_env):
    """
    📌 الحالة التي كسرت أول نشر: المنصة تُعرّف المتغيّر بقيمة خالية حين
       يفشل حلّ مرجع، وقبوله كان سيُنتج إعدادات فارغة بصمت.
    """
    set_discrete(clean_env)
    clean_env.setenv("DATABASE_URL", "")

    assert build_databases()["default"]["NAME"] == "railway"


def test_whitespace_only_url_also_falls_back(clean_env):
    set_discrete(clean_env)
    clean_env.setenv("DATABASE_URL", "   ")

    assert build_databases()["default"]["NAME"] == "railway"


def test_url_is_trimmed_before_parsing(clean_env):
    """لصق رابط بمسافة زائدة لا يكسر التحليل."""
    clean_env.setenv("DATABASE_URL", "  postgres://u:p@h:5432/n  ")

    assert build_databases()["default"]["NAME"] == "n"


def test_engine_is_always_postgresql(clean_env):
    """المحرّك ثابت — لا يُشتق من الرابط ولا من متغيّر."""
    clean_env.setenv("DATABASE_URL", "postgres://u:p@h:5432/n")
    assert build_databases()["default"]["ENGINE"] == POSTGRES_ENGINE

    clean_env.delenv("DATABASE_URL")
    set_discrete(clean_env)
    assert build_databases()["default"]["ENGINE"] == POSTGRES_ENGINE


def test_result_shape_has_exactly_the_django_keys(clean_env):
    clean_env.setenv("DATABASE_URL", "postgres://u:p@h:5432/n")

    result = build_databases()

    assert set(result) == {"default"}
    assert set(result["default"]) == {
        "ENGINE", "NAME", "USER", "PASSWORD", "HOST", "PORT"
    }


# ============================================================
# 4) تكامل ملفات الإعدادات
# ============================================================
def test_production_and_staging_both_use_the_helper():
    """
    كلاهما يستدعي build_databases بدل تكرار الكتلة — وإلا انحرف أحدهما.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "config" / "settings"
    for name in ("production.py", "staging.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert "build_databases()" in src, name
        # لا نسخة يدوية متبقية
        assert 'config("DB_NAME")' not in src, name
