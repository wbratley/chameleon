import pytest

from chameleon_local.config import LocalSettings


@pytest.fixture
def settings(tmp_path):
    return LocalSettings(
        RELAY_WS_URL="ws://127.0.0.1:9999/ws",
        RELAY_TOKEN="test-token",
        MAILDIR_PATH=str(tmp_path),
    )
