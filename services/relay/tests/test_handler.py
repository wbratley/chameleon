from concurrent.futures import Future as ConcurrentFuture
from unittest.mock import patch

import pytest
from nacl.public import SealedBox

from chameleon_relay.handler import RelayHandler


async def test_rcpt_accepts_configured_domain(handler, envelope, session):
    result = await handler.handle_RCPT(None, session, envelope, "user@example.com", [])
    assert result == "250 OK"
    assert "user@example.com" in envelope.rcpt_tos


async def test_rcpt_accepts_any_address_at_domain(handler, envelope, session):
    result = await handler.handle_RCPT(None, session, envelope, "x-7k4j@example.com", [])
    assert result == "250 OK"


async def test_rcpt_rejects_foreign_domain(handler, envelope, session):
    result = await handler.handle_RCPT(None, session, envelope, "user@evil.com", [])
    assert result.startswith("550")


async def test_rcpt_domain_check_is_case_insensitive(handler, envelope, session):
    result = await handler.handle_RCPT(None, session, envelope, "user@EXAMPLE.COM", [])
    assert result == "250 OK"


async def test_enqueue_and_broadcast_encrypts_before_queuing(
    handler, mock_queue, broadcaster, test_private_key
):
    plain = b"raw message"
    msg_id = await handler._enqueue_and_broadcast(plain)
    ciphertext = mock_queue.enqueue.call_args[0][0]
    # Stored bytes must not be plaintext
    assert ciphertext != plain
    # Must be decryptable with the corresponding private key
    assert SealedBox(test_private_key).decrypt(ciphertext) == plain
    broadcaster.broadcast.assert_awaited_once()
    assert msg_id == 42


async def test_data_returns_250_on_success(handler, envelope, session):
    f: ConcurrentFuture[int] = ConcurrentFuture()
    f.set_result(42)
    with patch("chameleon_relay.handler.asyncio.run_coroutine_threadsafe", return_value=f):
        result = await handler.handle_DATA(None, session, envelope)
    assert result == "250 OK"


async def test_data_returns_421_on_enqueue_failure(handler, envelope, session):
    f: ConcurrentFuture[int] = ConcurrentFuture()
    f.set_exception(Exception("db error"))
    with patch("chameleon_relay.handler.asyncio.run_coroutine_threadsafe", return_value=f):
        result = await handler.handle_DATA(None, session, envelope)
    assert result.startswith("421")


async def test_data_returns_421_on_timeout(handler, envelope, session):
    f: ConcurrentFuture[int] = ConcurrentFuture()
    f.set_exception(TimeoutError())
    with patch("chameleon_relay.handler.asyncio.run_coroutine_threadsafe", return_value=f):
        result = await handler.handle_DATA(None, session, envelope)
    assert result.startswith("421")


async def test_data_received_header_omits_recipient(handler, envelope, session):
    """Received header must not contain the recipient alias address."""
    envelope.rcpt_tos = ["private@example.com"]
    envelope.content = b"Subject: Test\r\n\r\nBody"
    captured_msgs: list[bytes] = []

    async def spy(msg: bytes) -> int:
        captured_msgs.append(msg)
        return 1

    # Spy replaces _enqueue_and_broadcast entirely — no encryption occurs.
    # We're testing the Received header construction, not the encryption layer.
    handler._enqueue_and_broadcast = spy

    f: ConcurrentFuture[int] = ConcurrentFuture()
    f.set_result(1)
    with patch(
        "chameleon_relay.handler.asyncio.run_coroutine_threadsafe", return_value=f
    ) as mock_rcts:
        await handler.handle_DATA(None, session, envelope)

    coro = mock_rcts.call_args[0][0]
    await coro

    assert len(captured_msgs) == 1
    msg = captured_msgs[0]
    received_section = msg[: msg.index(envelope.content)]
    assert b"private@example.com" not in received_section
    assert received_section.startswith(b"Received:")
