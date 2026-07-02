import asyncio
import base64
import json

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from chameleon_relay.api import Broadcaster, make_app
from chameleon_relay.config import RelaySettings
from chameleon_relay.queue import MessageQueue

SAMPLE = b"From: sender@example.com\r\nTo: user@example.com\r\nSubject: Hi\r\n\r\nBody"


async def test_broadcast_tolerates_client_added_mid_iteration():
    """A client connecting during a broadcast must not blow up the iteration."""
    broadcaster = Broadcaster()

    class _FakeWS:
        def __init__(self, on_send=None):
            self.on_send = on_send

        async def send_str(self, frame):
            if self.on_send is not None:
                self.on_send()

    # The first client, when sent to, simulates a new connection arriving.
    broadcaster.add(_FakeWS(on_send=lambda: broadcaster.add(_FakeWS())))
    broadcaster.add(_FakeWS())

    # Must not raise "Set changed size during iteration".
    await broadcaster.broadcast(1, b"payload")


@pytest.fixture
def settings(test_private_key):
    return RelaySettings(
        MY_DOMAIN="example.com",
        RELAY_HOSTNAME="relay.example.com",
        API_TOKEN="test-token",
        QUEUE_DB_PATH=":memory:",
        PUBLIC_KEY=base64.b64encode(bytes(test_private_key.public_key)).decode(),
    )


@pytest.fixture
async def queue():
    q = MessageQueue(":memory:")
    await q.setup()
    yield q
    await q.close()


@pytest.fixture
async def client(settings, queue):
    app, _ = make_app(settings, queue)
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"


async def test_ws_no_auth_rejected(client):
    with pytest.raises(aiohttp.WSServerHandshakeError):
        await client.ws_connect("/ws")


async def test_ws_wrong_token_rejected(client):
    with pytest.raises(aiohttp.WSServerHandshakeError):
        await client.ws_connect("/ws", headers={"Authorization": "Bearer wrong"})


async def test_ws_correct_token_connects(client):
    ws = await client.ws_connect("/ws", headers={"Authorization": "Bearer test-token"})
    await ws.close()


async def test_ws_flushes_pending_on_connect(client, queue):
    msg_id = await queue.enqueue(SAMPLE)
    ws = await client.ws_connect("/ws", headers={"Authorization": "Bearer test-token"})
    msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
    data = json.loads(msg.data)
    assert data["type"] == "deliver"
    assert data["id"] == msg_id
    assert base64.b64decode(data["message"]) == SAMPLE
    await ws.close()


async def test_ws_ack_marks_delivered(client, queue):
    msg_id = await queue.enqueue(SAMPLE)
    ws = await client.ws_connect("/ws", headers={"Authorization": "Bearer test-token"})
    await asyncio.wait_for(ws.receive(), timeout=5.0)
    await ws.send_json({"type": "ack", "id": msg_id})
    await asyncio.sleep(0.1)
    assert await queue.pending() == []
    await ws.close()


async def test_ws_malformed_ack_is_ignored(client, queue):
    msg_id = await queue.enqueue(SAMPLE)
    ws = await client.ws_connect("/ws", headers={"Authorization": "Bearer test-token"})
    await asyncio.wait_for(ws.receive(), timeout=5.0)
    # Send a malformed frame — server should not crash
    await ws.send_str("not json at all")
    await asyncio.sleep(0.1)
    # Message is still pending (malformed frame was ignored)
    assert len(await queue.pending()) == 1
    await ws.close()
