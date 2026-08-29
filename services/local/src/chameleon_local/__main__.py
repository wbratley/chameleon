import asyncio
import base64
import logging
import signal
import sys
from pathlib import Path

from aiohttp import web
from nacl.public import PrivateKey

from .aliases import AliasDB
from .client import run_client
from .config import LocalSettings
from .web import make_web_app


def _load_private_key() -> PrivateKey:
    key_path = Path("secrets/private_key")
    if not key_path.exists():
        sys.exit(
            f"error: {key_path} not found — run `python -m chameleon_local keygen` first "
            "(in the repo checkout on the home server, where the keypair was generated)"
        )
    return PrivateKey(base64.b64decode(key_path.read_text().strip()))


def _keygen() -> None:
    key = PrivateKey.generate()
    priv_b64 = base64.b64encode(bytes(key)).decode()
    pub_b64 = base64.b64encode(bytes(key.public_key)).decode()
    key_path = Path("secrets/private_key")
    key_path.parent.mkdir(exist_ok=True)
    key_path.write_text(priv_b64)
    key_path.chmod(0o600)
    print(f"Private key written to: {key_path}  (keep this off the relay)")
    print(f"CHAMELEON_PUBLIC_KEY={pub_b64}  <- put this in services/relay/.env")


def _publickey() -> None:
    """Re-derive the public key from secrets/private_key.

    keygen prints CHAMELEON_PUBLIC_KEY once and stores only the private key;
    the public key is always derivable from it, so a lost printout is
    recoverable without regenerating the pair (which would orphan mail sealed
    to the old key).
    """
    pub_b64 = base64.b64encode(bytes(_load_private_key().public_key)).decode()
    print(f"CHAMELEON_PUBLIC_KEY={pub_b64}  <- put this in services/relay/.env")


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
    log = logging.getLogger(__name__)
    log.info("web ui on %s:%d", settings.WEB_HOST, settings.WEB_PORT)
    if settings.WEB_PASSWORD:
        log.info("web ui auth=password")
    else:
        log.warning(
            "web ui auth=DISABLED: CHAMELEON_WEB_PASSWORD is not set; "
            "anyone who can reach the UI can view and burn aliases"
        )

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
    if len(sys.argv) > 1 and sys.argv[1] == "keygen":
        _keygen()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "publickey":
        _publickey()
        return
    asyncio.run(_main())


if __name__ == "__main__":
    main()
