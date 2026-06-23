import pytest
from pydantic import ValidationError

from chameleon_relay.config import RelaySettings


def test_defaults_applied():
    s = RelaySettings(
        MY_DOMAIN="example.com",
        RELAY_HOSTNAME="relay.example.com",
        API_TOKEN="secret",
    )
    assert s.LISTEN_HOST == "0.0.0.0"
    assert s.LISTEN_PORT == 1025
    assert s.MAX_MESSAGE_SIZE == 26_214_400
    assert s.API_HOST == "127.0.0.1"
    assert s.API_PORT == 8080
    assert s.QUEUE_DB_PATH == "/data/queue.db"
    assert s.QUEUE_RETAIN_MINUTES == 30
    assert s.TLS_CERT_PATH is None
    assert s.TLS_KEY_PATH is None


def test_missing_domain_raises():
    with pytest.raises(ValidationError):
        RelaySettings(RELAY_HOSTNAME="relay.example.com", API_TOKEN="secret")


def test_missing_hostname_raises():
    with pytest.raises(ValidationError):
        RelaySettings(MY_DOMAIN="example.com", API_TOKEN="secret")


def test_missing_api_token_raises():
    with pytest.raises(ValidationError):
        RelaySettings(MY_DOMAIN="example.com", RELAY_HOSTNAME="relay.example.com")
