import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer
from nacl.public import PrivateKey, SealedBox

from chameleon_local.aliases import AliasDB
from chameleon_local.client import run_client
from chameleon_local.config import LocalSettings

SAMPLE = b"From: sender@example.com\r\nTo: user@example.com\r\nSubject: Hi\r\n\r\nBody"


def _encrypt(raw: bytes, private_key: PrivateKey) -> str:
    return base64.b64encode(SealedBox(private_key.public_key).encrypt(raw)).decode()


def make_settings(port: int, tmp_path: Path, key_file: Path) -> LocalSettings:
    return LocalSettings(
        RELAY_WS_URL=f"ws://127.0.0.1:{port}/ws",
        RELAY_TOKEN="test-token",
        MY_DOMAIN="example.com",
        MAILDIR_PATH=str(tmp_path),
        ALIAS_DB_PATH=str(tmp_path / "aliases.db"),
        PRIVATE_KEY_PATH=str(key_file),
    )


async def _make_alias_db(tmp_path: Path) -> AliasDB:
    db = AliasDB(str(tmp_path / "aliases.db"))
    await db.setup()
    return db


async def test_client_delivers_message_and_acks(tmp_path, test_private_key, private_key_file):
    ack_received = asyncio.Event()
    acks: list[dict] = []

    async def ws_server(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({
            "type": "deliver",
            "id": 1,
            "message": _encrypt(SAMPLE, test_private_key),
        })
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                acks.append(json.loads(msg.data))
                ack_received.set()
        return ws

    db = await _make_alias_db(tmp_path)
    app = web.Application()
    app.router.add_get("/ws", ws_server)
    async with TestClient(TestServer(app)) as client:
        settings = make_settings(client.server.port, tmp_path, private_key_file)
        task = asyncio.create_task(run_client(settings, db))
        try:
            await asyncio.wait_for(ack_received.wait(), timeout=5.0)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    await db.close()

    assert acks == [{"type": "ack", "id": 1}]
    assert len(list((tmp_path / "new").iterdir())) == 1


async def test_client_no_ack_on_delivery_failure(tmp_path, test_private_key, private_key_file):
    deliver_called = asyncio.Event()
    acks: list[dict] = []

    async def ws_server(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({
            "type": "deliver",
            "id": 2,
            "message": _encrypt(SAMPLE, test_private_key),
        })
        await asyncio.wait_for(deliver_called.wait(), timeout=3.0)
        await asyncio.sleep(0.2)
        await ws.close()
        return ws

    async def failing_deliver(path, raw):
        deliver_called.set()
        raise OSError("disk full")

    db = await _make_alias_db(tmp_path)
    app = web.Application()
    app.router.add_get("/ws", ws_server)
    with patch("chameleon_local.client.deliver", side_effect=failing_deliver):
        async with TestClient(TestServer(app)) as client:
            settings = make_settings(client.server.port, tmp_path, private_key_file)
            task = asyncio.create_task(run_client(settings, db))
            try:
                await asyncio.wait_for(deliver_called.wait(), timeout=5.0)
                await asyncio.sleep(0.3)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
    await db.close()

    new_dir = tmp_path / "new"
    assert not new_dir.exists() or len(list(new_dir.iterdir())) == 0


async def test_client_reconnects_after_server_close(tmp_path, test_private_key, private_key_file):
    connect_count = 0
    message_delivered = asyncio.Event()

    async def ws_server(request):
        nonlocal connect_count
        connect_count += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if connect_count == 1:
            await ws.close()
        else:
            await ws.send_json({
                "type": "deliver",
                "id": connect_count,
                "message": _encrypt(SAMPLE, test_private_key),
            })
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    message_delivered.set()
                    break
        return ws

    db = await _make_alias_db(tmp_path)
    app = web.Application()
    app.router.add_get("/ws", ws_server)
    async with TestClient(TestServer(app)) as client:
        settings = make_settings(client.server.port, tmp_path, private_key_file)
        task = asyncio.create_task(run_client(settings, db))
        try:
            await asyncio.wait_for(message_delivered.wait(), timeout=5.0)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    await db.close()

    assert connect_count >= 2
    assert len(list((tmp_path / "new").iterdir())) >= 1


async def test_client_drops_burned_alias_and_acks(tmp_path, test_private_key, private_key_file):
    """Burned alias: acked (removed from relay queue) but not written to Maildir."""
    ack_received = asyncio.Event()
    acks: list[dict] = []

    async def ws_server(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({
            "type": "deliver",
            "id": 1,
            "message": _encrypt(SAMPLE, test_private_key),
        })
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                acks.append(json.loads(msg.data))
                ack_received.set()
        return ws

    db = await _make_alias_db(tmp_path)
    await db.record_delivery("user@example.com")
    alias = (await db.all())[0]
    await db.burn(alias.id)

    app = web.Application()
    app.router.add_get("/ws", ws_server)
    async with TestClient(TestServer(app)) as client:
        settings = make_settings(client.server.port, tmp_path, private_key_file)
        task = asyncio.create_task(run_client(settings, db))
        try:
            await asyncio.wait_for(ack_received.wait(), timeout=5.0)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    await db.close()

    assert acks == [{"type": "ack", "id": 1}]
    new_dir = tmp_path / "new"
    assert not new_dir.exists() or len(list(new_dir.iterdir())) == 0


async def test_client_acks_and_discards_on_decrypt_failure(
    tmp_path, test_private_key, private_key_file
):
    """Corrupted ciphertext is acked immediately and not written to Maildir."""
    ack_received = asyncio.Event()
    acks: list[dict] = []

    async def ws_server(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({
            "type": "deliver",
            "id": 99,
            "message": base64.b64encode(b"this is not valid ciphertext").decode(),
        })
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                acks.append(json.loads(msg.data))
                ack_received.set()
        return ws

    db = await _make_alias_db(tmp_path)
    app = web.Application()
    app.router.add_get("/ws", ws_server)
    async with TestClient(TestServer(app)) as client:
        settings = make_settings(client.server.port, tmp_path, private_key_file)
        task = asyncio.create_task(run_client(settings, db))
        try:
            await asyncio.wait_for(ack_received.wait(), timeout=5.0)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    await db.close()

    assert acks == [{"type": "ack", "id": 99}]
    new_dir = tmp_path / "new"
    assert not new_dir.exists() or len(list(new_dir.iterdir())) == 0


def _with_rcpt(rcpt: str, body: bytes) -> bytes:
    """Prepend the transport header the relay adds inside the encrypted payload."""
    return f"X-Chameleon-Rcpt: {rcpt}\r\n".encode() + body


async def _deliver_one(tmp_path, private_key_file, db, payload_b64, msg_id=1):
    """Run the client against a server that delivers one message; return acks."""
    ack_received = asyncio.Event()
    acks: list[dict] = []

    async def ws_server(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "deliver", "id": msg_id, "message": payload_b64})
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                acks.append(json.loads(msg.data))
                ack_received.set()
        return ws

    app = web.Application()
    app.router.add_get("/ws", ws_server)
    async with TestClient(TestServer(app)) as client:
        settings = make_settings(client.server.port, tmp_path, private_key_file)
        task = asyncio.create_task(run_client(settings, db))
        try:
            await asyncio.wait_for(ack_received.wait(), timeout=5.0)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    return acks


async def test_burn_enforced_via_envelope_recipient_when_to_header_differs(
    tmp_path, test_private_key, private_key_file
):
    """The core fix: a burned alias is dropped based on X-Chameleon-Rcpt even when
    the visible To header names a different address (BCC / list mail)."""
    db = await _make_alias_db(tmp_path)
    await db.record_delivery("secret-alias@example.com")
    await db.burn((await db.all())[0].id)

    # Visible To is a list address, not the alias — the old To-header logic missed this.
    body = (
        b"From: news@service.com\r\nTo: undisclosed-recipients:;\r\n"
        b"Subject: Deal!\r\n\r\nBuy now"
    )
    payload = _encrypt(_with_rcpt("secret-alias@example.com", body), test_private_key)
    acks = await _deliver_one(tmp_path, private_key_file, db, payload)
    await db.close()

    assert acks == [{"type": "ack", "id": 1}]  # acked (removed from queue)
    new_dir = tmp_path / "new"
    assert not new_dir.exists() or len(list(new_dir.iterdir())) == 0  # but not delivered


async def test_rcpt_header_stripped_before_delivery(
    tmp_path, test_private_key, private_key_file
):
    """The X-Chameleon-Rcpt transport header must not reach the user's Maildir."""
    db = await _make_alias_db(tmp_path)
    payload = _encrypt(_with_rcpt("user@example.com", SAMPLE), test_private_key)
    acks = await _deliver_one(tmp_path, private_key_file, db, payload)
    await db.close()

    assert acks == [{"type": "ack", "id": 1}]
    files = list((tmp_path / "new").iterdir())
    assert len(files) == 1
    content = files[0].read_bytes()
    assert b"X-Chameleon-Rcpt" not in content
    assert b"Subject: Hi" in content  # the real message survived intact


async def test_foreign_to_address_not_auto_registered_in_fallback(
    tmp_path, test_private_key, private_key_file
):
    """Fallback path (no rcpt header): a foreign To address must not become an alias."""
    db = await _make_alias_db(tmp_path)
    body = b"From: a@b.com\r\nTo: news@foreign.com\r\nSubject: Hi\r\n\r\nBody"
    payload = _encrypt(body, test_private_key)  # no X-Chameleon-Rcpt
    acks = await _deliver_one(tmp_path, private_key_file, db, payload)
    aliases = await db.all()
    await db.close()

    assert acks == [{"type": "ack", "id": 1}]
    assert len(list((tmp_path / "new").iterdir())) == 1  # still delivered
    assert aliases == []  # but no junk alias created for the foreign address
