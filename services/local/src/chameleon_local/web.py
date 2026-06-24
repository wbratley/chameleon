import pathlib

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .aliases import AliasDB
from .config import LocalSettings

_TEMPLATES = pathlib.Path(__file__).parent / "templates"


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
    app = web.Application()
    app["settings"] = settings
    app["alias_db"] = alias_db
    app["jinja_env"] = _jinja_env()
    app.router.add_get("/", index)
    app.router.add_post("/aliases", create_alias)
    app.router.add_post("/aliases/{id}/burn", burn_alias)
    return app
