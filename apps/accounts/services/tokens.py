"""
JWT Token Service — Identity Domain (Phase 1)

مكان واحد لإصدار التوكنات، حتى تكون الـclaims موحّدة عبر كل مسارات الدخول
(OTP / Apple / Google).

⚠️ لا يوجد هنا تسجيل دخول بكلمة مرور — نموذج المصادقة المعتمد هو
   OTP + Apple + Google + JWT فقط (Change Set — قسم 21).
"""

from ninja_jwt.tokens import RefreshToken


def issue_tokens_for_user(user):
    """
    يصدر زوج access/refresh للمستخدم.

    يضيف claims إضافية (role, status, phone) لأن الـDomains اللاحقة
    (Properties, Bookings) تحتاج الدور للتحقق من الصلاحيات دون استعلام
    قاعدة بيانات في كل طلب.

    ملاحظة: الـclaims الموضوعة على refresh token تُنسخ تلقائيًا إلى
    access token (راجع RefreshToken.access_token).

    🔒 لا تُوضع في الـclaims أي بيانات حسّاسة (لا رموز OTP، ولا
       raw_profile من المزوّد).
    """
    refresh = RefreshToken.for_user(user)

    refresh["role"] = user.role
    refresh["status"] = user.status
    refresh["phone"] = user.phone

    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }
