import base64
import json
import logging

from aiohttp import web, WSMsgType

from .config import RelaySettings
from .queue import MessageQueue

logger = logging.getLogger(__name__)


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[web.WebSocketResponse] = set()

    def add(self, ws: web.WebSocketResponse) -> None:
        self._clients.add(ws)

    def remove(self, ws: web.WebSocketResponse) -> None:
        self._clients.discard(ws)

    async def broadcast(self, msg_id: int, raw: bytes) -> None:
        if not self._clients:
            return
        frame = json.dumps({
            "type": "deliver",
            "id": msg_id,
            "message": base64.b64encode(raw).decode("ascii"),
        })
        dead: set[web.WebSocketResponse] = set()
        for ws in self._clients:
            try:
                await ws.send_str(frame)
            except Exception:
                dead.add(ws)
        self._clients -= dead


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    settings: RelaySettings = request.app["settings"]
    queue: MessageQueue = request.app["queue"]
    broadcaster: Broadcaster = request.app["broadcaster"]

    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {settings.API_TOKEN}":
        raise web.HTTPUnauthorized()

    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    broadcaster.add(ws)

    try:
        for msg_id, raw in await queue.pending():
            await ws.send_json({
                "type": "deliver",
                "id": msg_id,
                "message": base64.b64encode(raw).decode("ascii"),
            })

        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    if data.get("type") == "ack" and isinstance(data.get("id"), int):
                        await queue.ack(data["id"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    logger.warning("malformed_frame")
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        broadcaster.remove(ws)

    return ws


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def make_app(
    settings: RelaySettings, queue: MessageQueue
) -> tuple[web.Application, Broadcaster]:
    broadcaster = Broadcaster()
    app = web.Application()
    app["settings"] = settings
    app["queue"] = queue
    app["broadcaster"] = broadcaster
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/health", health_handler)
    return app, broadcaster
