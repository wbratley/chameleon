import asyncio
import mailbox
import os


def _write_to_maildir(path: str, message_bytes: bytes) -> str:
    # Maildir.__init__ only creates cur/new/tmp when the root doesn't exist yet.
    # Ensure all three exist so add() can write to tmp/ regardless.
    for subdir in ("cur", "new", "tmp"):
        os.makedirs(os.path.join(path, subdir), exist_ok=True)
    md = mailbox.Maildir(path, create=True)
    msg = mailbox.MaildirMessage(message_bytes)
    msg.set_subdir("new")
    key = md.add(msg)
    md.close()
    return key


async def deliver(path: str, message_bytes: bytes) -> str:
    return await asyncio.to_thread(_write_to_maildir, path, message_bytes)
