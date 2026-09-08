"""
Phase 0 Smoke Test

هدفه فقط التأكد أن:
  - Django يعمل
  - Ninja API متصلة بشكل صحيح
  - Endpoint الأساسي /api/health يستجيب

لا يختبر أي Business Logic (لا يوجد بعد).
"""

import pytest
from django.test import Client


@pytest.mark.django_db
def test_health_check_endpoint():
    client = Client()
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"