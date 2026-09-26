"""
CORS للوحة التحكم — موقع منفصل يستدعي /api/admin/* من المتصفح.

🔒 قائمة سماح صريحة، والـAPI وحده، وبلا credentials (التوكن في Authorization).
"""

import pytest

ADMIN_ORIGIN = "https://admin.example.com"


def preflight(client, path, origin):
    return client.options(
        path,
        HTTP_ORIGIN=origin,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type",
    )


@pytest.fixture
def cors(settings):
    settings.CORS_ALLOWED_ORIGINS = [ADMIN_ORIGIN]
    settings.CORS_ALLOWED_ORIGIN_REGEXES = []
    return settings


def test_listed_origin_passes_preflight_with_authorization_header(client, cors):
    r = preflight(client, "/api/admin/auth/login", ADMIN_ORIGIN)
    assert r.status_code == 200
    assert r["Access-Control-Allow-Origin"] == ADMIN_ORIGIN
    assert "authorization" in r["Access-Control-Allow-Headers"].lower()
    # 🔒 بلا كوكيز
    assert "Access-Control-Allow-Credentials" not in r


def test_unlisted_origin_gets_no_cors_headers(client, cors):
    r = preflight(client, "/api/admin/auth/login", "https://evil.example.com")
    assert "Access-Control-Allow-Origin" not in r


def test_cors_applies_to_the_api_only(client, cors):
    r = preflight(client, "/admin/login/", ADMIN_ORIGIN)
    assert "Access-Control-Allow-Origin" not in r


@pytest.mark.django_db
def test_simple_get_carries_the_origin_header(client, cors):
    r = client.get("/api/health", HTTP_ORIGIN=ADMIN_ORIGIN)
    assert r.status_code == 200
    assert r["Access-Control-Allow-Origin"] == ADMIN_ORIGIN


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://localhost:5175", "http://127.0.0.1:5173"])
def test_localhost_switch_allows_any_dev_port(client, settings, origin):
    settings.CORS_ALLOWED_ORIGINS = []
    settings.CORS_ALLOWED_ORIGIN_REGEXES = [r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"]
    r = preflight(client, "/api/admin/auth/login", origin)
    assert r["Access-Control-Allow-Origin"] == origin


def test_localhost_regex_does_not_match_lookalike_hosts(client, settings):
    settings.CORS_ALLOWED_ORIGINS = []
    settings.CORS_ALLOWED_ORIGIN_REGEXES = [r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"]
    r = preflight(client, "/api/admin/auth/login", "http://localhost.evil.com")
    assert "Access-Control-Allow-Origin" not in r
