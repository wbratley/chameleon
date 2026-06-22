import asyncio
from pathlib import Path

from chameleon_local.delivery import _write_to_maildir, deliver


def test_write_creates_file_in_new(tmp_path):
    message = b"Subject: Hello\r\n\r\nWorld"
    key = _write_to_maildir(str(tmp_path), message)
    files = list((tmp_path / "new").iterdir())
    assert len(files) == 1
    assert key != ""


def test_write_preserves_content(tmp_path):
    message = b"Subject: Preserve\r\n\r\nContent here"
    _write_to_maildir(str(tmp_path), message)
    written = next((tmp_path / "new").iterdir()).read_bytes()
    # MaildirMessage normalises CRLF -> LF on write; Dovecot re-adds CRLF over IMAP
    assert b"Subject: Preserve" in written
    assert b"Content here" in written


def test_write_returns_nonempty_key(tmp_path):
    key = _write_to_maildir(str(tmp_path), b"Subject: Key\r\n\r\nBody")
    assert key


def test_write_creates_maildir_dirs_if_missing(tmp_path):
    subdir = tmp_path / "nonexistent"
    _write_to_maildir(str(subdir), b"Subject: New\r\n\r\nBody")
    assert (subdir / "new").is_dir()
    assert (subdir / "cur").is_dir()
    assert (subdir / "tmp").is_dir()


async def test_concurrent_writes_no_collision(tmp_path):
    messages = [f"Subject: Msg {i}\r\n\r\nBody {i}".encode() for i in range(20)]
    await asyncio.gather(*[deliver(str(tmp_path), m) for m in messages])
    files = list((tmp_path / "new").iterdir())
    assert len(files) == 20
