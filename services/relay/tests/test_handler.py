from concurrent.futures import Future as ConcurrentFuture
from unittest.mock import patch

import asyncio

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
    # Broadcast is fire-and-forget: drain the background task before asserting.
    await asyncio.gather(*handler._broadcast_tasks)
    broadcaster.broadcast.assert_awaited_once()
    assert msg_id == 42


async def test_enqueue_returns_without_waiting_for_broadcast(
    handler, mock_queue, broadcaster
):
    """Regression (issue #4): a stalled WebSocket client must not delay or
    temp-fail the SMTP response. The message is durably enqueued and the
    broadcast runs in the background."""
    release = asyncio.Event()

    async def stalled_broadcast(msg_id: int, raw: bytes) -> None:
        await release.wait()  # simulates send_str hanging on a stalled client

    broadcaster.broadcast.side_effect = stalled_broadcast

    msg_id = await handler._enqueue_and_broadcast(b"payload")

    # Returned promptly even though the broadcast can never complete.
    assert msg_id == 42
    mock_queue.enqueue.assert_awaited_once()

    # The broadcast was genuinely scheduled, not skipped.
    await asyncio.sleep(0)
    broadcaster.broadcast.assert_awaited_once()
    assert handler._broadcast_tasks

    release.set()
    await asyncio.gather(*handler._broadcast_tasks)


async def test_broadcast_failure_is_logged_not_lost(handler, mock_queue, broadcaster, caplog):
    """A background broadcast exception must be logged, not silently
    swallowed by the fire-and-forget task."""
    broadcaster.broadcast.side_effect = RuntimeError("boom")

    msg_id = await handler._enqueue_and_broadcast(b"payload")
    assert msg_id == 42

    await asyncio.gather(*handler._broadcast_tasks, return_exceptions=True)
    await asyncio.sleep(0)  # let the done-callback run
    assert any("broadcast_failed" in r.message for r in caplog.records)


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


async def _capture_payload(handler, envelope, session) -> bytes:
    captured_msgs: list[bytes] = []

    async def spy(msg: bytes) -> int:
        captured_msgs.append(msg)
        return 1

    # Spy replaces _enqueue_and_broadcast entirely — no encryption occurs.
    # We're inspecting the plaintext payload construction, not the encryption layer.
    handler._enqueue_and_broadcast = spy

    f: ConcurrentFuture[int] = ConcurrentFuture()
    f.set_result(1)
    with patch(
        "chameleon_relay.handler.asyncio.run_coroutine_threadsafe", return_value=f
    ) as mock_rcts:
        await handler.handle_DATA(None, session, envelope)

    await mock_rcts.call_args[0][0]
    assert len(captured_msgs) == 1
    return captured_msgs[0]


async def test_data_non_ascii_ehlo_hostname_still_accepted(handler, envelope, session):
    """Regression (issue #15): a UTF-8 EHLO hostname must not crash the ASCII
    encode of Received and temp-fail the message — nothing is wrong with it."""
    session.host_name = "müller.example"
    envelope.rcpt_tos = ["private@example.com"]
    envelope.content = b"Subject: Test\r\n\r\nBody"

    f: ConcurrentFuture[int] = ConcurrentFuture()
    f.set_result(42)
    with patch("chameleon_relay.handler.asyncio.run_coroutine_threadsafe", return_value=f):
        result = await handler.handle_DATA(None, session, envelope)
    assert result == "250 OK"


async def test_data_received_header_is_ascii_for_idn_ehlo(handler, envelope, session):
    """An internationalized EHLO hostname is punycoded into the Received
    header, keeping it ASCII-only (issue #15)."""
    session.host_name = "müller.example"
    envelope.content = b"Subject: Test\r\n\r\nBody"

    msg = await _capture_payload(handler, envelope, session)

    received_section = msg[msg.index(b"Received:"):]
    received_section.decode("ascii")  # must not raise
    assert b"xn--mller-kva.example" in received_section


async def test_data_received_header_falls_back_to_unknown_host(handler, envelope, session):
    """A hostname that is neither ASCII nor IDNA-encodable (empty label) is
    replaced with "unknown" instead of failing DATA (issue #15)."""
    session.host_name = "müller..example"
    envelope.content = b"Subject: Test\r\n\r\nBody"

    msg = await _capture_payload(handler, envelope, session)

    received_section = msg[msg.index(b"Received:"):]
    received_section.decode("ascii")  # must not raise
    assert b"from unknown" in received_section


async def test_data_received_marks_starttls_session(handler, envelope, session):
    """A session upgraded via STARTTLS gets the RFC 3848 ESMTPS keyword, so
    MUAs reading the hop chain show it as encrypted."""
    session.host_name = "mail-sender.example"
    # aiosmtpd sets session.ssl (to the TLS transport's _extra dict) only
    # after a successful STARTTLS handshake.
    session.ssl = {"ssl_object": object()}
    envelope.content = b"Subject: Test\r\n\r\nBody"

    msg = await _capture_payload(handler, envelope, session)

    received_section = msg[msg.index(b"Received:"):]
    assert b"with ESMTPS" in received_section


async def test_data_received_marks_plaintext_session(handler, envelope, session):
    """A session that never issued STARTTLS keeps the plain ESMTP keyword."""
    session.host_name = "mail-sender.example"
    envelope.content = b"Subject: Test\r\n\r\nBody"  # fixture default: ssl=None (plaintext)

    msg = await _capture_payload(handler, envelope, session)

    received_section = msg[msg.index(b"Received:"):]
    assert b"with ESMTP;" in received_section


async def test_data_received_header_omits_recipient(handler, envelope, session):
    """The Received header must not contain the recipient (no leaky "for" clause)."""
    envelope.rcpt_tos = ["private@example.com"]
    envelope.content = b"Subject: Test\r\n\r\nBody"

    msg = await _capture_payload(handler, envelope, session)

    preamble = msg[: msg.index(envelope.content)]
    # The Received portion (everything from "Received:" on) must not leak the alias.
    received_section = preamble[preamble.index(b"Received:"):]
    assert b"private@example.com" not in received_section


async def test_data_embeds_recipient_for_local(handler, envelope, session):
    """The envelope recipient(s) are carried in a length-prefixed frame for
    burn enforcement (issue #6)."""
    envelope.rcpt_tos = ["private@example.com", "second@example.com"]
    envelope.content = b"Subject: Test\r\n\r\nBody"

    msg = await _capture_payload(handler, envelope, session)

    # Frame is first, inside the (to-be-)encrypted payload, ahead of Received,
    # and its length prefix makes the boundary exact — no header convention
    # that message content could collide with.
    block = b"private@example.com, second@example.com"
    prefix = b"CHAMELEON-RCPT/1 " + str(len(block)).encode() + b"\r\n" + block
    assert msg.startswith(prefix)
    assert msg[len(prefix):].startswith(b"Received:")
    assert msg.endswith(envelope.content)
