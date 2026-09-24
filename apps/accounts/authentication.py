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


class ActiveUserJWTAuth(JWTAuth):
    """JWTAuth + رفض أي حساب حالته ليست ACTIVE."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if user.status != UserStatus.ACTIVE:
            raise AuthenticationFailed(_("User is not active"))
        return user
