from unittest.mock import MagicMock

import pytest
from aiosmtpd.smtp import Envelope

from chameleon_local.config import LocalSettings
from chameleon_local.handler import LocalDeliveryHandler


@pytest.fixture
def settings(tmp_path):
    return LocalSettings(
        LISTEN_HOST="127.0.0.1",
        LISTEN_PORT=2525,
        MAILDIR_PATH=str(tmp_path),
        ALLOWED_PEERS="127.0.0.1",
    )


@pytest.fixture
def handler(settings):
    return LocalDeliveryHandler(settings)


@pytest.fixture
def envelope():
    e = Envelope()
    e.mail_from = "sender@external.com"
    e.rcpt_tos = []
    e.content = b"Subject: Test\r\n\r\nHello"
    return e


@pytest.fixture
def session():
    s = MagicMock()
    s.peer = ("127.0.0.1", 40000)
    s.host_name = "localhost"
    return s
