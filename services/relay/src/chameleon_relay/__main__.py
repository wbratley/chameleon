import asyncio

from .config import RelaySettings
from .server import main

asyncio.run(main(RelaySettings()))
