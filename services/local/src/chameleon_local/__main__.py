import asyncio
import logging

from .config import LocalSettings
from .client import run_client


def main() -> None:
    settings = LocalSettings()
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(run_client(settings))


main()
