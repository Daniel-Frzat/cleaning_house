"""
JWT Authentication — Identity Domain

ActiveUserJWTAuth هو الـauth الوحيد المسموح في كل الـrouters.

⚠️ لماذا لا نستخدم ninja_jwt.JWTAuth مباشرةً؟
   JWTAuth يفحص is_active فقط. الإيقاف الإداري في هذا المشروع يتم عبر
   status=SUSPENDED/INACTIVE (حقل منفصل عمدًا عن is_active — راجع
   models.User). بدون هذا الفحص يبقى الحساب الموقوف قادرًا على استخدام
   الـAPI حتى ينتهي توكنه.

🔒 الفحص يقرأ الحساب من قاعدة البيانات في كل طلب — لا يثق بـclaim
   "status" داخل التوكن، لأنها نسخة من لحظة الإصدار وقد تكون قديمة.
"""

from django.utils.translation import gettext_lazy as _
from ninja_jwt.authentication import JWTAuth
from ninja_jwt.exceptions import AuthenticationFailed

from .models import UserStatus

# المسارات المسموحة لحساب عليه must_change_password — تغيير كلمة السر
# وقراءة الحساب فقط.
PASSWORD_CHANGE_ALLOWED_PATHS = (
    "/api/admin/auth/",
    "/api/auth/me",
)


class AuthzError(Exception):
    """
    رفض صلاحية بعد مصادقة ناجحة — يُترجم إلى 403 بشكل ErrorOut الموحّد
    (معالج الاستثناء في config/urls.py).
    """

    def __init__(self, code, detail, status=403):
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(detail)


class ActiveUserJWTAuth(JWTAuth):
    """
    JWTAuth + رفض أي حساب حالته ليست ACTIVE + فرض تغيير كلمة السر المؤقتة.
    """

    def authenticate(self, request, token):
        user = super().authenticate(request, token)
        if user is not None and user.must_change_password:
            if not request.path.startswith(PASSWORD_CHANGE_ALLOWED_PATHS):
                raise AuthzError(
                    "password_change_required",
                    "Your password was reset. Change it before using the API.",
                )
        return user

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if user.status != UserStatus.ACTIVE:
            raise AuthenticationFailed(_("User is not active"))
        return user


class AdminJWTAuth(ActiveUserJWTAuth):
    """
    لمسارات لوحة التحكم (/api/admin/*): ADMIN حصرًا.

    📌 الخدمات تعيد فحص الدور بنفسها (طبقتان)، لكن هذا يرفض غير الأدمن قبل
       أن يصل أي منطق.
    """

    def authenticate(self, request, token):
        user = super().authenticate(request, token)
        if user is not None and not user.has_admin_access():
            raise AuthzError("admin_required", "Only administrators can access this endpoint.")
        return user


class SuperuserJWTAuth(AdminJWTAuth):
    """إدارة حسابات الأدمن — Superuser حصرًا."""

    def authenticate(self, request, token):
        user = super().authenticate(request, token)
        if user is not None and not user.is_superuser:
            raise AuthzError("superuser_required", "Only a superuser can manage administrators.")
        return user
