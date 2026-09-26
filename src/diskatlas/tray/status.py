"""Statusdatei: der Tray-Prozess schreibt sie, das Einstellungsfenster liest sie."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from diskatlas.config import default_data_dir

STALE_SECONDS = 20.0

STATE_LABELS = {
    "starting": "Verbindung wird aufgebaut …",
    "online": "Verbunden",
    "offline": "Server nicht erreichbar",
    "auth_error": "API-Token abgelehnt",
    "unconfigured": "Nicht eingerichtet",
    "config_error": "Konfigurationsfehler",
    "stopped": "Agent läuft nicht",
    "error": "Fehler",
}


@dataclass
class TrayStatus:
    state: str = "starting"
    detail: str = ""
    server_url: str = ""
    host: str = ""
    disks: int = 0
    last_ok: float | None = None
    updated_at: float = 0.0
    config_path: str = ""
    # "server": sendet an einen DiskAtlas-Server; "local": Oberfläche und Datenbank laufen lokal
    # (server_url ist dann die Adresse der lokalen Oberfläche).
    mode: str = "server"

    @property
    def label(self) -> str:
        if self.mode == "local" and self.state == "online":
            return "Läuft lokal"
        return STATE_LABELS.get(self.state, self.state)


def status_path() -> Path:
    return default_data_dir() / "agent-status.json"


def strip_credentials(url: str) -> str:
    """Server-URL ohne Benutzer/Passwort (steht sonst im Klartext in der Statusdatei)."""
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc=parts.netloc.rpartition("@")[2]))


def write_status(status: TrayStatus, path: Path | None = None) -> None:
    path = Path(path or status_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    status.updated_at = time.time()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(status)), encoding="utf-8")
    os.replace(tmp, path)


def read_status(path: Path | None = None, now: float | None = None) -> TrayStatus:
    """Liest den Status; fehlt die Datei oder ist sie veraltet, läuft der Agent nicht."""
    stopped = TrayStatus(state="stopped")
    try:
        data = json.loads(Path(path or status_path()).read_text(encoding="utf-8"))
        known = {f.name for f in fields(TrayStatus)}
        status = TrayStatus(**{k: v for k, v in data.items() if k in known})
    except (OSError, ValueError, TypeError):
        return stopped
    if (now if now is not None else time.time()) - status.updated_at > STALE_SECONDS:
        stopped.server_url, stopped.host = status.server_url, status.host
        stopped.config_path, stopped.mode = status.config_path, status.mode
        return stopped
    return status


def age_text(timestamp: float | None, now: float | None = None) -> str:
    if timestamp is None:
        return "noch nie"
    seconds = max(0, int((now if now is not None else time.time()) - timestamp))
    if seconds < 60:
        return f"vor {seconds} s"
    if seconds < 3600:
        return f"vor {seconds // 60} min"
    return f"vor {seconds // 3600} h"
