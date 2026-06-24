import pytest

from chameleon_local.aliases import AliasDB
from chameleon_local.config import LocalSettings


@pytest.fixture
def settings(tmp_path):
    return LocalSettings(
        RELAY_WS_URL="ws://127.0.0.1:9999/ws",
        RELAY_TOKEN="test-token",
        MY_DOMAIN="example.com",
        MAILDIR_PATH=str(tmp_path),
        ALIAS_DB_PATH=str(tmp_path / "aliases.db"),
    )


@pytest.fixture
async def alias_db(tmp_path):
    db = AliasDB(str(tmp_path / "aliases.db"))
    await db.setup()
    yield db
    await db.close()
