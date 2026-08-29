import base64

import pytest
from nacl.public import PrivateKey, PublicKey, SealedBox

from chameleon_local.__main__ import _keygen, _publickey


def _printed_public_key(capsys) -> bytes:
    return base64.b64decode(
        capsys.readouterr().out.split("CHAMELEON_PUBLIC_KEY=")[1].split()[0]
    )


def test_publickey_rederives_keygen_print(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _keygen()
    printed = _printed_public_key(capsys)

    _publickey()
    rederived = _printed_public_key(capsys)

    assert rederived == printed


def test_publickey_still_opens_sealed_boxes(tmp_path, monkeypatch, capsys):
    """The re-derived public key must belong to the stored private key:
    something sealed to it (as the relay does) must decrypt locally."""
    monkeypatch.chdir(tmp_path)
    _keygen()
    _publickey()
    pub = _printed_public_key(capsys)
    priv = PrivateKey(
        base64.b64decode((tmp_path / "secrets/private_key").read_text().strip())
    )

    ciphertext = SealedBox(PublicKey(bytes(pub))).encrypt(b"ping")

    assert SealedBox(priv).decrypt(ciphertext) == b"ping"


def test_publickey_missing_key_exits_with_hint(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit, match="keygen"):
        _publickey()
