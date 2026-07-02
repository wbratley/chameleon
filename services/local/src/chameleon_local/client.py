import asyncio
import base64
import email as email_lib
import json
import logging
from email.utils import parseaddr
from pathlib import Path

import aiohttp
from aiohttp import WSMsgType
from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, SealedBox

from .aliases import AliasDB
from .config import LocalSettings
from .delivery import deliver

logger = logging.getLogger(__name__)


# Transport header the relay prepends (inside the encrypted payload) carrying the
# true envelope recipient(s). It must be stripped before the message is delivered.
_RCPT_HEADER = b"X-Chameleon-Rcpt:"


def _split_rcpt_header(raw: bytes) -> tuple[list[str] | None, bytes]:
    """Peel off the leading X-Chameleon-Rcpt header.

    Returns (recipients, message). recipients is None when the header is absent
    (e.g. a message queued by an older relay), in which case message == raw.
    """
    if not raw.startswith(_RCPT_HEADER):
        return None, raw
    line, sep, rest = raw.partition(b"\n")
    if not sep:
        return None, raw
    value = line[len(_RCPT_HEADER):].decode("utf-8", "replace")
    recipients = [a.strip().lower() for a in value.split(",") if a.strip()]
    return (recipients or None), rest


def _extract_to(raw: bytes) -> str | None:
    try:
        msg = email_lib.message_from_bytes(raw)
        _, addr = parseaddr(msg.get("To", ""))
        return addr.lower() or None
    except Exception:
        return None


async def _handle_deliver(
    ws: aiohttp.ClientWebSocketResponse,
    settings: LocalSettings,
    alias_db: AliasDB,
    box: SealedBox,
    data: dict,
) -> None:
    msg_id = data["id"]
    try:
        ciphertext = base64.b64decode(data["message"])
        try:
            raw = box.decrypt(ciphertext)
        except CryptoError:
            logger.error("decrypt_failed id=%d — discarding", msg_id)
            await ws.send_json({"type": "ack", "id": msg_id})
            return

        recipients, message = _split_rcpt_header(raw)
        if recipients is not None:
            # Authoritative: the relay validated these are at our domain. Deliver
            # unless every recipient alias is burned.
            delivered_any = False
            for addr in recipients:
                if await alias_db.record_delivery(addr):
                    delivered_any = True
            if not delivered_any:
                logger.info("burned id=%d recipients=%s", msg_id, recipients)
                await ws.send_json({"type": "ack", "id": msg_id})
                return
        else:
            # Backward-compat for messages queued before the relay added the header.
            # Only trust a To address at our own domain — never auto-register a
            # foreign address as an alias.
            to_addr = _extract_to(message)
            own_domain = "@" + settings.MY_DOMAIN.lower()
            if to_addr is not None and to_addr.endswith(own_domain):
                if not await alias_db.record_delivery(to_addr):
                    logger.info("burned id=%d address=%s", msg_id, to_addr)
                    await ws.send_json({"type": "ack", "id": msg_id})
                    return

        key = await deliver(settings.MAILDIR_PATH, message)
        logger.info("delivered id=%d key=%s", msg_id, key)
        await ws.send_json({"type": "ack", "id": msg_id})
    except Exception as exc:
        # No ack — relay retains the message as pending and resends on reconnect
        logger.error("delivery_failed id=%d error=%s", msg_id, type(exc).__name__)


async def run_client(settings: LocalSettings, alias_db: AliasDB) -> None:
    priv_bytes = base64.b64decode(Path(settings.PRIVATE_KEY_PATH).read_text().strip())
    box = SealedBox(PrivateKey(priv_bytes))

    backoff = 1.0
    max_backoff = 60.0

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    settings.RELAY_WS_URL,
                    headers={"Authorization": f"Bearer {settings.RELAY_TOKEN}"},
                    heartbeat=30,
                ) as ws:
                    logger.info("connected url=%s", settings.RELAY_WS_URL)
                    backoff = 1.0  # reset on successful connect

                    async for msg in ws:
                        if msg.type == WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                if data.get("type") == "deliver":
                                    await _handle_deliver(ws, settings, alias_db, box, data)
                            except (json.JSONDecodeError, KeyError, TypeError):
                                logger.warning("malformed_frame")
                        elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                            break

        except (aiohttp.ClientError, OSError) as exc:
            logger.warning("connection_failed error=%s backoff=%.1f", exc, backoff)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)
