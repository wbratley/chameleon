import base64
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiosmtpd.smtp import Envelope
from nacl.public import PrivateKey

from chameleon_relay.api import Broadcaster
from chameleon_relay.config import RelaySettings
from chameleon_relay.handler import RelayHandler
from chameleon_relay.queue import MessageQueue


@pytest.fixture(scope="session")
def test_private_key():
    return PrivateKey.generate()


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
def mock_queue():
    q = AsyncMock(spec=MessageQueue)
    q.enqueue.return_value = 42
    return q


@pytest.fixture
def broadcaster():
    return AsyncMock(spec=Broadcaster)


@pytest.fixture
def handler(settings, mock_queue, broadcaster):
    return RelayHandler(settings, mock_queue, broadcaster, None)


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
