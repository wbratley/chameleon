from unittest.mock import MagicMock

import pytest
from aiosmtpd.smtp import Envelope

from chameleon_relay.config import RelaySettings
from chameleon_relay.handler import RelayHandler


@pytest.fixture
def settings():
    return RelaySettings(
        MY_DOMAIN="example.com",
        RELAY_HOSTNAME="relay.example.com",
        LOCAL_SMTP_HOST="127.0.0.1",
        LOCAL_SMTP_PORT=2525,
    )


@pytest.fixture
def handler(settings):
    return RelayHandler(settings)


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
    s.peer = ("1.2.3.4", 40000)
    s.host_name = "mail.external.com"
    return s
