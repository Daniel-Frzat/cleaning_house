"""
Social Login Tests — Identity Domain (Phase 1)

يغطي: أول دخول (إنشاء)، تكرار الدخول (بدون ازدواج)، الربط بحساب قائم
عبر البريد، وقيد التفرد على هوية المزوّد.
"""

import pytest
from django.db import IntegrityError, transaction

from adapters.social_auth import ProviderProfile, SocialAuthError, get_social_auth_adapter
from adapters.social_auth.base import BaseSocialAuthAdapter
from adapters.social_auth.fake import FakeSocialAuthAdapter
from apps.accounts.models import SocialAccount, SocialProvider, User, UserStatus
from apps.accounts.roles import ConfirmedRole
from apps.accounts.services import social as social_service


@pytest.fixture(autouse=True)
def _fake_adapter(settings):
    """يضبط الـadapter الوهمي ويُنظّف التوكنات بين الاختبارات."""
    settings.SOCIAL_AUTH_ADAPTER = "adapters.social_auth.fake.FakeSocialAuthAdapter"
    settings.SOCIAL_AUTH_ALLOW_FAKE = True
    FakeSocialAuthAdapter.reset()
    yield
    FakeSocialAuthAdapter.reset()


def make_profile(
    provider=SocialProvider.GOOGLE,
    provider_user_id="google-sub-001",
    email="user@example.com",
    full_name="Social User",
    raw=None,
    email_verified=True,
):
    return ProviderProfile(
        provider=provider,
        provider_user_id=provider_user_id,
        email=email,
        email_verified=email_verified,
        full_name=full_name,
        raw=raw if raw is not None else {"sub": provider_user_id, "email": email},
    )


# ============================================================
# 1) أول دخول → مستخدم جديد بدور CUSTOMER + ربط صحيح
# ============================================================
@pytest.mark.django_db
def test_first_time_social_login_creates_customer_and_links():
    profile = make_profile()
    FakeSocialAuthAdapter.register_token("tok-new", profile)

    user, social, created = social_service.login_with_provider(
        SocialProvider.GOOGLE, "tok-new"
    )

    assert created is True
    assert User.objects.count() == 1
    assert SocialAccount.objects.count() == 1

    # الدور الافتراضي CUSTOMER — لا CONTRACTOR ولا ADMIN عبر هذا المسار
    assert user.role == ConfirmedRole.CUSTOMER
    assert user.status == UserStatus.ACTIVE
    assert user.is_active is True
    assert user.is_staff is False
    assert user.is_superuser is False

    # الربط صحيح
    assert social.user_id == user.id
    assert social.provider == SocialProvider.GOOGLE
    assert social.provider_user_id == "google-sub-001"
    assert social.email == "user@example.com"
    assert social.raw_profile == {"sub": "google-sub-001", "email": "user@example.com"}
    assert social.linked_at is not None
    assert list(user.social_accounts.all()) == [social]


@pytest.mark.django_db
def test_social_login_never_self_registers_privileged_roles():
    """التسجيل الذاتي لا يمنح CONTRACTOR/ADMIN مهما كانت بيانات المزوّد."""
    profile = make_profile(
        provider_user_id="google-sub-admin",
        email="admin@example.com",
        raw={"role": "ADMIN", "is_staff": True},
    )
    FakeSocialAuthAdapter.register_token("tok-admin", profile)

    user, _, _ = social_service.login_with_provider(SocialProvider.GOOGLE, "tok-admin")

    assert user.role == ConfirmedRole.CUSTOMER
    assert user.is_staff is False
    assert user.is_superuser is False


@pytest.mark.django_db
def test_apple_provider_is_supported():
    profile = make_profile(provider=SocialProvider.APPLE, provider_user_id="apple-sub-1")
    FakeSocialAuthAdapter.register_token("tok-apple", profile)

    user, social, created = social_service.login_with_provider(
        SocialProvider.APPLE, "tok-apple"
    )
    assert created is True
    assert social.provider == SocialProvider.APPLE


@pytest.mark.django_db
def test_login_without_provider_email_still_works():
    """Apple Private Relay: قد لا يصل بريد إطلاقًا."""
    profile = make_profile(provider_user_id="google-no-email", email=None)
    FakeSocialAuthAdapter.register_token("tok-no-email", profile)

    user, social, created = social_service.login_with_provider(
        SocialProvider.GOOGLE, "tok-no-email"
    )
    assert created is True
    assert social.email is None
    assert user.email is None


# ============================================================
# 2) تكرار الدخول بنفس provider_user_id → نفس المستخدم، بلا ازدواج
# ============================================================
@pytest.mark.django_db
def test_repeat_social_login_returns_same_user_without_duplicates():
    profile = make_profile()
    FakeSocialAuthAdapter.register_token("tok-1", profile)

    user1, social1, created1 = social_service.login_with_provider(
        SocialProvider.GOOGLE, "tok-1"
    )
    user2, social2, created2 = social_service.login_with_provider(
        SocialProvider.GOOGLE, "tok-1"
    )

    assert created1 is True
    assert created2 is False
    assert user1.id == user2.id
    assert social1.id == social2.id

    # لا ازدواج إطلاقًا
    assert User.objects.count() == 1
    assert SocialAccount.objects.count() == 1


@pytest.mark.django_db
def test_repeat_login_with_changed_email_does_not_create_duplicate():
    """
    البريد لدى المزوّد قد يتغير — الهوية تبقى provider_user_id.
    يجب ألا يُنشئ ذلك مستخدمًا ثانيًا.
    """
    FakeSocialAuthAdapter.register_token("tok-a", make_profile(email="old@example.com"))
    user1, social1, _ = social_service.login_with_provider(SocialProvider.GOOGLE, "tok-a")

    FakeSocialAuthAdapter.register_token("tok-b", make_profile(email="new@example.com"))
    user2, social2, created = social_service.login_with_provider(
        SocialProvider.GOOGLE, "tok-b"
    )

    assert created is False
    assert user1.id == user2.id
    assert social1.id == social2.id
    assert User.objects.count() == 1

    # البريد المُبلَّغ عنه يُحدَّث على سجل الربط
    social2.refresh_from_db()
    assert social2.email == "new@example.com"


@pytest.mark.django_db
def test_same_provider_user_id_on_different_providers_are_distinct_identities():
    """نفس المعرّف النصي لدى Apple وGoogle هويتان مختلفتان."""
    FakeSocialAuthAdapter.register_token(
        "tok-g", make_profile(provider=SocialProvider.GOOGLE, provider_user_id="shared-1", email="g@example.com")
    )
    FakeSocialAuthAdapter.register_token(
        "tok-a", make_profile(provider=SocialProvider.APPLE, provider_user_id="shared-1", email="a@example.com")
    )

    user_g, _, _ = social_service.login_with_provider(SocialProvider.GOOGLE, "tok-g")
    user_a, _, _ = social_service.login_with_provider(SocialProvider.APPLE, "tok-a")

    assert user_g.id != user_a.id
    assert SocialAccount.objects.count() == 2


# ============================================================
# 3) البريد يطابق مستخدمًا مسجّلًا بالهاتف → ربط بلا إنشاء
# ============================================================
@pytest.mark.django_db
def test_matching_email_links_to_existing_phone_registered_user():
    existing = User.objects.create_user(
        phone="+96550007777",
        email="existing@example.com",
        email_verified=True,
        full_name="Phone User",
    )

    profile = make_profile(provider_user_id="google-sub-777", email="existing@example.com")
    FakeSocialAuthAdapter.register_token("tok-link", profile)

    user, social, created = social_service.login_with_provider(
        SocialProvider.GOOGLE, "tok-link"
    )

    # لم يُنشأ مستخدم جديد
    assert created is False
    assert user.id == existing.id
    assert User.objects.count() == 1

    # الحساب الاجتماعي رُبط بالمستخدم القائم، ورقم الهاتف الأصلي محفوظ
    assert social.user_id == existing.id
    assert user.phone == "+96550007777"
    assert SocialAccount.objects.count() == 1


@pytest.mark.django_db
def test_email_match_is_case_insensitive():
    existing = User.objects.create_user(
        phone="+96550008888", email="Mixed@Example.com", email_verified=True
    )
    FakeSocialAuthAdapter.register_token(
        "tok-case", make_profile(provider_user_id="g-case", email="mixed@example.com")
    )

    user, _, created = social_service.login_with_provider(SocialProvider.GOOGLE, "tok-case")

    assert created is False
    assert user.id == existing.id
    assert User.objects.count() == 1


@pytest.mark.django_db
def test_inactive_user_cannot_login_via_social():
    User.objects.create_user(
        phone="+96550009999",
        email="suspended@example.com",
        email_verified=True,
        status=UserStatus.SUSPENDED,
    )
    FakeSocialAuthAdapter.register_token(
        "tok-susp", make_profile(provider_user_id="g-susp", email="suspended@example.com")
    )

    with pytest.raises(social_service.InactiveUserError):
        social_service.login_with_provider(SocialProvider.GOOGLE, "tok-susp")

    assert SocialAccount.objects.count() == 0


# ------------------------------------------------------------
# 3b) لا ربط عبر بريد غير مُثبت الملكية (account pre-hijacking)
# ------------------------------------------------------------
@pytest.mark.django_db
def test_unverified_local_email_is_never_linked():
    """
    مهاجم يضع بريد الضحية في حسابه عبر PATCH /auth/me (غير مُثبت). دخول
    الضحية بـGoogle لا يجوز أن يصل إلى حساب المهاجم.
    """
    attacker = User.objects.create_user(phone="+96550006666", email="victim@example.com")
    assert attacker.email_verified is False
    FakeSocialAuthAdapter.register_token(
        "tok-victim", make_profile(provider_user_id="g-victim", email="victim@example.com")
    )

    user, social, created = social_service.login_with_provider(SocialProvider.GOOGLE, "tok-victim")

    assert created is True
    assert user.id != attacker.id
    assert social.user_id == user.id
    # البريد مأخوذ بحساب آخر → الحساب الجديد بلا بريد، والبريد يبقى على الربط
    assert user.email is None
    assert social.email == "victim@example.com"


@pytest.mark.django_db
def test_provider_unverified_email_is_never_linked():
    existing = User.objects.create_user(
        phone="+96550005555", email="owner@example.com", email_verified=True
    )
    FakeSocialAuthAdapter.register_token(
        "tok-unv",
        make_profile(provider_user_id="g-unv", email="owner@example.com", email_verified=False),
    )

    user, _, created = social_service.login_with_provider(SocialProvider.GOOGLE, "tok-unv")

    assert created is True
    assert user.id != existing.id


@pytest.mark.django_db
def test_new_social_user_with_verified_email_is_marked_verified():
    FakeSocialAuthAdapter.register_token(
        "tok-new-v", make_profile(provider_user_id="g-new-v", email="fresh@example.com")
    )
    user, _, created = social_service.login_with_provider(SocialProvider.GOOGLE, "tok-new-v")
    assert created is True
    assert user.email == "fresh@example.com"
    assert user.email_verified is True


@pytest.mark.django_db
def test_editing_email_resets_verification():
    from apps.accounts.services import identity

    user = User.objects.create_user(
        phone="+96550004444", email="mine@example.com", email_verified=True
    )
    identity.update_own_profile(user, email="other@example.com")
    user.refresh_from_db()
    assert user.email_verified is False


@pytest.mark.django_db
def test_user_already_linked_to_another_identity_gets_clean_error():
    """المستخدم المطابق بالبريد مرتبط مسبقًا بـsub آخر لدى Google → خطأ domain لا 500."""
    existing = User.objects.create_user(
        phone="+96550003333", email="linked@example.com", email_verified=True
    )
    SocialAccount.objects.create(
        user=existing, provider=SocialProvider.GOOGLE, provider_user_id="g-original"
    )
    FakeSocialAuthAdapter.register_token(
        "tok-second", make_profile(provider_user_id="g-second", email="linked@example.com")
    )

    with pytest.raises(social_service.ProviderAlreadyLinkedError):
        social_service.login_with_provider(SocialProvider.GOOGLE, "tok-second")


def test_placeholder_phone_does_not_collide_on_shared_prefix():
    """sub الخاص بـGoogle 21 رقمًا — الاقتطاع القديم كان يُصادم البادئات المشتركة."""
    a = social_service._placeholder_phone("GOOGLE", "123456789012345678901")
    b = social_service._placeholder_phone("GOOGLE", "123456789012345678902")
    assert a != b
    assert len(a) <= 32 and len(b) <= 32


# ============================================================
# 4) قيود التفرد — لا يتشارك مستخدمان سجل ربط واحد
# ============================================================
@pytest.mark.django_db
def test_unique_constraint_on_provider_identity():
    """(provider, provider_user_id) فريد — لا يمكن ربطه بمستخدمين مختلفين."""
    user_a = User.objects.create_user(phone="+96551110001", email="a@example.com")
    user_b = User.objects.create_user(phone="+96551110002", email="b@example.com")

    SocialAccount.objects.create(
        user=user_a, provider=SocialProvider.GOOGLE, provider_user_id="dup-sub"
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            SocialAccount.objects.create(
                user=user_b, provider=SocialProvider.GOOGLE, provider_user_id="dup-sub"
            )

    assert SocialAccount.objects.filter(provider_user_id="dup-sub").count() == 1
    assert SocialAccount.objects.get(provider_user_id="dup-sub").user_id == user_a.id


@pytest.mark.django_db
def test_unique_constraint_one_account_per_provider_per_user():
    """المستخدم لا يملك أكثر من حساب واحد لكل مزوّد."""
    user = User.objects.create_user(phone="+96551110003", email="c@example.com")
    SocialAccount.objects.create(
        user=user, provider=SocialProvider.GOOGLE, provider_user_id="sub-1"
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            SocialAccount.objects.create(
                user=user, provider=SocialProvider.GOOGLE, provider_user_id="sub-2"
            )


@pytest.mark.django_db
def test_user_can_link_both_apple_and_google():
    user = User.objects.create_user(phone="+96551110004", email="d@example.com")
    SocialAccount.objects.create(
        user=user, provider=SocialProvider.GOOGLE, provider_user_id="g-1"
    )
    SocialAccount.objects.create(
        user=user, provider=SocialProvider.APPLE, provider_user_id="a-1"
    )
    assert user.social_accounts.count() == 2


@pytest.mark.django_db
def test_deleting_user_removes_social_accounts():
    user = User.objects.create_user(phone="+96551110005", email="e@example.com")
    SocialAccount.objects.create(
        user=user, provider=SocialProvider.GOOGLE, provider_user_id="g-del"
    )
    user.delete()
    assert SocialAccount.objects.count() == 0


# ============================================================
# الـAdapter نفسه
# ============================================================
@pytest.mark.django_db
def test_adapter_is_resolved_from_settings_and_honours_contract():
    adapter = get_social_auth_adapter()
    assert isinstance(adapter, BaseSocialAuthAdapter)
    assert isinstance(adapter, FakeSocialAuthAdapter)


@pytest.mark.django_db
def test_invalid_token_is_rejected():
    with pytest.raises(SocialAuthError):
        social_service.login_with_provider(SocialProvider.GOOGLE, "unknown-token")
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_token_from_wrong_provider_is_rejected():
    FakeSocialAuthAdapter.register_token(
        "tok-g", make_profile(provider=SocialProvider.GOOGLE)
    )
    with pytest.raises(SocialAuthError):
        social_service.login_with_provider(SocialProvider.APPLE, "tok-g")


@pytest.mark.django_db
def test_unsupported_provider_is_rejected():
    with pytest.raises(social_service.UnsupportedProviderError):
        social_service.login_with_provider("FACEBOOK", "whatever")


@pytest.mark.django_db
def test_no_real_provider_credentials_referenced():
    """لا اعتمادات Apple/Google في كود الـDomain أو الـadapter المجرد."""
    import inspect

    import adapters.social_auth.base as base_mod
    import apps.accounts.services.social as svc_mod

    for mod in (base_mod, svc_mod):
        src = inspect.getsource(mod).lower()
        for needle in ("client_secret", "private_key", "api_key", "team_id", "client_id"):
            assert needle not in src, f"{needle} leaked into {mod.__name__}"


@pytest.mark.django_db
def test_str_representation():
    user = User.objects.create_user(phone="+96551110006", email="f@example.com")
    social = SocialAccount.objects.create(
        user=user, provider=SocialProvider.APPLE, provider_user_id="a-str"
    )
    assert str(social) == f"Apple account for {user.phone}"
