"""Hot-Swap-Schächte: Zuordnung SATA-Port -> Schachtnummer und Belegung.

Schächte sind eine Eigenschaft des **Clients** (Rechners): Anzahl, Zuordnung und Richtung liegen in
den Spalten `clients.bay_*`; ohne Haken „Keine Wechselschächte“ (`has_bays`) erscheint je Client ein
Schachtblock im Dashboard. Die Portbelegung meldet der Agent per Heartbeat (Tabelle `host_states`,
Schlüssel `client:<id>`). Im lokalen Betrieb ohne Anmeldung gibt es keine Clients; dort bleibt eine
einzige Zuordnung in der Tabelle `settings`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Select, delete, select
from sqlalchemy.orm import Session

from diskatlas.config import default_data_dir
from diskatlas.db.models import Client, Disk, Setting, User
from diskatlas.services import hosts
from diskatlas.services.authz import Viewer
from diskatlas.services.hosts import HostSnapshot

DEFAULT_BAYS = 4
MAX_BAYS = 24
SETTING_KEY = "bays"


class BayError(ValueError):
    """Eingabefehler mit Text für den Benutzer."""


def setting_key(user_id: int | None) -> str:
    """Frühere Ablage je Benutzer (ohne Anmeldung: eine einzige)."""
    return SETTING_KEY if user_id is None else f"{SETTING_KEY}:{user_id}"


def default_bays_path() -> Path:
    """Ort der früheren lokalen Schachtdatei (wird nur noch einmalig importiert)."""
    return default_data_dir() / "bays.json"


@dataclass
class BayConfig:
    host: str | None = None  # nur lokaler Betrieb/Altbestand
    ports: list[str | None] = field(default_factory=lambda: [None] * DEFAULT_BAYS)
    reverse: bool = False  # True: Schacht 1 liegt unten (Anzeige von oben nach unten umgekehrt)

    @property
    def count(self) -> int:
        return len(self.ports)

    @property
    def assigned(self) -> int:
        return sum(1 for p in self.ports if p)


def clean_ports(raw, count: int | None = None) -> list[str | None]:
    """Nur gültige Portnamen; auf `count` Schächte aufgefüllt bzw. gekürzt."""
    ports = [p if isinstance(p, str) and p.startswith("ata") else None
             for p in (raw if isinstance(raw, list) else [])]
    if count is None:
        count = len(ports) if 1 <= len(ports) <= MAX_BAYS else DEFAULT_BAYS
    return (ports + [None] * count)[:count]


def check_count(count: int) -> int:
    if not 1 <= count <= MAX_BAYS:
        raise BayError(f"Die Anzahl der Schächte muss zwischen 1 und {MAX_BAYS} liegen.")
    return count


def check_unique(ports: list[str | None]) -> None:
    used = [p for p in ports if p]
    if len(used) != len(set(used)):
        raise BayError("Ein Port darf nur einem Schacht zugeordnet sein.")


# ------------------------------------------------------------------ lokaler Betrieb (settings)
def _read_setting(session: Session, key: str) -> dict:
    row = session.get(Setting, key)
    try:
        data = json.loads(row.value) if row else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def load_config(session: Session, user_id: int | None = None) -> BayConfig:
    data = _read_setting(session, setting_key(user_id))
    stored = data.get("ports")
    count = min(max(len(stored) if isinstance(stored, list) else 0, DEFAULT_BAYS), MAX_BAYS)
    return BayConfig(
        host=data.get("host"), ports=clean_ports(stored, count),
        reverse=bool(data.get("reverse", False)),
    )


def save_config(session: Session, config: BayConfig, user_id: int | None = None) -> None:
    check_unique(config.ports)
    value = json.dumps({"host": config.host, "ports": config.ports, "reverse": config.reverse})
    row = session.get(Setting, setting_key(user_id))
    if row is None:
        session.add(Setting(key=setting_key(user_id), value=value))
    else:
        row.value = value


def import_legacy_file(session: Session, path: Path, host: str) -> bool:
    """Übernimmt eine frühere lokale `bays.json` einmalig in die Datenbank."""
    if session.get(Setting, SETTING_KEY) is not None:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    ports = data.get("ports", []) if isinstance(data, dict) else []
    save_config(session, BayConfig(host=host, ports=clean_ports(ports, DEFAULT_BAYS),
                                   reverse=bool(data.get("reverse", False))))
    return True


# ------------------------------------------------------------------ Clients
def client_config(client: Client) -> BayConfig:
    try:
        raw = json.loads(client.bay_ports) if client.bay_ports else []
    except ValueError:
        raw = []
    count = client.bay_count if 1 <= client.bay_count <= MAX_BAYS else DEFAULT_BAYS
    return BayConfig(ports=clean_ports(raw, count), reverse=client.bay_reverse)


def save_client_config(client: Client, config: BayConfig, has_bays: bool | None = None) -> None:
    check_count(config.count)
    check_unique(config.ports)
    client.bay_count = config.count
    client.bay_ports = json.dumps(config.ports)
    client.bay_reverse = config.reverse
    if has_bays is not None:
        client.has_bays = has_bays


def resize(ports: list[str | None], count: int) -> list[str | None]:
    """Neue Anzahl Schächte; würde das zugeordnete Ports entfernen, gibt es einen Fehler."""
    check_count(count)
    dropped = [p for p in ports[count:] if p]
    if dropped:
        raise BayError(
            f"Mit {count} Schächten würden die Zuordnungen {', '.join(dropped)} wegfallen. "
            "Bitte zuerst im Assistenten freigeben."
        )
    return (ports + [None] * count)[:count]


def adopt_legacy(session: Session, client: Client, host: str) -> bool:
    """Übernimmt eine frühere, benutzerweite Schachtzuordnung beim ersten Heartbeat des Clients.

    Nur wenn der Client noch keinen Port zugeordnet hat, in der alten Zuordnung mindestens einer
    stand und sie zu diesem Rechner passt. Das Löschen des alten Settings entscheidet atomar,
    wer es bekommt: ein weiterer Client desselben Benutzers übernimmt es nicht erneut.
    """
    if any(client_config(client).ports):
        return False
    keys = [setting_key(client.user_id)]
    if session.get(User, client.user_id).is_master:
        keys.append(SETTING_KEY)  # verwaistes Setting aus Version 0.4.0
    for key in keys:
        data = _read_setting(session, key)
        ports = clean_ports(data.get("ports"))
        if not any(ports) or data.get("host") not in (None, host):
            continue
        if session.execute(delete(Setting).where(Setting.key == key)).rowcount != 1:
            continue  # ein anderer Heartbeat war schneller
        save_client_config(client, BayConfig(ports=ports, reverse=bool(data.get("reverse"))),
                           has_bays=True)
        return True
    return False


# ------------------------------------------------------------------ Anzeige
@dataclass
class Bay:
    number: int
    port: str | None
    state: str  # "unassigned" | "empty" | "disk" | "unknown" | "offline" (Agent meldet sich nicht)
    device: str | None = None
    disk: Disk | None = None
    indexing: bool = False


def build_bays(
    session: Session, config: BayConfig, snapshot: HostSnapshot | None,
    visible: Select | None = None,
) -> list[Bay]:
    """Schächte von unten nach oben nummeriert; die Anzeigereihenfolge regelt `config.reverse`."""
    live = snapshot.by_port() if snapshot else {}
    serials = {p.serial for p in live.values() if p.serial}
    by_serial: dict[str, Disk] = {}
    if serials:
        stmt = select(Disk).where(Disk.serial.in_(serials))
        if visible is not None:
            stmt = stmt.where(Disk.id.in_(visible))
        by_serial = {d.serial: d for d in session.scalars(stmt)}
    online = bool(snapshot and snapshot.is_online)
    bays = []
    for i, port in enumerate(config.ports, start=1):
        if port is None:
            bays.append(Bay(i, None, "unassigned"))
        elif not online:
            bays.append(Bay(i, port, "offline"))
        elif (sata := live.get(port)) is None:
            bays.append(Bay(i, port, "empty"))
        elif (disk := by_serial.get(sata.serial)) is None:
            bays.append(Bay(i, port, "unknown", device=sata.device))
        else:
            indexing = any(v.index_status == "running" for v in disk.volumes)
            bays.append(Bay(i, port, "disk", device=sata.device, disk=disk, indexing=indexing))
    return bays[::-1] if config.reverse else bays


def client_snapshot(session: Session, client: Client) -> HostSnapshot | None:
    return hosts.get(session, hosts.state_key(client, ""))


def local_snapshot(session: Session, config: BayConfig, host: str = "") -> HostSnapshot | None:
    """Lokaler Betrieb: gewählter, konfigurierter oder erster Rechner, der Ports meldet."""
    for candidate in (host, config.host):
        if candidate and (snap := hosts.get(session, candidate)):
            return snap
    return next((h for h in hosts.known_hosts(session) if h.all_ports), None)


@dataclass
class Panel:
    """Ein Schachtblock im Dashboard (ein Client bzw. der lokale Rechner)."""

    label: str  # Client-Name; leer im lokalen Betrieb
    bays: list[Bay]
    setup_url: str


def panels_for(session: Session, viewer: Viewer, visible: Select | None = None) -> list[Panel]:
    """Schachtblöcke des Betrachters: je eigenem Client mit Wechselschächten einer, lokal einer."""
    if viewer.user_id is None:  # lokaler Betrieb ohne Anmeldung: keine Clients
        config = load_config(session)
        snapshot = local_snapshot(session, config)
        if snapshot is None or not snapshot.all_ports:
            return []
        return [Panel("", build_bays(session, config, snapshot, visible), "/bays/setup")]
    panels = []
    own = session.scalars(
        select(Client).where(Client.user_id == viewer.user_id, Client.has_bays.is_(True))
        .order_by(Client.id)
    )
    for client in own:
        bays = build_bays(session, client_config(client), client_snapshot(session, client),
                          visible)
        panels.append(Panel(client.nickname, bays, f"/clients/{client.id}/bays"))
    return panels
