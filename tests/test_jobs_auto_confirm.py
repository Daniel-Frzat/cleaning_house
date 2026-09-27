"""
العميل لا يؤكد انتهاء التنظيف (قرار PO — 2026-09-27):
تذكير بعد 6 ساعات من "انتهيت"، وتأكيد تلقائي بعد 12 يُطلق دفعة المقاول.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.jobs.models import Job, JobStatus
from apps.jobs.services import jobs as jobs_svc
from apps.notifications.models import Notification
from apps.payouts.models import Payout
from tests.test_jobs_completion import (  # noqa: F401
    add_both_photos, auth, client, contractor, customer, job, service_type,
)


def awaiting(job, contractor, hours_ago):
    user, _ = contractor
    add_both_photos(user, job)
    jobs_svc.mark_job_done(job, user)
    Job.objects.filter(pk=job.pk).update(marked_done_at=timezone.now() - timedelta(hours=hours_ago))
    job.refresh_from_db()
    return job


@pytest.mark.django_db
def test_nothing_happens_before_six_hours(job, contractor, django_capture_on_commit_callbacks):
    job = awaiting(job, contractor, 5)
    with django_capture_on_commit_callbacks(execute=True):
        assert jobs_svc.send_confirmation_reminders() == 0
        assert jobs_svc.auto_confirm_overdue_jobs() == 0
    job.refresh_from_db()
    assert job.status == JobStatus.AWAITING_CUSTOMER_CONFIRMATION


@pytest.mark.django_db
def test_one_reminder_after_six_hours(job, contractor, django_capture_on_commit_callbacks):
    job = awaiting(job, contractor, 7)
    with django_capture_on_commit_callbacks(execute=True):
        assert jobs_svc.send_confirmation_reminders() == 1
        assert jobs_svc.send_confirmation_reminders() == 0  # مرة واحدة فقط
    reminders = Notification.objects.filter(user=job.booking.customer, type="job.confirmation_reminder")
    assert reminders.count() == 1
    assert "6 hours" in reminders.get().body
    assert jobs_svc.auto_confirm_overdue_jobs() == 0


@pytest.mark.django_db
def test_auto_confirmed_after_twelve_hours_and_payout_released(job, contractor, django_capture_on_commit_callbacks):
    job = awaiting(job, contractor, 12)
    with django_capture_on_commit_callbacks(execute=True):
        assert jobs_svc.auto_confirm_overdue_jobs() == 1

    job.refresh_from_db()
    assert job.status == JobStatus.COMPLETED
    assert job.auto_confirmed is True and job.confirmed_at
    assert Payout.objects.filter(booking=job.booking).exists()
    assert Notification.objects.filter(user=job.booking.customer, type="job.auto_confirmed").exists()
    # المهمة المؤكَّدة لا تأخذ تذكيرًا متأخرًا
    assert jobs_svc.send_confirmation_reminders() == 0


@pytest.mark.django_db
def test_customer_confirmation_is_not_marked_automatic(job, contractor):
    job = awaiting(job, contractor, 1)
    jobs_svc.confirm_job_completion(job, job.booking.customer)
    job.refresh_from_db()
    assert job.auto_confirmed is False
    assert jobs_svc.auto_confirm_overdue_jobs() == 0


@pytest.mark.django_db
def test_job_api_shows_the_auto_confirm_deadline(client, job, contractor):
    job = awaiting(job, contractor, 2)
    body = client.get(f"/api/bookings/{job.booking_id}/job", **auth(job.booking.customer)).json()
    deadline = job.marked_done_at + timedelta(hours=12)
    assert body["auto_confirm_at"].replace("Z", "+00:00")[:19] == deadline.isoformat()[:19]
    assert body["auto_confirmed"] is False


@pytest.mark.django_db
def test_periodic_command_runs_both_tasks(job, contractor):
    from io import StringIO

    from django.core.management import call_command

    awaiting(job, contractor, 13)
    out = StringIO()
    call_command("run_periodic_tasks", stdout=out)
    assert "auto_confirm_overdue_jobs: 1" in out.getvalue()
    assert "send_confirmation_reminders: 0" in out.getvalue()
