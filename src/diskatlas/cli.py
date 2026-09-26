"""Kommandozeile: `diskatlas <befehl>` – siehe `diskatlas --help`."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import signal
import sys
import threading
from pathlib import Path

from diskatlas import __version__
from diskatlas.config import Config, default_config_path, load_config
from diskatlas.runtime import make_agent, redact_url

log = logging.getLogger("diskatlas")

EXAMPLE_CONFIG = Path(__file__).parent / "config.example.toml"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("alembic").setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        log.error("%s", exc)
        return 2
    from sqlalchemy.exc import OperationalError

    try:
        return args.func(config, args) or 0
    except OperationalError as exc:
        log.error("Datenbank nicht erreichbar (%s): %s",
                  redact_url(config.effective_database_url), exc.orig)
        return 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="diskatlas",
        description="Inventar für Festplatten: Hardware, SMART, Belegung und Dateiindex.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-c", "--config", help=f"Konfigurationsdatei (Standard: {default_config_path()})"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="ausführliche Ausgabe")
    sub = parser.add_subparsers(title="Befehle", metavar="<befehl>")

    p = sub.add_parser("run", help="Dashboard starten und Festplatten überwachen (Standardbetrieb)")
    _server_args(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("serve", help="nur Dashboard/API starten (z. B. im Docker-Container)")
    _server_args(p)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser(
        "agent", help="Agent für den Betrieb mit zentralem Server (Docker/Unraid): überwacht "
        "Festplatten, sendet an [agent] server_url und führt Aufträge des Servers aus"
    )
    p.set_defaults(func=cmd_agent)
    p = sub.add_parser("watch", help="nur Agent: Festplatten überwachen und bei Hot-Plug scannen")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("scan", help="alle angeschlossenen Festplatten einmalig scannen")
    p.add_argument("--no-files", action="store_true", help="keinen Dateiindex erstellen")
    p.add_argument("--disk", action="append", metavar="GERÄT|SERIENNR|ID",
                   help="nur diese Festplatte(n) scannen, z. B. /dev/sdb")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("disks", help="erkannte Festplatten anzeigen (ohne zu speichern)")
    p.add_argument("--smart", action="store_true", help="SMART-Werte mit auslesen")
    p.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    p.set_defaults(func=cmd_disks)

    p = sub.add_parser("config", help="Konfiguration anzeigen oder Beispieldatei anlegen")
    p.add_argument("--init", action="store_true", help="Beispielkonfiguration anlegen")
    p.set_defaults(func=cmd_config)

    db = sub.add_parser("db", help="Datenbankverwaltung")
    db_sub = db.add_subparsers(metavar="<aktion>")
    p = db_sub.add_parser("upgrade", help="Schema auf den neuesten Stand bringen")
    p.set_defaults(func=cmd_db_upgrade)
    p = db_sub.add_parser("copy", help="Datenbestand in eine andere (leere) Datenbank kopieren")
    p.add_argument("--to", required=True, metavar="URL",
                   help="Ziel, z. B. postgresql+psycopg://user:pw@unraid:5432/diskatlas")
    p.add_argument("--from", dest="source", metavar="URL",
                   help="Quelle (Standard: konfigurierte DB)")
    p.set_defaults(func=cmd_db_copy)
    return parser


def _server_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", help="Adresse, an die der Server bindet (Standard 127.0.0.1)")
    p.add_argument("--port", type=int, help="Port (Standard 8765)")


# ------------------------------------------------------------------ Befehle


def _install_stop_handler(agent) -> None:
    def handler(signum, frame):
        log.info("Beende …")
        agent.stop()

    signal.signal(signal.SIGINT, handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handler)


def cmd_scan(config: Config, args) -> int:
    agent = make_agent(config)
    _install_stop_handler(agent)
    try:
        count = agent.scan_all(
            index_files=False if args.no_files else None,
            only=set(args.disk) if args.disk else None,
        )
    finally:
        agent.sink.close()
    log.info("%d Festplatte(n) gescannt", count)
    return 0


def cmd_agent(config: Config, args) -> int:
    if not config.agent.server_url:
        log.error(
            "Kein Server konfiguriert. In der Konfiguration unter [agent] server_url und "
            "api_token eintragen (siehe config.example.toml) – oder `diskatlas run` für den "
            "Betrieb ohne Server nutzen."
        )
        return 2
    return cmd_watch(config, args)


def cmd_watch(config: Config, args) -> int:
    agent = make_agent(config)
    _install_stop_handler(agent)
    try:
        agent.watch()
    finally:
        agent.sink.close()
    return 0


def _apply_server_args(config: Config, args) -> None:
    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port


def cmd_serve(config: Config, args) -> int:
    import uvicorn

    from diskatlas.web.app import create_app

    _apply_server_args(config, args)
    app = create_app(config)
    log.info("Dashboard: http://%s:%d", config.server.host, config.server.port)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host=config.server.host, port=config.server.port, log_level="warning",
            ws="none",  # WebSockets unnötig; vermeidet Konflikte mit System-websockets
        )
    )
    # Neuere uvicorn-Versionen lösen das Signal nach dem sauberen Herunterfahren erneut aus.
    with contextlib.suppress(KeyboardInterrupt):
        server.run()
    log.info("Server beendet")
    return 0


def cmd_run(config: Config, args) -> int:
    agent = make_agent(config)
    thread = threading.Thread(target=agent.watch, name="diskatlas-agent", daemon=True)
    thread.start()
    try:
        cmd_serve(config, args)  # blockiert bis Strg+C
    finally:
        agent.stop()
        thread.join(timeout=15)
        agent.sink.close()
    return 0


def cmd_disks(config: Config, args) -> int:
    from diskatlas.probe import list_disks
    from diskatlas.probe.smart import read_smart
    from diskatlas.web.formatting import filesize

    disks = list_disks()
    if args.smart:
        for disk in disks:
            disk.smart = read_smart(
                disk.smart_device or disk.device, config.agent.smartctl_path,
                config.agent.smart_use_sudo,
            )
    if args.json:
        payload = [d.model_dump(mode="json", exclude={"smart": {"raw"}}) for d in disks]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    for disk in disks:
        print(f"{disk.device}  {disk.model or '?'}  SN {disk.serial or '?'}  "
              f"{filesize(disk.size_bytes)}  {disk.transport or ''}  [{disk.key}]")
        if disk.smart:
            s = disk.smart
            extra = f"  ({s.error})" if s.error else ""
            print(f"    SMART: {s.health}  Temp {s.temperature_c} °C  "
                  f"{s.power_on_hours} h{extra}")
        for v in disk.volumes:
            print(f"    - {v.label or '(ohne Bezeichnung)'}  {v.fs_type or '?'}  "
                  f"{filesize(v.size_bytes)}  frei {filesize(v.free_bytes)}  "
                  f"{v.mountpoint or 'nicht eingehängt'}{'  [System]' if v.is_system else ''}")
    return 0


def cmd_config(config: Config, args) -> int:
    if args.init:
        target = Path(args.config) if args.config else default_config_path()
        if target.exists():
            log.error("%s existiert bereits", target)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Beispielkonfiguration angelegt: {target}")
        return 0
    print(f"Konfigurationsdatei: {config.config_path or '(keine – Standardwerte)'}")
    print(f"Datenbank:          {redact_url(config.effective_database_url)}")
    print(f"Server:             http://{config.server.host}:{config.server.port}")
    agent = config.agent
    print(f"Agent-Ziel:         {agent.server_url or 'lokale Datenbank'}")
    print(f"Abfrageintervall:   {agent.poll_interval} s,"
          f" SMART alle {agent.smart_interval_minutes} min,"
          f" Neuindex alle {agent.rescan_interval_hours} h")
    print(f"Dateiindex:         {'an' if agent.index_files else 'aus'}"
          f" (Systemvolumes: {'ja' if agent.index_system_volumes else 'nein'})")
    return 0


def cmd_db_upgrade(config: Config, args) -> int:
    from diskatlas.db import Database

    Database(config.effective_database_url).upgrade()
    log.info("Schema aktuell: %s", redact_url(config.effective_database_url))
    return 0


def cmd_db_copy(config: Config, args) -> int:
    from diskatlas.tools.dbcopy import copy_database

    source = args.source or config.effective_database_url
    try:
        counts = copy_database(source, args.to)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    log.info("Fertig: %s", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
