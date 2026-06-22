import asyncio

from .config import LocalSettings
from .server import main

asyncio.run(main(LocalSettings()))
