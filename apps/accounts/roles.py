"""
Roles Skeleton — Phase 0 (Auth Foundation)

يحتوي فقط على الأدوار المؤكدة صراحة في المرجع المعماري (قسم 19):
    Customer, Contractor, Admin

⚠️ PropertyManager غير مُدرَج هنا عمدًا — هو 🔴 Blocking Decision
(راجع قسم 27 → البند #5) ولم يُحسم بعد ما إذا كان:
    - Role مستقل ضمن نظام الصلاحيات
    - أم صفة إضافية على حساب Customer موجود
    - أم كيان مختلف تمامًا

لا تضف PropertyManager هنا حتى يُحسم القرار صراحة مع Daniel.
"""

from django.db import models


class ConfirmedRole(models.TextChoices):
    CUSTOMER = "CUSTOMER", "Customer"
    CONTRACTOR = "CONTRACTOR", "Contractor"
    ADMIN = "ADMIN", "Admin"