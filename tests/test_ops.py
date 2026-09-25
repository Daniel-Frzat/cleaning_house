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
