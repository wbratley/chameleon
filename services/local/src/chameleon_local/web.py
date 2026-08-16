import base64
import logging
import pathlib
import secrets

from aiohttp import web
from aiohttp.typedefs import Handler
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .aliases import AliasDB
from .config import LocalSettings

logger = logging.getLogger(__name__)

_TEMPLATES = pathlib.Path(__file__).parent / "templates"

# Methods that can mutate state. Browsers attach an Origin header to every
# request using these methods (same-origin included), so a missing Origin means
# a non-browser client — fine to reject for an htmx-only UI.
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _origin_host(origin: str) -> str:
    """Host (with port, if any) of an Origin header value, lowercased.

    "null" (sandboxed iframes, some privacy tools) maps to "null", which
    never matches a real Host header and is therefore rejected.
    """
    return origin.split("://", 1)[-1].lower()


def _check_basic_auth(header: str, password: str) -> bool:
    """Validate an ``Authorization: Basic ...`` header (any username)."""
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    _, _, supplied = decoded.partition(":")
    return secrets.compare_digest(supplied.encode("utf-8"), password.encode("utf-8"))


@web.middleware
async def auth_and_csrf(
    request: web.Request, handler: Handler
) -> web.StreamResponse:
    """HTTP Basic auth for LAN access + CSRF (Origin) check on mutations.

    Two threats, two mechanisms (issue #5):

    - The UI is reachable from the LAN, so an optional shared password
      (``CHAMELEON_WEB_PASSWORD``) gates every request. Browsers prompt via
      the WWW-Authenticate challenge; a companion app can send the same
      ``Authorization`` header. Constant-time comparison avoids leaking it.
    - A malicious webpage can still trigger the user's browser to POST here
      (a form post is a CORS "simple request" — the side effect happens even
      though the response can't be read), *including* requests where the
      browser reuses cached Basic credentials. Browsers always attach an
      Origin header to such requests, so a mismatching (or absent, when not
      credentialed) Origin is rejected. Non-browser clients send no Origin
      and are accepted with valid credentials only.
    """
    settings: LocalSettings = request.app["settings"]
    password = settings.WEB_PASSWORD
    authorized = bool(password) and _check_basic_auth(
        request.headers.get("Authorization", ""), password
    )
    if password and not authorized:
        raise web.HTTPUnauthorized(
            headers={"WWW-Authenticate": 'Basic realm="chameleon"'}
        )

    if request.method in _UNSAFE_METHODS:
        origin = request.headers.get("Origin")
        if origin is None:
            # Browsers always attach Origin to unsafe-method requests, so a
            # missing Origin means a non-browser client: allowed only with
            # valid credentials (companion app / scripting), else rejected.
            if not authorized:
                logger.warning(
                    "csrf_rejected reason=missing_origin path=%s", request.path
                )
                raise web.HTTPForbidden(reason="missing Origin header")
        elif _origin_host(origin) != request.host.lower():
            logger.warning(
                "csrf_rejected reason=cross_origin path=%s origin=%s",
                request.path,
                origin,
            )
            raise web.HTTPForbidden(reason="cross-origin request rejected")
    return await handler(request)


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )


async def index(request: web.Request) -> web.Response:
    alias_db: AliasDB = request.app["alias_db"]
    env: Environment = request.app["jinja_env"]
    aliases = await alias_db.all()
    html = env.get_template("index.html").render(aliases=aliases)
    return web.Response(text=html, content_type="text/html")


async def create_alias(request: web.Request) -> web.Response:
    alias_db: AliasDB = request.app["alias_db"]
    settings: LocalSettings = request.app["settings"]
    env: Environment = request.app["jinja_env"]
    data = await request.post()
    service = (data.get("service") or "").strip()
    if not service:
        raise web.HTTPBadRequest(reason="service is required")
    alias = await alias_db.create(service, settings.MY_DOMAIN)
    html = env.get_template("_alias_row.html").render(alias=alias)
    return web.Response(text=html, content_type="text/html")


async def burn_alias(request: web.Request) -> web.Response:
    alias_db: AliasDB = request.app["alias_db"]
    env: Environment = request.app["jinja_env"]
    try:
        alias_id = int(request.match_info["id"])
    except ValueError:
        raise web.HTTPNotFound()
    alias = await alias_db.burn(alias_id)
    if alias is None:
        raise web.HTTPNotFound()
    html = env.get_template("_alias_row.html").render(alias=alias)
    return web.Response(text=html, content_type="text/html")


def make_web_app(settings: LocalSettings, alias_db: AliasDB) -> web.Application:
    app = web.Application(middlewares=[auth_and_csrf])
    app["settings"] = settings
    app["alias_db"] = alias_db
    app["jinja_env"] = _jinja_env()
    app.router.add_get("/", index)
    app.router.add_post("/aliases", create_alias)
    app.router.add_post("/aliases/{id}/burn", burn_alias)
    return app
