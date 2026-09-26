"""FastAPI-Anwendung (Dashboard + REST-API)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from diskatlas import __version__
from diskatlas.config import Config
from diskatlas.db import Database
from diskatlas.services import bays
from diskatlas.web import api, views


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
    app.state.bays_path = bays_path or bays.default_bays_path()
    app.mount("/static", _RevalidatingStaticFiles(directory=Path(__file__).parent / "static"),
              name="static")
    app.include_router(api.router, prefix="/api/v1")
    app.include_router(views.router)
    return app
