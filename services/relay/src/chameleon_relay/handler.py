import asyncio
import base64
import email.utils
import logging

from nacl.public import PublicKey, SealedBox
from aiosmtpd.smtp import Envelope, Session, SMTP

from .api import Broadcaster
from .config import RelaySettings
from .queue import MessageQueue

logger = logging.getLogger(__name__)


class RelayHandler:
    def __init__(
        self,
        settings: RelaySettings,
        queue: MessageQueue,
        broadcaster: Broadcaster,
        main_loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._settings = settings
        self._queue = queue
        self._broadcaster = broadcaster
        self._main_loop = main_loop
        self._box = SealedBox(PublicKey(base64.b64decode(settings.PUBLIC_KEY)))

    async def handle_RCPT(
        self,
        server: SMTP,
        session: Session,
        envelope: Envelope,
        address: str,
        rcpt_options: list[str],
    ) -> str:
        domain = address.split("@", 1)[-1].lower()
        if domain != self._settings.MY_DOMAIN.lower():
            return "550 5.7.1 Relaying not permitted"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(
        self,
        server: SMTP,
        session: Session,
        envelope: Envelope,
    ) -> str:
        peer_ip = session.peer[0] if session.peer else "unknown"
        host_name = session.host_name or "unknown"

        # The "for" clause is intentionally omitted from Received — it would leak the
        # alias address into the message delivered to the user's inbox.
        received = (
            f"Received: from {host_name} ([{peer_ip}])\r\n"
            f"\tby {self._settings.RELAY_HOSTNAME} (chameleon-relay) with ESMTP;\r\n"
            f"\t{email.utils.formatdate(localtime=False)}\r\n"
        ).encode("ascii")

        # Carry the true envelope recipient(s) to the local server so it can enforce
        # burns reliably instead of guessing from the To header. This lives *inside*
        # the sealed-box payload — the relay operator never sees it — and the local
        # server strips it before writing to the Maildir, so it never reaches the inbox.
        rcpt_header = (
            "X-Chameleon-Rcpt: " + ", ".join(envelope.rcpt_tos) + "\r\n"
        ).encode("utf-8")
        message_bytes = rcpt_header + received + envelope.content

        future = asyncio.run_coroutine_threadsafe(
            self._enqueue_and_broadcast(message_bytes),
            self._main_loop,
        )
        try:
            msg_id = future.result(timeout=10)
            logger.info("queued id=%d", msg_id)
            return "250 OK"
        except TimeoutError:
            logger.error("enqueue_timeout")
            return "421 4.4.1 Queue unavailable, try again later"
        except Exception as exc:
            logger.error("enqueue_failed error=%s", type(exc).__name__)
            return "421 4.4.1 Queue error, try again later"

    async def _enqueue_and_broadcast(self, plain_bytes: bytes) -> int:
        ciphertext = self._box.encrypt(plain_bytes)
        msg_id = await self._queue.enqueue(ciphertext)
        await self._broadcaster.broadcast(msg_id, ciphertext)
        return msg_id
