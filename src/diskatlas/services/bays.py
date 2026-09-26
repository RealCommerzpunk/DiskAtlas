"""Hot-Swap-Schächte: Zuordnung SATA-Port -> Schachtnummer und Belegung.

Die Portbelegung meldet der Agent des jeweiligen Rechners (Heartbeat); die Zuordnung zu Schächten
ist eine Einstellung des Servers (Tabelle `settings`) und wird einmalig per Assistent eingerichtet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from diskatlas.config import default_data_dir
from diskatlas.db.models import Disk, Setting
from diskatlas.services.hosts import HostSnapshot

BAY_COUNT = 4
SETTING_KEY = "bays"


def setting_key(user_id: int | None) -> str:
    """Die Schachtzuordnung gehört zum Rechner eines Benutzers (ohne Anmeldung: eine einzige)."""
    return SETTING_KEY if user_id is None else f"{SETTING_KEY}:{user_id}"


def default_bays_path() -> Path:
    """Ort der früheren lokalen Schachtdatei (wird nur noch einmalig importiert)."""
    return default_data_dir() / "bays.json"


@dataclass
class BayConfig:
    host: str | None = None
    ports: list[str | None] = field(default_factory=lambda: [None] * BAY_COUNT)
    reverse: bool = False  # True: Schacht 1 liegt unten (Anzeige von 4 nach 1)


def load_config(session: Session, user_id: int | None = None) -> BayConfig:
    row = session.get(Setting, setting_key(user_id))
    try:
        data = json.loads(row.value) if row else {}
    except ValueError:
        data = {}
    ports = data.get("ports", []) if isinstance(data, dict) else []
    ports = [p if isinstance(p, str) and p.startswith("ata") else None for p in ports]
    return BayConfig(
        host=data.get("host") if isinstance(data, dict) else None,
        ports=(ports + [None] * BAY_COUNT)[:BAY_COUNT],
        reverse=bool(data.get("reverse", False)) if isinstance(data, dict) else False,
    )


def save_config(session: Session, config: BayConfig, user_id: int | None = None) -> None:
    used = [p for p in config.ports if p]
    if len(used) != len(set(used)):
        raise ValueError("Ein Port darf nur einem Schacht zugeordnet sein.")
    value = json.dumps(
        {"host": config.host, "ports": (config.ports + [None] * BAY_COUNT)[:BAY_COUNT],
         "reverse": config.reverse}
    )
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
    save_config(session, BayConfig(host=host, ports=(list(ports) + [None] * BAY_COUNT)[:BAY_COUNT],
                                   reverse=bool(data.get("reverse", False))))
    return True


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
