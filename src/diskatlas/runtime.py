"""Bausteine, die sich CLI und Desktop-GUI teilen: Agent-Aufbau, URL-Maskierung."""

from __future__ import annotations

import logging

from diskatlas.config import Config

log = logging.getLogger("diskatlas")


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
