"""FastAPI-Anwendung (Dashboard + REST-API)."""

from __future__ import annotations

import socket
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from diskatlas import __version__
from diskatlas.config import Config
from diskatlas.db import Database
from diskatlas.services import bays, relay, users
from diskatlas.web import accounts, api, auth, copyrequests, sharing, views


class _RevalidatingStaticFiles(StaticFiles):
    """Ohne dieses Header-Paar behält die eingebettete WebView alte JS/CSS-Dateien im Cache."""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"  # immer per ETag nachfragen
        return response


def create_app(
    config: Config, db: Database | None = None, bays_path: Path | None = None
) -> FastAPI:
    if db is None:
        db = Database(config.effective_database_url)
        db.upgrade()
    app = FastAPI(
        title="DiskAtlas",
        version=__version__,
        description="Inventar für Festplatten – Hardware, SMART, Belegung und Dateiindex.",
    )
    app.state.config = config
    app.state.db = db
    relay.configure(relay.RelayStore(relay.default_dir(config), config.server.relay_max_bytes))
    with db.session() as session:
        relay.recover(session)
    with db.session() as session:
        auth.check_exposure(config, users.has_users(session))
        users.bootstrap_master(session, config.server.password)
        app.state.auth_enabled = users.has_users(session)
    legacy = bays_path or bays.default_bays_path()
    if legacy.is_file():  # frühere lokale Schachtzuordnung einmalig übernehmen
        with db.session() as session:
            bays.import_legacy_file(session, legacy, config.agent.host_name or socket.gethostname())
    app.mount("/static", _RevalidatingStaticFiles(directory=Path(__file__).parent / "static"),
              name="static")
    app.state.login_throttle = auth.LoginThrottle()
    app.middleware("http")(auth.auth_middleware)
    app.include_router(api.router, prefix="/api/v1")
    app.include_router(views.router)
    app.include_router(accounts.router)
    app.include_router(sharing.router)
    app.include_router(copyrequests.router)
    return app
