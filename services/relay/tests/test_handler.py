from unittest.mock import AsyncMock, patch

import aiosmtplib
import pytest


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


async def test_data_calls_send_with_correct_args(handler, envelope, session):
    envelope.rcpt_tos = ["user@example.com"]
    with patch("chameleon_relay.handler.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        result = await handler.handle_DATA(None, session, envelope)
    assert result == "250 OK"
    mock_send.assert_awaited_once()
    args, kwargs = mock_send.call_args
    assert kwargs["sender"] == "sender@external.com"
    assert kwargs["recipients"] == ["user@example.com"]
    assert kwargs["hostname"] == "127.0.0.1"
    assert kwargs["port"] == 2525


async def test_data_prepends_received_header(handler, envelope, session):
    envelope.rcpt_tos = ["user@example.com"]
    captured: dict = {}

    async def capture(msg, **kwargs):
        captured["message"] = msg

    with patch("chameleon_relay.handler.aiosmtplib.send", side_effect=capture):
        await handler.handle_DATA(None, session, envelope)

    assert captured["message"].startswith(b"Received:")
    assert envelope.content in captured["message"]


async def test_received_header_omits_recipient(handler, envelope, session):
    envelope.rcpt_tos = ["user@example.com"]
    captured: dict = {}

    async def capture(msg, **kwargs):
        captured["message"] = msg

    with patch("chameleon_relay.handler.aiosmtplib.send", side_effect=capture):
        await handler.handle_DATA(None, session, envelope)

    # Extract only the injected Received header (before the original content)
    injected = captured["message"][: captured["message"].index(envelope.content)]
    assert b"user@example.com" not in injected


async def test_data_returns_421_on_smtp_exception(handler, envelope, session):
    envelope.rcpt_tos = ["user@example.com"]
    with patch(
        "chameleon_relay.handler.aiosmtplib.send",
        new_callable=AsyncMock,
        side_effect=aiosmtplib.SMTPException("Connection refused"),
    ):
        result = await handler.handle_DATA(None, session, envelope)
    assert result.startswith("421")


async def test_data_returns_421_on_os_error(handler, envelope, session):
    envelope.rcpt_tos = ["user@example.com"]
    with patch(
        "chameleon_relay.handler.aiosmtplib.send",
        new_callable=AsyncMock,
        side_effect=OSError("Connection refused"),
    ):
        result = await handler.handle_DATA(None, session, envelope)
    assert result.startswith("421")
