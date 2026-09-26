"""Eigenständiges Programmfenster (kein Terminal, kein Browser nötig).

Verwendet dieselbe Web-Oberfläche wie `diskatlas run`/`serve`, zeigt sie aber in einem
normalen Fenster an (pywebview). Ist ein Server konfiguriert (`[agent] server_url`), öffnet
sich stattdessen direkt dessen Dashboard – lokal läuft dann nur der Agent.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import threading
from dataclasses import dataclass

from diskatlas import __version__
from diskatlas.config import Config, default_config_path, default_data_dir, load_config
from diskatlas.runtime import make_agent, redact_url, start_local_server

log = logging.getLogger("diskatlas.gui")

WINDOW_TITLE = "DiskAtlas"


@dataclass
class Session:
    """Laufende Komponenten (Server/Agent), damit sie beim Schließen des Fensters sauber
    heruntergefahren werden können."""

    url: str
    agent: object | None = None
    agent_thread: threading.Thread | None = None
    server: object | None = None
    server_thread: threading.Thread | None = None

    def shutdown(self) -> None:
        log.info("Fenster geschlossen – beende …")
        if self.server is not None:
            self.server.should_exit = True
        if self.agent is not None:
            self.agent.stop()
        if self.server_thread is not None:
            self.server_thread.join(timeout=15)
        if self.agent_thread is not None:
            self.agent_thread.join(timeout=15)
        if self.agent is not None:
            self.agent.sink.close()


def start_session(config: Config) -> Session:
    """Baut Agent (und ggf. lokalen Server) auf und startet sie im Hintergrund."""
    if config.agent.server_url:
        # Zentraler Server ist konfiguriert: dessen Dashboard anzeigen, hier nur der Agent.
        agent = make_agent(config)
        thread = threading.Thread(target=agent.watch, name="diskatlas-agent", daemon=True)
        thread.start()
        return Session(url=config.agent.server_url, agent=agent, agent_thread=thread)

    local = start_local_server(config)
    agent = make_agent(config)
    agent_thread = threading.Thread(target=agent.watch, name="diskatlas-agent", daemon=True)
    agent_thread.start()
    return Session(
        url=local.url, agent=agent, agent_thread=agent_thread,
        server=local.server, server_thread=local.thread,
    )


def run_window(config: Config) -> int:
    import webview

    session = start_session(config)
    log.info("Öffne Fenster: %s", session.url)
    icon = _icon_path()
    window = webview.create_window(
        WINDOW_TITLE, session.url, width=1300, height=860, min_size=(900, 600),
    )
    window.events.closed += session.shutdown
    start_kwargs = {"icon": str(icon)} if icon else {}
    # Sitzung dauerhaft speichern, damit die Anmeldung am Server nur einmal nötig ist.
    storage = default_data_dir() / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    webview.start(private_mode=False, storage_path=str(storage), **start_kwargs)
    return 0


def _icon_path():
    from pathlib import Path

    icon = Path(__file__).parent / "web" / "static" / "icon_256.png"
    return icon if icon.is_file() else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="diskatlas-gui", description="DiskAtlas als eigenständiges Programmfenster."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-c", "--config", help=f"Konfigurationsdatei (Standard: {default_config_path()})"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="ausführliche Ausgabe")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("alembic").setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        log.error("%s", exc)
        return 2
    try:
        import webview  # noqa: F401
    except ImportError:
        log.error(
            "pywebview ist nicht installiert. Installation: pip install -e \".[gui]\" "
            "(unter Linux zusätzlich python3-gi und gir1.2-webkit2-4.1, meist schon vorhanden)."
        )
        return 2
    with contextlib.suppress(KeyboardInterrupt):
        try:
            return run_window(config)
        except Exception:
            log.exception("Datenbank/Server nicht erreichbar (%s)",
                          redact_url(config.effective_database_url))
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
