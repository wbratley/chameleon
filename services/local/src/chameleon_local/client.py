import asyncio
import base64
import json
import logging

import aiohttp
from aiohttp import WSMsgType

from .config import LocalSettings
from .delivery import deliver

logger = logging.getLogger(__name__)


async def _handle_deliver(
    ws: aiohttp.ClientWebSocketResponse,
    settings: LocalSettings,
    data: dict,
) -> None:
    msg_id = data["id"]
    try:
        raw = base64.b64decode(data["message"])
        key = await deliver(settings.MAILDIR_PATH, raw)
        logger.info("delivered id=%d key=%s", msg_id, key)
        await ws.send_json({"type": "ack", "id": msg_id})
    except Exception as exc:
        # No ack — relay retains the message as pending and resends on reconnect
        logger.error("delivery_failed id=%d error=%s", msg_id, type(exc).__name__)


async def run_client(settings: LocalSettings) -> None:
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
                                    await _handle_deliver(ws, settings, data)
                            except (json.JSONDecodeError, KeyError, TypeError):
                                logger.warning("malformed_frame")
                        elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                            break

        except (aiohttp.ClientError, OSError) as exc:
            logger.warning("connection_failed error=%s backoff=%.1f", exc, backoff)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)
