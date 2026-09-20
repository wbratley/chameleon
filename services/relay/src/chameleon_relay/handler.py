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


def _ascii_hostname(host_name: str | None) -> str:
    """Coerce an EHLO hostname to a plain-ASCII str.

    RFC 5321 §4.1.1.1 restricts the EHLO argument to ASCII, but a client can
    send anything. session.host_name is pasted verbatim into the Received
    header, which must encode as ASCII — a UTF-8 hostname (e.g. IDN) would
    otherwise raise UnicodeEncodeError inside handle_DATA and temp-fail the
    message (issue #15).
    """
    if not host_name:
        return "unknown"
    try:
        return host_name.encode("ascii").decode("ascii")
    except UnicodeEncodeError:
        pass
    try:
        return host_name.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        # Not even IDNA-representable (e.g. empty label): drop the client's
        # claim rather than lose the message over it.
        return "unknown"


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
        # Fire-and-forget broadcast tasks: keep a reference so they can't be
        # garbage-collected mid-flight (see _enqueue_and_broadcast).
        self._broadcast_tasks: set[asyncio.Task[None]] = set()

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
        host_name = _ascii_hostname(session.host_name)

        # RFC 3848 service-type keyword: ESMTPS marks a session upgraded via
        # STARTTLS (aiosmtpd populates session.ssl only after the handshake);
        # plain ESMTP otherwise. MUAs and hop-analysis tools key on this to
        # show whether the transport was encrypted — a bare ESMTP reads as
        # plaintext even when the session did upgrade.
        smtp_with = "ESMTPS" if session.ssl is not None else "ESMTP"

        # The "for" clause is intentionally omitted from Received — it would leak the
        # alias address into the message delivered to the user's inbox.
        received = (
            f"Received: from {host_name} ([{peer_ip}])\r\n"
            f"\tby {self._settings.RELAY_HOSTNAME} (chameleon-relay) with {smtp_with};\r\n"
            f"\t{email.utils.formatdate(localtime=False)}\r\n"
        ).encode("ascii")

        # Carry the true envelope recipient(s) to the local server so it can enforce
        # burns reliably instead of guessing from the To header. This lives *inside*
        # the sealed-box payload — the relay operator never sees it — and the local
        # server strips it before writing to the Maildir, so it never reaches the inbox.
        # The block is length-prefixed rather than a positional header convention:
        # the client reads exactly len(rcpt_block) bytes and then requires its own
        # Received: header, so sender-controlled DATA bytes can never be parsed as
        # recipient info, whatever a future refactor does to the prepends (issue #6).
        rcpt_block = ", ".join(envelope.rcpt_tos).encode("utf-8")
        rcpt_frame = (
            b"CHAMELEON-RCPT/1 "
            + str(len(rcpt_block)).encode("ascii")
            + b"\r\n"
            + rcpt_block
        )
        message_bytes = rcpt_frame + received + envelope.content

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
        # The broadcast is only a latency optimization — ws_handler replays all
        # pending messages whenever a client (re)connects — so it must never
        # gate the SMTP 250. A connected-but-stalled client can make send_str
        # hang; awaiting it here would temp-fail DATA with a 421 for a message
        # that is already durably enqueued, and the sender's retry would then
        # enqueue a duplicate (issue #4). Only the enqueue gates the response.
        task = asyncio.create_task(self._broadcaster.broadcast(msg_id, ciphertext))
        self._broadcast_tasks.add(task)
        task.add_done_callback(self._finish_broadcast)
        return msg_id

    def _finish_broadcast(self, task: asyncio.Task[None]) -> None:
        """Drop finished broadcast tasks and log anything they raised, so a
        background failure is not silently swallowed."""
        self._broadcast_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("broadcast_failed error=%s", type(exc).__name__)
