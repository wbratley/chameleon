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
from chameleon_local.client import _parse_rcpt_frame, run_client
from chameleon_local.config import LocalSettings

SAMPLE = b"From: sender@example.com\r\nTo: user@example.com\r\nSubject: Hi\r\n\r\nBody"
RECEIVED = (
    b"Received: from sender ([203.0.113.9])\r\n"
    b"\tby relay.example.com (chameleon-relay) with ESMTP;\r\n"
    b"\tFri,  1 Jan 2026 00:00:00 +0000\r\n"
)


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
            "message": _encrypt(_frame("user@example.com", SAMPLE), test_private_key),
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
            "message": _encrypt(_frame("user@example.com", SAMPLE), test_private_key),
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
                "message": _encrypt(_frame("user@example.com", SAMPLE), test_private_key),
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
            "message": _encrypt(_frame("user@example.com", SAMPLE), test_private_key),
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


def _frame(rcpt: str, body: bytes) -> bytes:
    """Prepend the length-prefixed recipient frame the relay writes (issue #6)."""
    block = rcpt.encode()
    return b"CHAMELEON-RCPT/1 " + str(len(block)).encode() + b"\r\n" + block + RECEIVED + body


def _with_rcpt(rcpt: str, body: bytes) -> bytes:
    """Prepend the legacy transport header (relays before the frame format)."""
    return f"X-Chameleon-Rcpt: {rcpt}\r\n".encode() + RECEIVED + body


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
    """The core fix: a burned alias is dropped based on the envelope recipients
    even when the visible To header names a different address (BCC / list mail)."""
    db = await _make_alias_db(tmp_path)
    await db.record_delivery("secret-alias@example.com")
    await db.burn((await db.all())[0].id)

    # Visible To is a list address, not the alias — the old To-header logic missed this.
    body = (
        b"From: news@service.com\r\nTo: undisclosed-recipients:;\r\n"
        b"Subject: Deal!\r\n\r\nBuy now"
    )
    payload = _encrypt(_frame("secret-alias@example.com", body), test_private_key)
    acks = await _deliver_one(tmp_path, private_key_file, db, payload)
    await db.close()

    assert acks == [{"type": "ack", "id": 1}]  # acked (removed from queue)
    new_dir = tmp_path / "new"
    assert not new_dir.exists() or len(list(new_dir.iterdir())) == 0  # but not delivered


async def test_rcpt_frame_stripped_before_delivery(
    tmp_path, test_private_key, private_key_file
):
    """Neither the frame nor its recipients block may reach the user's Maildir."""
    db = await _make_alias_db(tmp_path)
    payload = _encrypt(_frame("user@example.com", SAMPLE), test_private_key)
    acks = await _deliver_one(tmp_path, private_key_file, db, payload)
    await db.close()

    assert acks == [{"type": "ack", "id": 1}]
    files = list((tmp_path / "new").iterdir())
    assert len(files) == 1
    content = files[0].read_bytes()
    assert b"CHAMELEON-RCPT/1" not in content
    assert content.startswith(b"Received:")  # relay's header starts the message
    assert b"Subject: Hi" in content  # the real message survived intact


async def test_legacy_rcpt_header_still_delivers(
    tmp_path, test_private_key, private_key_file
):
    """Staggered upgrades: messages queued by a pre-frame relay (legacy
    X-Chameleon-Rcpt header) still deliver, stripped and burn-checked."""
    db = await _make_alias_db(tmp_path)
    payload = _encrypt(_with_rcpt("user@example.com", SAMPLE), test_private_key)
    acks = await _deliver_one(tmp_path, private_key_file, db, payload)
    await db.close()

    assert acks == [{"type": "ack", "id": 1}]
    files = list((tmp_path / "new").iterdir())
    assert len(files) == 1
    content = files[0].read_bytes()
    assert b"X-Chameleon-Rcpt" not in content
    assert b"Subject: Hi" in content


async def test_unframed_payload_is_discarded_and_acked(
    tmp_path, test_private_key, private_key_file
):
    """No frame and no legacy header: the To-header guess is gone (issue #6) —
    a payload we can't attribute can't be burn-checked, so it is discarded
    (acked, so it isn't redelivered forever) and nothing is auto-registered."""
    db = await _make_alias_db(tmp_path)
    payload = _encrypt(SAMPLE, test_private_key)  # no frame, no legacy header
    acks = await _deliver_one(tmp_path, private_key_file, db, payload)
    aliases = await db.all()
    await db.close()

    assert acks == [{"type": "ack", "id": 1}]
    new_dir = tmp_path / "new"
    assert not new_dir.exists() or len(list(new_dir.iterdir())) == 0
    assert aliases == []


async def test_forged_frame_without_received_is_discarded(
    tmp_path, test_private_key, private_key_file
):
    """The frame's sanity check: whatever follows the recipient block must be
    the relay's own Received: header. A frame whose block is followed by
    attacker-style content is not trusted and not delivered (issue #6)."""
    db = await _make_alias_db(tmp_path)
    block = b"unburned-alias@example.com"
    raw = (
        b"CHAMELEON-RCPT/1 " + str(len(block)).encode() + b"\r\n" + block
        + b"Subject: forged\r\n\r\nno Received header here"
    )
    payload = _encrypt(raw, test_private_key)
    acks = await _deliver_one(tmp_path, private_key_file, db, payload)
    aliases = await db.all()
    await db.close()

    assert acks == [{"type": "ack", "id": 1}]
    new_dir = tmp_path / "new"
    assert not new_dir.exists() or len(list(new_dir.iterdir())) == 0
    assert aliases == []  # no delivery recorded for the forged recipient


# --- frame parser unit tests (issue #6) ------------------------------------


def test_parse_frame_roundtrip():
    block = b"first@example.com, second@example.com"
    raw = (
        b"CHAMELEON-RCPT/1 " + str(len(block)).encode() + b"\r\n" + block + RECEIVED + b"body"
    )
    recipients, message = _parse_rcpt_frame(raw)
    assert recipients == ["first@example.com", "second@example.com"]
    assert message == RECEIVED + b"body"


def test_parse_frame_rejects_non_numeric_length():
    raw = b"CHAMELEON-RCPT/1 9x9\r\nabc" + RECEIVED
    assert _parse_rcpt_frame(raw)[0] is None


def test_parse_frame_rejects_truncated_block():
    raw = b"CHAMELEON-RCPT/1 500\r\nshort" + RECEIVED
    assert _parse_rcpt_frame(raw)[0] is None


def test_parse_frame_rejects_missing_received():
    raw = b"CHAMELEON-RCPT/1 5\r\nuser@example.com" + b"X-Not-Received: 1\r\nbody"
    assert _parse_rcpt_frame(raw)[0] is None


def test_parse_frame_rejects_empty_block():
    raw = b"CHAMELEON-RCPT/1 0\r\n" + RECEIVED
    assert _parse_rcpt_frame(raw)[0] is None
