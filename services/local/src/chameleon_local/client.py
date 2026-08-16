import asyncio
import base64
import json
import logging
from pathlib import Path

import aiohttp
from aiohttp import WSMsgType
from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, SealedBox

from .aliases import AliasDB
from .config import LocalSettings
from .delivery import deliver

logger = logging.getLogger(__name__)


# Length-prefixed recipient frame the relay prepends (inside the encrypted
# payload) carrying the true envelope recipient(s):
#   "CHAMELEON-RCPT/1 <n>\r\n" + exactly n bytes of comma-separated recipients,
# followed immediately by the relay's Received: header and the message.
# Because the block length is explicit, message content can never be parsed
# as frame bytes — unlike the legacy header convention below (issue #6).
_FRAME_MAGIC = b"CHAMELEON-RCPT/1 "
# Legacy positional header written by relays before the frame format; kept only
# so messages queued across a staggered upgrade still deliver. Remove once no
# pre-frame relay can feed this deployment.
_RCPT_HEADER = b"X-Chameleon-Rcpt:"


def _parse_rcpt_frame(raw: bytes) -> tuple[list[str] | None, bytes]:
    """Parse the length-prefixed recipient frame.

    Returns (recipients, message). recipients is None when the payload is not
    a valid frame — including when any sanity check fails, so the caller falls
    back to the legacy format / discards rather than trusting a suspect frame.
    """
    if not raw.startswith(_FRAME_MAGIC):
        return None, raw
    head, sep, rest = raw.partition(b"\r\n")
    if not sep:
        return None, raw
    length = head[len(_FRAME_MAGIC):]
    if not length.isdigit():
        return None, raw
    n = int(length)
    if n <= 0 or len(rest) < n:
        return None, raw  # truncated or empty recipient block
    block, message = rest[:n], rest[n:]
    # The relay always writes its own Received: header immediately after the
    # frame. Anything else means this isn't a frame our relay constructed.
    if not message.startswith(b"Received:"):
        return None, raw
    recipients = [a.strip().lower() for a in block.decode("utf-8", "replace").split(",") if a.strip()]
    return (recipients or None), message


def _split_rcpt_header(raw: bytes) -> tuple[list[str] | None, bytes]:
    """Peel off the legacy leading X-Chameleon-Rcpt header.

    Transitional (staggered upgrades): same construction as the frame — the
    relay prepended this line ahead of its Received: header, which must follow
    for the header to be trusted.
    """
    if not raw.startswith(_RCPT_HEADER):
        return None, raw
    line, sep, rest = raw.partition(b"\n")
    if not sep:
        return None, raw
    if not rest.startswith(b"Received:"):
        return None, raw
    value = line[len(_RCPT_HEADER):].decode("utf-8", "replace")
    recipients = [a.strip().lower() for a in value.split(",") if a.strip()]
    return (recipients or None), rest


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

        recipients, message = _parse_rcpt_frame(raw)
        if recipients is None:
            # Legacy framing from a relay queued before the frame format existed.
            recipients, message = _split_rcpt_header(raw)
        if recipients is None:
            # No trusted recipient info and no guessing from message content
            # any more (issue #6): a payload we cannot attribute cannot be
            # burn-checked, so discard it — like undecryptable mail, a retry
            # can never fix the bytes.
            logger.error("no_rcpt_frame id=%d — discarding", msg_id)
            await ws.send_json({"type": "ack", "id": msg_id})
            return

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
