import smtplib
import socket
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiosmtpd.controller import Controller

from chameleon_local.config import LocalSettings
from chameleon_local.handler import LocalDeliveryHandler


async def test_rcpt_rejects_disallowed_peer(handler, envelope, session):
    session.peer = ("1.2.3.4", 9999)
    result = await handler.handle_RCPT(None, session, envelope, "user@example.com", [])
    assert result.startswith("421")


async def test_rcpt_accepts_allowed_peer(handler, envelope, session):
    result = await handler.handle_RCPT(None, session, envelope, "user@example.com", [])
    assert result == "250 OK"
    assert "user@example.com" in envelope.rcpt_tos


async def test_data_delivers_message(handler, envelope, session, settings):
    envelope.content = b"Subject: Deliver me\r\n\r\nBody"
    result = await handler.handle_DATA(None, session, envelope)
    assert result == "250 OK"
    assert len(list((Path(settings.MAILDIR_PATH) / "new").iterdir())) == 1


async def test_bounce_message_accepted(handler, envelope, session, settings):
    envelope.mail_from = "<>"
    envelope.content = b"Subject: Bounce\r\n\r\nDSN"
    result = await handler.handle_DATA(None, session, envelope)
    assert result == "250 OK"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def local_server(tmp_path):
    port = _free_port()
    settings = LocalSettings(
        LISTEN_HOST="127.0.0.1",
        LISTEN_PORT=port,
        MAILDIR_PATH=str(tmp_path),
        ALLOWED_PEERS="127.0.0.1",
    )
    handler = LocalDeliveryHandler(settings)
    controller = Controller(handler, hostname="127.0.0.1", port=port)
    controller.start()
    yield controller, tmp_path
    controller.stop()


def test_end_to_end_smtp(local_server):
    controller, maildir_path = local_server
    port = controller.port
    with smtplib.SMTP("127.0.0.1", port) as smtp:
        smtp.sendmail(
            "sender@external.com",
            ["recipient@example.com"],
            b"Subject: E2E Test\r\n\r\nHello World",
        )
    files = list((maildir_path / "new").iterdir())
    assert len(files) == 1
    assert b"Hello World" in files[0].read_bytes()
