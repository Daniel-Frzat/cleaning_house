"""
Roles — Identity Domain (Phase 1)

الأدوار المؤكدة في المرجع المعماري (قسم 19):
    Customer, Contractor, Admin

✅ PropertyManager: قرار محسوم — PropertyManager = CUSTOMER
   (راجع Change Set — قسم 12). لا يُعرَّف كـRole مستقل، ولا يُضاف إلى
   هذا الـEnum. أي سلوك خاص بمدير العقار يُبنى فوق حساب Customer.

⚠️ هذا هو المصدر الوحيد لتعريف الأدوار (Single Source of Truth).
   لا تُعِد تعريف هذا الـEnum في أي مكان آخر — استورده من هنا.
"""

from django.db import models


class ConfirmedRole(models.TextChoices):
    CUSTOMER = "CUSTOMER", "Customer"
    CONTRACTOR = "CONTRACTOR", "Contractor"
    ADMIN = "ADMIN", "Admin"
