import asyncio
import logging
import signal
import ssl

from aiohttp import web
from aiosmtpd.controller import Controller

from .api import make_app
from .config import RelaySettings
from .handler import RelayHandler
from .queue import MessageQueue

logger = logging.getLogger(__name__)


async def _sweep_loop(queue: MessageQueue, interval: int = 300) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            count = await queue.sweep()
            if count:
                logger.info("sweep deleted=%d", count)
        except Exception as exc:
            logger.error("sweep_failed error=%s", type(exc).__name__)


async def main(settings: RelaySettings) -> None:
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Capture the loop before starting the Controller, which spawns its own thread+loop.
    loop = asyncio.get_running_loop()

    queue = MessageQueue(settings.QUEUE_DB_PATH, settings.QUEUE_RETAIN_MINUTES)
    await queue.setup()

    app, broadcaster = make_app(settings, queue)
    runner = web.AppRunner(app)
    await runner.setup()
    # web.AppRunner + web.TCPSite (not web.run_app) lets us compose aiohttp
    # with the aiosmtpd Controller in the same event loop.
    site = web.TCPSite(runner, settings.API_HOST, settings.API_PORT)
    await site.start()
    logger.info("api_started host=%s port=%d", settings.API_HOST, settings.API_PORT)

    tls_context = None
    if settings.TLS_CERT_PATH and settings.TLS_KEY_PATH:
        tls_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls_context.load_cert_chain(settings.TLS_CERT_PATH, settings.TLS_KEY_PATH)

    handler = RelayHandler(settings, queue, broadcaster, loop)
    controller = Controller(
        handler,
        hostname=settings.LISTEN_HOST,
        port=settings.LISTEN_PORT,
        tls_context=tls_context,
        server_kwargs={"max_content_size": settings.MAX_MESSAGE_SIZE},
    )
    controller.start()
    logger.info(
        "smtp_started host=%s port=%d domain=%s",
        settings.LISTEN_HOST,
        settings.LISTEN_PORT,
        settings.MY_DOMAIN,
    )

    sweep_task = asyncio.create_task(_sweep_loop(queue))

    stop: asyncio.Future[None] = loop.create_future()
    loop.add_signal_handler(signal.SIGTERM, stop.set_result, None)
    loop.add_signal_handler(signal.SIGINT, stop.set_result, None)

    try:
        await stop
    finally:
        logger.info("shutting_down")
        sweep_task.cancel()
        controller.stop()
        await runner.cleanup()
        await queue.close()
