"""
أدوات مشتركة لخدمات لوحة التحكم — تستوردها خدمات admin في كل نطاق.

🔒 طبقتان: AdminJWTAuth يرفض غير الأدمن قبل الوصول، وكل دالة خدمة
   إدارية تعيد الفحص بنفسها عبر assert_admin — الإنفاذ لا يعتمد على أن
   الواجهة اختارت الـauth الصحيح.
"""

# بادئة failure_reason التي تكتبها خدمات الدفع حين يرمي المزوّد استثناءً:
# النتيجة مجهولة (قد يكون المال تحرّك) فتبقى الدفعة معلّقة للمطابقة.
PROVIDER_ERROR_PREFIX = "provider_error"


class AdminRequiredError(Exception):
    code = "admin_required"


class SuperuserRequiredError(Exception):
    code = "superuser_required"


def assert_admin(user):
    if user is None or not user.is_authenticated or not user.has_admin_access():
        raise AdminRequiredError("Only administrators can access this endpoint.")


def assert_superuser(user):
    assert_admin(user)
    if not user.is_superuser:
        raise SuperuserRequiredError("Only a superuser can perform this action.")


def filter_date_range(queryset, field, start=None, end=None):
    """
    نطاق تواريخ شامل للطرفين على حقل DateTime.

    📌 __date يحوّل إلى المنطقة الزمنية الحالية قبل المقارنة، فـ"اليوم"
       هو يوم المنصة لا يوم UTC.
    """
    if start is not None:
        queryset = queryset.filter(**{f"{field}__date__gte": start})
    if end is not None:
        queryset = queryset.filter(**{f"{field}__date__lte": end})
    return queryset


def is_unknown_outcome(failure_reason):
    """هل تنتظر الدفعة مطابقة يدوية؟ (استثناء من المزوّد — نتيجة مجهولة)"""
    return bool(failure_reason) and failure_reason.startswith(PROVIDER_ERROR_PREFIX)
