import base64

import pytest
from nacl.public import PrivateKey

from chameleon_local.aliases import AliasDB
from chameleon_local.config import LocalSettings


@pytest.fixture(scope="session")
def test_private_key():
    return PrivateKey.generate()


@pytest.fixture
def private_key_file(tmp_path, test_private_key):
    key_file = tmp_path / "private_key"
    key_file.write_text(base64.b64encode(bytes(test_private_key)).decode())
    return key_file


@pytest.fixture
def settings(tmp_path, private_key_file):
    return LocalSettings(
        RELAY_WS_URL="ws://127.0.0.1:9999/ws",
        RELAY_TOKEN="test-token",
        MY_DOMAIN="example.com",
        MAILDIR_PATH=str(tmp_path),
        ALIAS_DB_PATH=str(tmp_path / "aliases.db"),
        PRIVATE_KEY_PATH=str(private_key_file),
    )


@pytest.fixture
async def alias_db(tmp_path):
    db = AliasDB(str(tmp_path / "aliases.db"))
    await db.setup()
    yield db
    await db.close()
