import logging
import pathlib

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


@web.middleware
async def reject_cross_origin(
    request: web.Request, handler: Handler
) -> web.StreamResponse:
    """Block cross-site POSTs (CSRF) against the alias UI.

    The UI binds to 127.0.0.1:8080, but that does not stop a malicious page
    in the user's own browser: CORS blocks reading cross-origin responses,
    yet a form-encoded POST is a "simple request" whose side effect still
    happens — any visited webpage could burn every alias by iterating ids.
    Browsers attach Origin to all unsafe-method requests, so comparing it
    with Host stops that without any tokens or session state (issue #5).
    """
    if request.method in _UNSAFE_METHODS:
        origin = request.headers.get("Origin")
        if origin is None:
            logger.warning("csrf_rejected reason=missing_origin path=%s", request.path)
            raise web.HTTPForbidden(reason="missing Origin header")
        if _origin_host(origin) != request.host.lower():
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
    app = web.Application(middlewares=[reject_cross_origin])
    app["settings"] = settings
    app["alias_db"] = alias_db
    app["jinja_env"] = _jinja_env()
    app.router.add_get("/", index)
    app.router.add_post("/aliases", create_alias)
    app.router.add_post("/aliases/{id}/burn", burn_alias)
    return app
