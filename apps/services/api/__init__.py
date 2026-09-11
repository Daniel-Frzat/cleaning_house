"""
API layer — Services Catalog & Pricing Domain.

المواصفة تسمّي المسار apps/services/api.py، لكن هذا المشروع يتبع نمط
حزمة api/ (router منفصل عن schemas) كما في apps/properties و apps/accounts.
لا يمكن وجود الاثنين معًا (تعارض اسم module مع package)، فاعتُمدت الحزمة
مع إعادة تصدير الـrouter هنا حتى يعمل الاستيراد المختصر أيضًا:

    from apps.services.api import router           # ← عبر هذا الملف
    from apps.services.api.catalog import router   # ← المسار الصريح
"""

from .catalog import router

__all__ = ["router"]
