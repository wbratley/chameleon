import pytest
from aiohttp.test_utils import TestClient, TestServer

from chameleon_local.web import make_web_app

DOMAIN = "example.com"


@pytest.fixture
async def client(settings, alias_db):
    app = make_web_app(settings, alias_db)
    async with TestClient(TestServer(app)) as c:
        yield c


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
    resp = await client.post("/aliases", data={"service": "Netflix"})
    assert resp.status == 200
    html = await resp.text()
    assert "<tr" in html
    assert "netflix-" in html
    assert "@example.com" in html


async def test_create_alias_missing_service_returns_400(client):
    resp = await client.post("/aliases", data={"service": ""})
    assert resp.status == 400


async def test_burn_alias_returns_updated_row(client, alias_db):
    alias = await alias_db.create("Spammer", DOMAIN)
    resp = await client.post(f"/aliases/{alias.id}/burn")
    assert resp.status == 200
    html = await resp.text()
    assert "Burned" in html
    assert "burned" in html  # CSS class


async def test_burn_unknown_alias_returns_404(client):
    resp = await client.post("/aliases/9999/burn")
    assert resp.status == 404


async def test_burn_is_idempotent_via_http(client, alias_db):
    alias = await alias_db.create("Test", DOMAIN)
    await client.post(f"/aliases/{alias.id}/burn")
    resp = await client.post(f"/aliases/{alias.id}/burn")
    assert resp.status == 200
    assert "Burned" in await resp.text()
