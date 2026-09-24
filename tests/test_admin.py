"""
Django Admin — Identity Domain (Phase 1 — Step 1)

يتحقق أن الـAdmin يعمل مع User المخصص (بلا username):
  - /admin/ يفتح
  - قائمة المستخدمين تُعرض دون خطأ
  - صفحات الإضافة والتعديل تعمل
"""

import pytest
from django.contrib import admin
from django.test import Client

from apps.accounts.models import User


@pytest.fixture
def admin_client(db):
    user = User.objects.create_superuser(phone="+96551110000", password="AdminPass123!")
    client = Client()
    client.force_login(user)
    return client, user


@pytest.mark.django_db
def test_user_model_is_registered():
    assert User in admin.site._registry
    model_admin = admin.site._registry[User]
    assert model_admin.list_display == ("phone", "role", "is_contractor", "status", "is_active")


@pytest.mark.django_db
def test_admin_index_loads(admin_client):
    client, _ = admin_client
    response = client.get("/admin/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_admin_user_changelist_renders(admin_client):
    """صفحة قائمة المستخدمين — أكثر موضع يفشل عند User مخصص."""
    client, user = admin_client
    response = client.get("/admin/accounts/user/")
    assert response.status_code == 200
    content = response.content.decode()
    assert user.phone in content


@pytest.mark.django_db
def test_admin_user_add_page_renders(admin_client):
    client, _ = admin_client
    response = client.get("/admin/accounts/user/add/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_admin_user_change_page_renders(admin_client):
    client, user = admin_client
    response = client.get(f"/admin/accounts/user/{user.pk}/change/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_admin_changelist_filters_and_search(admin_client):
    client, _ = admin_client
    assert client.get("/admin/accounts/user/?role=ADMIN").status_code == 200
    assert client.get("/admin/accounts/user/?q=9655").status_code == 200
