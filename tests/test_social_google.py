"""
Sign in with Google — GoogleSocialAuthAdapter.

التحقق من التوقيع نفسه تقوم به مكتبة google-auth؛ هنا نختبر ما نملكه:
الجمهور (client id) يُمرَّر، والرفض يتحول إلى 401، والملف يُبنى صحيحًا،
و Apple يُرفض برمز واضح لا بخطأ 500.
"""

import json

import pytest
from google.oauth2 import id_token

from apps.accounts.models import SocialAccount, User

CLIENT_ID = "461684012123-23e09b4q707qbjoknhc40jsvosojda1r.apps.googleusercontent.com"


@pytest.fixture
def google(settings):
    settings.SOCIAL_AUTH_ADAPTER = "adapters.social_auth.google.GoogleSocialAuthAdapter"
    settings.GOOGLE_OAUTH_CLIENT_IDS = [CLIENT_ID]
    return settings


def claims(**over):
    base = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "110169484474386276334",
        "email": "sam@gmail.com",
        "email_verified": True,
        "name": "Sam Smith",
    }
    base.update(over)
    return base


def login(client, token="google-id-token", provider="google"):
    return client.post(
        f"/api/auth/social/{provider}", data=json.dumps({"token": token}), content_type="application/json"
    )


@pytest.mark.django_db
def test_valid_google_token_signs_up_a_customer(client, google, monkeypatch):
    seen = {}

    def fake_verify(token, request, audience=None):
        seen["token"], seen["audience"] = token, audience
        return claims()

    monkeypatch.setattr(id_token, "verify_oauth2_token", fake_verify)
    r = login(client)
    assert r.status_code == 200, r.content
    assert r.json()["tokens"]["access"]
    assert seen == {"token": "google-id-token", "audience": [CLIENT_ID]}

    account = SocialAccount.objects.get(provider="GOOGLE", provider_user_id="110169484474386276334")
    assert account.user.full_name == "Sam Smith" and account.user.email == "sam@gmail.com"

    # الدخول الثاني بالهوية نفسها لا ينشئ حسابًا جديدًا
    assert login(client).status_code == 200
    assert User.objects.filter(social_accounts__provider="GOOGLE").count() == 1


@pytest.mark.django_db
def test_rejected_token_is_401(client, google, monkeypatch):
    def reject(token, request, audience=None):
        raise ValueError("Token has wrong audience")

    monkeypatch.setattr(id_token, "verify_oauth2_token", reject)
    r = login(client)
    assert r.status_code == 401 and r.json()["code"] == "social_auth_failed"
    assert not SocialAccount.objects.exists()


@pytest.mark.django_db
def test_foreign_issuer_is_rejected(client, google, monkeypatch):
    monkeypatch.setattr(id_token, "verify_oauth2_token", lambda *a, **k: claims(iss="https://evil.example"))
    assert login(client).status_code == 401


@pytest.mark.django_db
def test_email_verified_as_string_is_understood(client, google, monkeypatch):
    monkeypatch.setattr(id_token, "verify_oauth2_token", lambda *a, **k: claims(email_verified="true"))
    assert login(client).status_code == 200
    assert User.objects.get(email="sam@gmail.com").email_verified is True


@pytest.mark.django_db
def test_apple_is_refused_clearly_until_configured(client, google):
    r = login(client, provider="apple")
    assert r.status_code == 401 and r.json()["code"] == "provider_not_configured"


@pytest.mark.django_db
def test_missing_client_id_is_refused_not_500(client, google):
    google.GOOGLE_OAUTH_CLIENT_IDS = []
    r = login(client)
    assert r.status_code == 401 and r.json()["code"] == "provider_not_configured"
