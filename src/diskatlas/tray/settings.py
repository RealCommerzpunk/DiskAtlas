"""Einstellungen des Agenten: Datei lesen/prüfen/schreiben und Verbindung testen."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any

import httpx

from diskatlas.config import AgentConfig

# Im Fenster bearbeitbare [agent]-Optionen mit Typ und Bezeichnung für Fehlermeldungen.
FIELDS: dict[str, tuple[type, str]] = {
    "server_url": (str, "Server-Adresse"),
    "api_token": (str, "Client-Token"),
    "host_name": (str, "Rechnername"),
    "smartctl_path": (str, "Pfad zu smartctl"),
    "poll_interval": (float, "Abfrageintervall"),
    "smart_interval_minutes": (float, "SMART-Intervall"),
    "rescan_interval_hours": (float, "Neuindex-Intervall"),
    "index_files": (bool, "Dateien indizieren"),
    "index_system_volumes": (bool, "Systemvolumes indizieren"),
    "auto_mount": (bool, "Automatisch einhängen"),
    "smart_use_sudo": (bool, "SMART mit sudo lesen"),
    "tray_icon_color": (str, "Farbe des Symbols"),
}
CHOICES = {"tray_icon_color": ("auto", "light", "dark")}

TEST_HOST = "[verbindungstest]"
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def read_raw(path: Path) -> dict[str, Any]:
    """Rohinhalt der Konfigurationsdatei ({} wenn sie noch nicht existiert)."""
    try:
        return tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Die Konfigurationsdatei ist nicht lesbar: {exc}") from exc


def default_values() -> dict[str, Any]:
    defaults = AgentConfig()
    return {name: getattr(defaults, name) for name in FIELDS}


def load_values(path: Path) -> dict[str, Any]:
    """Werte für das Formular: Datei über Standardwerten (Umgebungsvariablen bleiben außen vor)."""
    values = default_values()
    stored = read_raw(path).get("agent", {})
    values.update({k: v for k, v in stored.items() if k in FIELDS})
    return values


def validate(values: dict[str, Any]) -> dict[str, Any]:
    """Prüft und bereinigt Formularwerte; wirft ValueError mit verständlicher Meldung."""
    clean: dict[str, Any] = {}
    for name, (kind, label) in FIELDS.items():
        if name not in values:
            continue
        value = values[name]
        if kind is bool:
            if not isinstance(value, bool):
                raise ValueError(f"{label}: ungültiger Wert")
        elif kind is float:
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{label}: bitte eine Zahl eingeben") from None
            if value < (1.0 if name == "poll_interval" else 0.1):
                raise ValueError(f"{label}: Wert ist zu klein")
        else:
            value = str(value).strip()
            if name in CHOICES and value not in CHOICES[name]:
                raise ValueError(f"{label}: ungültiger Wert")
        clean[name] = value
    url = clean.get("server_url", "")
    if url:
        if not re.match(r"https?://[^\s/]+", url):
            raise ValueError("Die Server-Adresse muss mit http:// oder https:// beginnen.")
        clean["server_url"] = url.rstrip("/")
    return clean


def _key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else json.dumps(key)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    raise ValueError(f"Nicht unterstützter Wert in der Konfiguration: {value!r}")


def render_toml(data: dict[str, Any]) -> str:
    lines = [
        "# Vom DiskAtlas-Agenten gespeichert. Kommentare gehen beim Speichern verloren;",
        "# die vorherige Fassung liegt in config.toml.bak.",
    ]
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    lines += [f"{_key(k)} = {_value(v)}" for k, v in data.items() if k not in tables]
    for name, table in tables.items():
        lines += ["", f"[{_key(name)}]"]
        lines += [f"{_key(k)} = {_value(v)}" for k, v in table.items()]
    return "\n".join(lines) + "\n"


def save(path: Path, values: dict[str, Any]) -> None:
    """Schreibt die geprüften Werte in [agent]; alle übrigen Optionen bleiben erhalten."""
    path = Path(path)
    data = read_raw(path)
    data.setdefault("agent", {}).update(values)
    text = render_toml(data)
    tomllib.loads(text)  # Selbstprüfung, bevor etwas überschrieben wird
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    with contextlib.suppress(OSError):
        tmp.chmod(0o600)  # enthält das Client-Token
    os.replace(tmp, path)


def check_connection(
    server_url: str, token: str = "", client: httpx.Client | None = None
) -> tuple[bool, str]:
    """Prüft Erreichbarkeit und Token so, wie der Agent den Server benutzt."""
    url = server_url.strip().rstrip("/")
    if not url:
        return False, "Bitte zuerst die Server-Adresse eintragen."
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    own = client is None
    client = client or httpx.Client(timeout=6.0)
    try:
        try:
            response = client.get(f"{url}/api/v1/health")
        except httpx.TimeoutException:
            return False, "Keine Antwort vom Server (Zeitüberschreitung)."
        except (httpx.InvalidURL, httpx.UnsupportedProtocol):
            return False, "Die Server-Adresse ist ungültig."
        except httpx.TransportError as exc:
            return False, f"Server nicht erreichbar: {exc}"
        if 300 <= response.status_code < 400:
            target = response.headers.get("location", "?")
            return False, f"Der Server leitet um nach {target} – bitte diese Adresse eintragen."
        if response.status_code != 200:
            return False, f"Antwort {response.status_code} – ist das ein DiskAtlas-Server?"
        try:
            version = response.json().get("version", "?")
        except ValueError:
            return False, "Die Antwort stammt nicht von einem DiskAtlas-Server."
        response = client.get(
            f"{url}/api/v1/ingest/commands", params={"host": TEST_HOST}, headers=headers
        )
        if response.status_code in (401, 403):
            return False, "Server erreichbar, aber das Client-Token wird abgelehnt."
        if response.status_code != 200:
            return False, f"Die Agent-Schnittstelle antwortet mit {response.status_code}."
        return True, f"Verbindung in Ordnung (Server-Version {version}, Token akzeptiert)."
    finally:
        if own:
            client.close()
