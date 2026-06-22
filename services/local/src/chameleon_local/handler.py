import logging

from aiosmtpd.smtp import Envelope, Session, SMTP

from .config import LocalSettings
from .delivery import deliver

logger = logging.getLogger(__name__)


class LocalDeliveryHandler:
    def __init__(self, settings: LocalSettings) -> None:
        self._settings = settings

    async def handle_RCPT(
        self,
        server: SMTP,
        session: Session,
        envelope: Envelope,
        address: str,
        rcpt_options: list[str],
    ) -> str:
        peer_ip = session.peer[0]
        if peer_ip not in self._settings.allowed_peer_set:
            logger.warning("rejected_peer peer=%s", peer_ip)
            return "421 4.7.0 Connection not permitted"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(
        self,
        server: SMTP,
        session: Session,
        envelope: Envelope,
    ) -> str:
        try:
            key = await deliver(self._settings.MAILDIR_PATH, envelope.content)
            logger.info("delivered key=%s", key)
            return "250 OK"
        except Exception as exc:
            logger.error("delivery_failed error=%s", type(exc).__name__)
            return "421 4.3.0 Mail system error"
