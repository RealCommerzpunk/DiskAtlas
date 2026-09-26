"""Bausteine, die sich CLI, Desktop-GUI und Tray teilen: Agent-Aufbau, lokaler Server,
URL-Maskierung."""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass

from diskatlas.config import Config

log = logging.getLogger("diskatlas")

SERVER_STARTUP_TIMEOUT = 15.0


@dataclass
class LocalServer:
    """Weboberfläche, die im Hintergrund-Thread läuft (lokaler Betrieb ohne Docker)."""

    server: object
    thread: threading.Thread
    url: str

    def stop(self, timeout: float = 15.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=timeout)


def free_port(host: str, preferred: int) -> int:
    """Bevorzugten Port versuchen, sonst einen freien vergeben."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, preferred))
        except OSError:
            s.bind((host, 0))
        return s.getsockname()[1]


def start_local_server(config: Config, timeout: float = SERVER_STARTUP_TIMEOUT) -> LocalServer:
    """Startet die Weboberfläche samt lokaler Datenbank und wartet, bis sie antwortet."""
    import uvicorn

    from diskatlas.web.app import create_app

    port = free_port(config.server.host, config.server.port)
    if port != config.server.port:
        log.warning("Port %d belegt, verwende %d", config.server.port, port)
    app = create_app(config)  # legt/aktualisiert die lokale Datenbank an
    server = uvicorn.Server(
        uvicorn.Config(
            app, host=config.server.host, port=port, log_level="warning",
            ws="none",  # WebSockets unnötig; vermeidet Konflikte mit System-websockets
        )
    )
    thread = threading.Thread(target=server.run, name="diskatlas-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    while not server.started and time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("Die Weboberfläche konnte nicht gestartet werden (siehe Log).")
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        raise RuntimeError("Die Weboberfläche ist innerhalb der Frist nicht gestartet.")
    host = "127.0.0.1" if config.server.host in ("0.0.0.0", "::") else config.server.host
    return LocalServer(server=server, thread=thread, url=f"http://{host}:{port}")


def make_agent(config: Config):
    """Baut den Agenten inkl. Sink auf. Bei lokalem Betrieb wird die Datenbank aktualisiert."""
    from diskatlas.agent.agent import Agent
    from diskatlas.agent.sinks import DatabaseSink, HttpSink

    if config.agent.server_url:
        log.info("Sende Ergebnisse an %s", config.agent.server_url)
        sink = HttpSink(config.agent.server_url, config.agent.api_token)
    else:
        from diskatlas.db import Database

        db = Database(config.effective_database_url)
        db.upgrade()
        log.info("Datenbank: %s", redact_url(config.effective_database_url))
        sink = DatabaseSink(db)
    return Agent(config.agent, sink)


def redact_url(url: str) -> str:
    """Datenbank-URL ohne Passwort, z. B. für Logs und Anzeige."""
    from sqlalchemy.engine import make_url

    return make_url(url).render_as_string(hide_password=True)
