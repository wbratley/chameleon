import email.utils
import logging

import aiosmtplib
from aiosmtpd.smtp import Envelope, Session, SMTP

from .config import RelaySettings

logger = logging.getLogger(__name__)


class RelayHandler:
    def __init__(self, settings: RelaySettings) -> None:
        self._settings = settings

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

        # Omit the "for" clause intentionally — it would expose the alias address
        received = (
            f"Received: from {host_name} ([{peer_ip}])\r\n"
            f"\tby {self._settings.RELAY_HOSTNAME} (chameleon-relay) with ESMTP;\r\n"
            f"\t{email.utils.formatdate(localtime=False)}\r\n"
        ).encode("ascii")

        message_bytes = received + envelope.content

        try:
            await aiosmtplib.send(
                message_bytes,
                sender=envelope.mail_from,
                recipients=envelope.rcpt_tos,
                hostname=self._settings.LOCAL_SMTP_HOST,
                port=self._settings.LOCAL_SMTP_PORT,
                timeout=30,
            )
            return "250 OK"
        except (aiosmtplib.SMTPException, OSError) as exc:
            logger.error("forward_failed error=%s", type(exc).__name__)
            return "421 4.4.1 Local delivery unavailable, try again later"
