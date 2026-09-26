"""Hot-Swap-Schächte: Zuordnung SATA-Port -> Schachtnummer und Belegung.

Die Zuordnung ist eine Eigenschaft des Rechners (welcher Port führt zu welchem Einschub) und
wird einmalig per Assistent eingerichtet. Sie liegt als kleine JSON-Datei im Datenverzeichnis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from diskatlas.config import default_data_dir
from diskatlas.db.models import Disk
from diskatlas.probe.ports import SataPort

BAY_COUNT = 4


def default_bays_path() -> Path:
    return default_data_dir() / "bays.json"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_reverse(path: Path) -> bool:
    """True, wenn Schacht 1 unten liegt (Anzeige dann von 4 nach 1)."""
    return bool(_read(path).get("reverse", False))


def load_assignment(path: Path) -> list[str | None]:
    """Ports je Schacht (Index 0 = Schacht 1); None = nicht zugeordnet."""
    ports = _read(path).get("ports", [])
    if not isinstance(ports, list):
        return [None] * BAY_COUNT
    ports = [p if isinstance(p, str) and p.startswith("ata") else None for p in ports]
    return (ports + [None] * BAY_COUNT)[:BAY_COUNT]


def save_assignment(path: Path, ports: list[str | None], reverse: bool = False) -> None:
    cleaned = [p or None for p in (ports + [None] * BAY_COUNT)[:BAY_COUNT]]
    used = [p for p in cleaned if p]
    if len(used) != len(set(used)):
        raise ValueError("Ein Port darf nur einem Schacht zugeordnet sein.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ports": cleaned, "reverse": reverse}, indent=2), encoding="utf-8")


@dataclass
class Bay:
    number: int
    port: str | None
    state: str  # "unassigned" | "empty" | "disk" | "unknown"
    device: str | None = None
    disk: Disk | None = None
    indexing: bool = False


def build_bays(
    session: Session, assignment: list[str | None], live: dict[str, SataPort]
) -> list[Bay]:
    serials = {p.serial for p in live.values() if p.serial}
    by_serial = {}
    if serials:
        stmt = select(Disk).where(Disk.serial.in_(serials))
        by_serial = {d.serial: d for d in session.scalars(stmt)}
    bays = []
    for i, port in enumerate(assignment, start=1):
        if port is None:
            bays.append(Bay(i, None, "unassigned"))
            continue
        sata = live.get(port)
        if sata is None:
            bays.append(Bay(i, port, "empty"))
            continue
        disk = by_serial.get(sata.serial)
        if disk is None:
            bays.append(Bay(i, port, "unknown", device=sata.device))
            continue
        indexing = any(v.index_status == "running" for v in disk.volumes)
        bays.append(Bay(i, port, "disk", device=sata.device, disk=disk, indexing=indexing))
    return bays
