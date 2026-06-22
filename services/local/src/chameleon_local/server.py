import asyncio
import logging
import signal

from aiosmtpd.controller import Controller

from .config import LocalSettings
from .handler import LocalDeliveryHandler


async def main(settings: LocalSettings) -> None:
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    handler = LocalDeliveryHandler(settings)
    controller = Controller(
        handler,
        hostname=settings.LISTEN_HOST,
        port=settings.LISTEN_PORT,
    )
    controller.start()

    loop = asyncio.get_running_loop()
    stop: asyncio.Future[None] = loop.create_future()
    loop.add_signal_handler(signal.SIGTERM, stop.set_result, None)
    loop.add_signal_handler(signal.SIGINT, stop.set_result, None)

    logging.getLogger(__name__).info(
        "listening host=%s port=%d maildir=%s",
        settings.LISTEN_HOST,
        settings.LISTEN_PORT,
        settings.MAILDIR_PATH,
    )

    await stop
    controller.stop()
