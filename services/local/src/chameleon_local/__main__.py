import asyncio
import logging
import signal

from aiohttp import web

from .aliases import AliasDB
from .client import run_client
from .config import LocalSettings
from .web import make_web_app


async def _main() -> None:
    settings = LocalSettings()
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    alias_db = AliasDB(settings.ALIAS_DB_PATH)
    await alias_db.setup()

    app = make_web_app(settings, alias_db)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, settings.WEB_HOST, settings.WEB_PORT).start()
    logging.getLogger(__name__).info("web ui on %s:%d", settings.WEB_HOST, settings.WEB_PORT)

    loop = asyncio.get_running_loop()
    stop: asyncio.Future[None] = loop.create_future()
    loop.add_signal_handler(signal.SIGTERM, stop.set_result, None)
    loop.add_signal_handler(signal.SIGINT, stop.set_result, None)

    client_task = asyncio.create_task(run_client(settings, alias_db))
    try:
        await stop
    finally:
        client_task.cancel()
        try:
            await client_task
        except asyncio.CancelledError:
            pass
        await runner.cleanup()
        await alias_db.close()


def main() -> None:
    asyncio.run(_main())


main()
