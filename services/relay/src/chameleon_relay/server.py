import asyncio
import logging
import signal
import ssl

from aiosmtpd.controller import Controller

from .config import RelaySettings
from .handler import RelayHandler


async def main(settings: RelaySettings) -> None:
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    tls_context = None
    if settings.TLS_CERT_PATH and settings.TLS_KEY_PATH:
        tls_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls_context.load_cert_chain(settings.TLS_CERT_PATH, settings.TLS_KEY_PATH)

    handler = RelayHandler(settings)
    controller = Controller(
        handler,
        hostname=settings.LISTEN_HOST,
        port=settings.LISTEN_PORT,
        tls_context=tls_context,
        server_kwargs={"max_content_size": settings.MAX_MESSAGE_SIZE},
    )
    controller.start()

    loop = asyncio.get_running_loop()
    stop: asyncio.Future[None] = loop.create_future()
    loop.add_signal_handler(signal.SIGTERM, stop.set_result, None)
    loop.add_signal_handler(signal.SIGINT, stop.set_result, None)

    logging.getLogger(__name__).info(
        "listening host=%s port=%d domain=%s -> %s:%d",
        settings.LISTEN_HOST,
        settings.LISTEN_PORT,
        settings.MY_DOMAIN,
        settings.LOCAL_SMTP_HOST,
        settings.LOCAL_SMTP_PORT,
    )

    await stop
    controller.stop()
