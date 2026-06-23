from unittest.mock import AsyncMock, MagicMock

import pytest
from aiosmtpd.smtp import Envelope

from chameleon_relay.api import Broadcaster
from chameleon_relay.config import RelaySettings
from chameleon_relay.handler import RelayHandler
from chameleon_relay.queue import MessageQueue


@pytest.fixture
def settings():
    return RelaySettings(
        MY_DOMAIN="example.com",
        RELAY_HOSTNAME="relay.example.com",
        API_TOKEN="test-token",
        QUEUE_DB_PATH=":memory:",
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
    # main_loop=None — tests either call _enqueue_and_broadcast directly
    # or mock asyncio.run_coroutine_threadsafe
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
