"""التشغيل: فحص الجاهزية وأمر المهام الدورية."""

import pytest
from django.core.management import call_command
from django.test import Client


@pytest.mark.django_db
def test_readiness_is_ok_when_migrated():
    r = Client().get("/api/health/ready")
    assert r.status_code == 200 and r.json() == {"status": "ready"}


@pytest.mark.django_db
def test_readiness_reports_pending_migrations(monkeypatch):
    from django.db.migrations import executor

    monkeypatch.setattr(executor.MigrationExecutor, "migration_plan", lambda self, targets: [("x", False)])
    r = Client().get("/api/health/ready")
    assert r.status_code == 503 and "unapplied" in r.json()["reason"]


@pytest.mark.django_db
def test_periodic_tasks_command_runs_every_task(capsys):
    call_command("run_periodic_tasks")
    out = capsys.readouterr().out
    for name in ("expire_pending_offers", "repair_confirmed_bookings",
                 "retry_pending_notifications", "cleanup_notifications"):
        assert name in out


def test_production_settings_let_railway_healthcheck_through():
    """فحص Railway: hostname داخلي و HTTP — لا 400 ولا 301."""
    import importlib
    import os
    from unittest import mock

    env = {"SECRET_KEY": "x" * 50, "JWT_SIGNING_KEY": "y", "DATABASE_URL": "postgres://u:p@h:5432/d",
           "ALLOWED_HOSTS": "example.com", "DJANGO_ENV": "production"}
    with mock.patch.dict(os.environ, env):
        importlib.reload(importlib.import_module("config.settings.base"))
        prod = importlib.reload(importlib.import_module("config.settings.production"))
    importlib.reload(importlib.import_module("config.settings.base"))
    assert "healthcheck.railway.app" in prod.ALLOWED_HOSTS
    assert "example.com" in prod.ALLOWED_HOSTS
    assert any(__import__("re").match(p, "api/health/ready") for p in prod.SECURE_REDIRECT_EXEMPT)


@pytest.mark.django_db
def test_schema_validation_errors_use_the_uniform_error_shape():
    """B9: أخطاء شكل الطلب {code, detail, errors[]} كبقية الأخطاء."""
    import json
    import uuid

    r = Client().post("/api/auth/otp/request", data=json.dumps({}), content_type="application/json")
    body = r.json()
    assert r.status_code == 422
    assert body["code"] == "validation_error"
    assert isinstance(body["detail"], str) and "phone" in body["detail"]
    assert body["errors"][0]["field"] == "phone"
    assert body["errors"][0]["loc"][-1] == "phone"


def test_error_codes_file_is_up_to_date():
    """B10: ERROR_CODES.md يُولَّد من الكود — رمز جديد أو معدَّل يظهر في المراجعة."""
    call_command("export_error_codes", "--check")
