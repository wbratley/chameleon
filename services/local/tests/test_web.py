import base64

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chameleon_local.web import make_web_app

DOMAIN = "example.com"


@pytest.fixture
async def client(settings, alias_db):
    app = make_web_app(settings, alias_db)
    async with TestClient(TestServer(app)) as c:
        yield c


@pytest.fixture
async def auth_client(settings, alias_db):
    """Client against an app configured with a UI password (LAN mode)."""
    settings = settings.model_copy(update={"WEB_PASSWORD": "sekret"})
    app = make_web_app(settings, alias_db)
    async with TestClient(TestServer(app)) as c:
        yield c


def auth_headers(password: str, user: str = "me") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def same_origin(client: TestClient) -> dict[str, str]:
    """Headers a real browser would send: Origin matching the request Host."""
    return {"Origin": f"http://{client.server.host}:{client.server.port}"}


async def test_index_returns_200(client):
    resp = await client.get("/")
    assert resp.status == 200
    text = await resp.text()
    assert "Chameleon" in text


async def test_index_shows_existing_aliases(client, alias_db):
    await alias_db.create("Netflix", DOMAIN)
    resp = await client.get("/")
    assert resp.status == 200
    assert "Netflix" in await resp.text()


async def test_create_alias_returns_tr(client):
    resp = await client.post(
        "/aliases", data={"service": "Netflix"}, headers=same_origin(client)
    )
    assert resp.status == 200
    html = await resp.text()
    assert "<tr" in html
    assert "netflix-" in html
    assert "@example.com" in html


async def test_create_alias_missing_service_returns_400(client):
    resp = await client.post(
        "/aliases", data={"service": ""}, headers=same_origin(client)
    )
    assert resp.status == 400


async def test_burn_alias_returns_updated_row(client, alias_db):
    alias = await alias_db.create("Spammer", DOMAIN)
    resp = await client.post(
        f"/aliases/{alias.id}/burn", headers=same_origin(client)
    )
    assert resp.status == 200
    html = await resp.text()
    assert "Burned" in html
    assert "burned" in html  # CSS class


async def test_burn_unknown_alias_returns_404(client):
    resp = await client.post("/aliases/9999/burn", headers=same_origin(client))
    assert resp.status == 404


async def test_burn_is_idempotent_via_http(client, alias_db):
    alias = await alias_db.create("Test", DOMAIN)
    await client.post(f"/aliases/{alias.id}/burn", headers=same_origin(client))
    resp = await client.post(f"/aliases/{alias.id}/burn", headers=same_origin(client))
    assert resp.status == 200
    assert "Burned" in await resp.text()


# --- CSRF protection (issue #5) -------------------------------------------


async def test_post_without_origin_is_rejected(client):
    """Non-browser clients don't send Origin; the htmx-only UI rejects them."""
    resp = await client.post("/aliases", data={"service": "Netflix"})
    assert resp.status == 403


async def test_cross_origin_post_is_rejected(client):
    resp = await client.post(
        "/aliases",
        data={"service": "Netflix"},
        headers={"Origin": "http://evil.example"},
    )
    assert resp.status == 403


async def test_cross_origin_burn_is_rejected(client, alias_db):
    """A malicious page must not be able to burn aliases via a simple POST."""
    alias = await alias_db.create("Spammer", DOMAIN)
    resp = await client.post(
        f"/aliases/{alias.id}/burn",
        headers={"Origin": "http://evil.example:1234"},
    )
    assert resp.status == 403
    aliases = await alias_db.all()
    assert aliases[0].burned is False


async def test_null_origin_is_rejected(client):
    resp = await client.post(
        "/aliases", data={"service": "Netflix"}, headers={"Origin": "null"}
    )
    assert resp.status == 403


async def test_get_is_not_blocked_without_origin(client):
    """Read-only requests need no Origin — plain browsing keeps working."""
    resp = await client.get("/")
    assert resp.status == 200


# --- Basic auth (LAN mode) -------------------------------------------------


async def test_password_enabled_requires_credentials(auth_client):
    resp = await auth_client.get("/")
    assert resp.status == 401
    assert resp.headers["WWW-Authenticate"].startswith('Basic realm=')


async def test_password_enabled_rejects_wrong_password(auth_client):
    resp = await auth_client.get("/", headers=auth_headers("wrong"))
    assert resp.status == 401


async def test_password_enabled_accepts_any_username(auth_client):
    resp = await auth_client.get("/", headers=auth_headers("sekret", user="anyone"))
    assert resp.status == 200


async def test_credentialed_post_without_origin_is_allowed(auth_client):
    """Non-browser clients (companion app, curl) send Authorization but no
    Origin; valid credentials exempt them from the browser-only Origin rule."""
    resp = await auth_client.post(
        "/aliases", data={"service": "Netflix"}, headers=auth_headers("sekret")
    )
    assert resp.status == 200
    assert "netflix-" in await resp.text()


async def test_credentialed_cross_origin_post_is_rejected(auth_client):
    """A cross-site form post carries Origin even when the browser reuses
    cached Basic credentials — credentials must not bypass the Origin check."""
    resp = await auth_client.post(
        "/aliases",
        data={"service": "Netflix"},
        headers={**auth_headers("sekret"), "Origin": "http://evil.example"},
    )
    assert resp.status == 403
