"""
Social Login Service — Identity Domain (Phase 1)

منطق ربط هوية مزوّد خارجي (Apple/Google) بحساب User داخلي.

تسلسل الحل (resolution order) — مهم ألا يُغيَّر دون قرار صريح:
  1) البحث بـ(provider, provider_user_id) → الهوية معروفة مسبقًا.
  2) وإلا: البحث بالبريد الذي أبلغ عنه المزوّد → ربط بحساب قائم —
     🔒 فقط إذا أكّد المزوّد البريد **و** كان بريد الحساب القائم مُثبت
     الملكية (email_verified). البريد المُدخَل يدويًا عبر PATCH /auth/me
     لا يُربط به أبدًا: وإلا يضع المهاجم بريد الضحية في حسابه مسبقًا،
     فتدخل الضحية بـGoogle إلى حساب المهاجم (account pre-hijacking).
  3) وإلا: إنشاء User جديد بدور CUSTOMER. إن كان البريد مأخوذًا بحساب
     آخر يُنشأ الحساب بلا بريد (يبقى البريد على SocialAccount فقط).

⚠️ قرار محسوم: التسجيل الذاتي عبر Social Login ينشئ CUSTOMER فقط.
   أدوار CONTRACTOR/ADMIN تتطلب مسارات تحقق منفصلة لم تُبنَ بعد،
   ولا يجوز الحصول عليها عبر هذا المسار.

⚠️ لا يوجد هنا أي اعتماد (credentials) لـApple/Google — التحقق من التوكن
   مسؤولية الـAdapter، ويُضبط عبر settings.SOCIAL_AUTH_ADAPTER.
"""

import hashlib
import logging

from django.db import IntegrityError, transaction

from adapters.social_auth import SocialAuthError, get_social_auth_adapter

from ..models import SocialAccount, SocialProvider, User, UserStatus
from ..roles import ConfirmedRole

logger = logging.getLogger(__name__)


class SocialLoginError(Exception):
    """أصل أخطاء تسجيل الدخول الاجتماعي."""

    code = "social_login_error"


class UnsupportedProviderError(SocialLoginError):
    code = "unsupported_provider"


class ProviderAlreadyLinkedError(SocialLoginError):
    """الحساب المطابق مرتبط مسبقًا بهوية أخرى لدى المزوّد نفسه."""

    code = "provider_already_linked"


class InactiveUserError(SocialLoginError):
    """الحساب موجود لكنه معطّل/موقوف — لا يُمنح دخول."""

    code = "inactive_user"


def _placeholder_phone(provider, provider_user_id):
    """
    phone حقل إلزامي وفريد على User، لكن مستخدم Social Login قد لا يملك
    رقمًا بعد. نولّد قيمة نائبة مستقرة ومميّزة حتى يُكمل المستخدم ملفه.

    الشكل مقصود أن يكون غير صالح كرقم هاتف حقيقي، حتى لا يُخلط بينه وبين
    رقم مُتحقَّق منه عبر OTP.

    ⚠️ hash لا اقتطاع: الحقل 32 حرفًا، واقتطاع sub الخاص بـGoogle (21
       رقمًا) كان يُبقي 18 رقمًا فقط، فيتصادم مستخدمان يشتركان في البادئة.
    """
    prefix = f"social:{provider.lower()}:"
    digest = hashlib.sha256(provider_user_id.encode()).hexdigest()
    return prefix + digest[: 32 - len(prefix)]


def _find_by_email(email):
    """بحث عن مستخدم قائم بالبريد (غير حسّاس لحالة الأحرف)."""
    if not email:
        return None
    return User.objects.filter(email__iexact=email.strip()).first()


def _find_linkable_by_email(profile):
    """
    الحساب القائم الذي يجوز ربط الهوية به عبر البريد — أو None.

    🔒 شرطان معًا: المزوّد أكّد البريد، والحساب القائم أثبت ملكيته.
    """
    if not profile.email or not profile.email_verified:
        return None
    user = _find_by_email(profile.email)
    if user is not None and user.email_verified:
        return user
    return None


@transaction.atomic
def login_with_provider(provider, token):
    """
    نقطة الدخول الوحيدة لتسجيل الدخول الاجتماعي.

    يتحقق من التوكن عبر الـAdapter، ثم يحل الهوية إلى User.

    يعيد (user, social_account, created) حيث created يشير إلى أن مستخدمًا
    جديدًا أُنشئ في هذه العملية.
    """
    if provider not in SocialProvider.values:
        raise UnsupportedProviderError(f"Unsupported provider: {provider}")

    # التحقق من التوكن مسؤولية الـAdapter — لا منطق مزوّد هنا
    profile = get_social_auth_adapter().verify_token(provider, token)

    if not profile.provider_user_id:
        raise SocialLoginError("Provider did not return a stable user identifier.")

    return link_or_create_user(profile)


@transaction.atomic
def link_or_create_user(profile):
    """
    يحل ProviderProfile (مُتحقَّق منه مسبقًا) إلى User.

    مفصولة عن login_with_provider حتى يمكن اختبار منطق الربط مباشرةً
    دون المرور بأي توكن.
    """
    provider = profile.provider
    provider_user_id = profile.provider_user_id

    # ---- 1) الهوية معروفة مسبقًا ----
    existing = (
        SocialAccount.objects.select_related("user")
        .filter(provider=provider, provider_user_id=provider_user_id)
        .first()
    )
    if existing is not None:
        _assert_user_can_login(existing.user)
        # نحدّث البيانات المُبلَّغ عنها (قد تتغير لدى المزوّد)
        _refresh_reported_profile(existing, profile)
        logger.info("Social login matched existing identity (social_id=%s)", existing.id)
        return existing.user, existing, False

    # ---- 2) مستخدم قائم بنفس البريد (مُثبت الملكية من الطرفين) ----
    user = _find_linkable_by_email(profile)
    created = False

    # ---- 3) إنشاء مستخدم جديد بدور CUSTOMER ----
    if user is None:
        # البريد يُنسخ على الحساب فقط إن لم يكن لحساب آخر (الحقل فريد)
        email = profile.email or None
        if email and _find_by_email(email) is not None:
            email = None
        user = User.objects.create_user(
            phone=_placeholder_phone(provider, provider_user_id),
            email=email,
            email_verified=bool(email and profile.email_verified),
            full_name=profile.full_name or "",
            # قرار محسوم: التسجيل الذاتي عبر Social Login = CUSTOMER فقط
            role=ConfirmedRole.CUSTOMER,
        )
        created = True
        logger.info("Social login created new user (user_id=%s)", user.id)
    else:
        _assert_user_can_login(user)

    # الربط — القيد الفريد هو خط الدفاع الأخير ضد الازدواج المتزامن
    try:
        # savepoint: بدونه يُفسد IntegrityError المعاملة الخارجية على
        # PostgreSQL فيفشل الاستعلام التالي نفسه.
        with transaction.atomic():
            social = SocialAccount.objects.create(
                user=user,
                provider=provider,
                provider_user_id=provider_user_id,
                email=profile.email or None,
                raw_profile=profile.raw or {},
            )
    except IntegrityError:
        # (أ) سباق: أُنشئ نفس الربط في معاملة متزامنة → نستخدم الموجود
        social = (
            SocialAccount.objects.select_related("user")
            .filter(provider=provider, provider_user_id=provider_user_id)
            .first()
        )
        if social is None:
            # (ب) الحساب مرتبط مسبقًا بهوية أخرى لدى المزوّد نفسه
            raise ProviderAlreadyLinkedError(
                "This account is already linked to another identity at this provider."
            )
        return social.user, social, False

    logger.info("Social account linked (social_id=%s, created_user=%s)", social.id, created)
    return user, social, created


def _assert_user_can_login(user):
    """يمنع منح دخول لحساب معطّل أو موقوف."""
    if not user.is_active or user.status != UserStatus.ACTIVE:
        raise InactiveUserError("This account is not active.")


def _refresh_reported_profile(social, profile):
    """يحدّث البريد/الحمولة الخام كما أبلغ عنها المزوّد في آخر دخول."""
    fields = []
    if profile.email and social.email != profile.email:
        social.email = profile.email
        fields.append("email")
    if profile.raw and social.raw_profile != profile.raw:
        social.raw_profile = profile.raw
        fields.append("raw_profile")
    if fields:
        social.save(update_fields=fields)
