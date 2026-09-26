"""Konfiguration: Defaults < TOML-Datei < Umgebungsvariablen."""

from __future__ import annotations

import logging
import os
import sys
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

APP_NAME = "diskatlas"
log = logging.getLogger(__name__)

# Verzeichnisnamen (fnmatch, case-insensitive), die beim Dateiindex übersprungen werden.
DEFAULT_EXCLUDE_DIRS = [
    "$RECYCLE.BIN",
    "System Volume Information",
    ".Trash-*",
    ".Trashes",
    "lost+found",
    ".Spotlight-V100",
    ".fseventsd",
    ".DocumentRevisions-V100",
]


def default_config_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


def default_config_path() -> Path:
    return default_config_dir() / "config.toml"


@dataclass
class AgentConfig:
    poll_interval: float = 5.0
    smart_interval_minutes: float = 60.0
    rescan_interval_hours: float = 24.0
    # Neuindex, sobald sich die Belegung eines Volumes geändert hat und danach so lange stabil
    # blieb (Verschieben/Kopieren/Löschen von Daten). 0 = aus.
    change_settle_seconds: float = 30.0
    # Ein Neuindex wegen Datenänderung braucht mindestens so viel Änderung an der Belegung
    # (Byte) und mindestens so viele Minuten Abstand zum letzten Komplett-Scan. Systemvolumes
    # lösen nie aus; dort gilt nur `rescan_interval_hours`.
    change_min_bytes: int = 50_000_000
    change_min_interval_minutes: float = 10.0
    index_files: bool = True
    index_system_volumes: bool = False
    # Linux: angeschlossene, nicht eingehängte Dateisysteme selbst einhängen (udisks2, wie der
    # Dateimanager), damit Belegung und Dateien erfasst werden können.
    auto_mount: bool = True
    exclude_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_DIRS))
    smartctl_path: str = "smartctl"
    smart_use_sudo: bool = True
    # Leer = direkt in die Datenbank schreiben; sonst URL eines DiskAtlas-Servers.
    server_url: str = ""
    api_token: str = ""
    host_name: str = ""
    # Tray-Programm: Farbe des Symbols im Infobereich – "auto" (Windows: nach Taskleiste),
    # "light" (für dunkle Leisten) oder "dark" (für helle Leisten).
    tray_icon_color: str = "auto"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    # Startpasswort des Benutzers „Master“ (nur beim allerersten Start ausgewertet). Pflicht,
    # sobald der Server nicht nur lokal lauscht.
    password: str = ""
    # Nur für Tests/Sonderfälle: Betrieb im Netz ohne Passwort erlauben (nicht empfohlen).
    allow_insecure: bool = False


@dataclass
class Config:
    database_url: str = ""
    agent: AgentConfig = field(default_factory=AgentConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    config_path: Path | None = None

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(default_data_dir() / 'diskatlas.db').as_posix()}"


# Umgebungsvariable -> (Abschnitt, Feld). Abschnitt None = oberste Ebene.
ENV_MAP: dict[str, list[tuple[str | None, str]]] = {
    "DISKATLAS_DATABASE_URL": [(None, "database_url")],
    "DISKATLAS_SERVER_URL": [("agent", "server_url")],
    "DISKATLAS_API_TOKEN": [("agent", "api_token")],
    "DISKATLAS_PASSWORD": [("server", "password")],
    "DISKATLAS_ALLOW_INSECURE": [("server", "allow_insecure")],
    "DISKATLAS_HOST": [("server", "host")],
    "DISKATLAS_PORT": [("server", "port")],
    "DISKATLAS_HOST_NAME": [("agent", "host_name")],
    "DISKATLAS_SMARTCTL": [("agent", "smartctl_path")],
}


def load_config(path: str | Path | None = None) -> Config:
    """Lädt die Konfiguration. Ein explizit angegebener Pfad muss existieren."""
    cfg = Config()
    explicit = path is not None or "DISKATLAS_CONFIG" in os.environ
    cfg_path = Path(path or os.environ.get("DISKATLAS_CONFIG") or default_config_path())
    if cfg_path.is_file():
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        _apply_dict(cfg, data, source=str(cfg_path))
        cfg.config_path = cfg_path
    elif explicit:
        raise FileNotFoundError(f"Konfigurationsdatei nicht gefunden: {cfg_path}")
    _apply_env(cfg)
    return cfg


def _apply_dict(cfg: Config, data: dict[str, Any], source: str) -> None:
    for key, value in data.items():
        if key in ("agent", "server"):
            if not isinstance(value, dict):
                raise ValueError(f"{source}: [{key}] muss ein Abschnitt sein")
            section = getattr(cfg, key)
            valid = {f.name for f in fields(section)}
            for sub_key, sub_value in value.items():
                if (key, sub_key) == ("server", "api_token"):
                    log.warning("%s: [server] api_token gibt es nicht mehr und wird ignoriert; "
                                "Agenten nutzen das Token ihres Clients (Konto-Seite).", source)
                    continue
                if sub_key not in valid:
                    raise ValueError(f"{source}: unbekannte Option [{key}].{sub_key}")
                setattr(section, sub_key, sub_value)
        elif key == "database_url":
            cfg.database_url = str(value)
        else:
            raise ValueError(f"{source}: unbekannte Option {key}")


def _apply_env(cfg: Config) -> None:
    for env_name, targets in ENV_MAP.items():
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        for section_name, attr in targets:
            target = cfg if section_name is None else getattr(cfg, section_name)
            current = getattr(target, attr)
            setattr(target, attr, _coerce(raw, current))


def _coerce(raw: str, current: Any) -> Any:
    if isinstance(current, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on", "ja")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return raw
