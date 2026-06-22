import pytest
from pydantic import ValidationError

from chameleon_relay.config import RelaySettings


def test_defaults_applied():
    s = RelaySettings(MY_DOMAIN="example.com", RELAY_HOSTNAME="relay.example.com")
    assert s.LISTEN_HOST == "0.0.0.0"
    assert s.LISTEN_PORT == 1025
    assert s.LOCAL_SMTP_HOST == "127.0.0.1"
    assert s.LOCAL_SMTP_PORT == 2525
    assert s.MAX_MESSAGE_SIZE == 26_214_400
    assert s.TLS_CERT_PATH is None
    assert s.TLS_KEY_PATH is None


def test_missing_domain_raises():
    with pytest.raises(ValidationError):
        RelaySettings(RELAY_HOSTNAME="relay.example.com")


def test_missing_hostname_raises():
    with pytest.raises(ValidationError):
        RelaySettings(MY_DOMAIN="example.com")
