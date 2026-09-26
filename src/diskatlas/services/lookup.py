"""Platte anhand einer gescannten oder getippten Seriennummer finden – und sagen, wo sie ist."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from diskatlas.db.models import Disk
from diskatlas.services import bays, hosts

MIN_CODE = 4  # kürzere Eingaben wären zu mehrdeutig


def normalize(value: str | None) -> str:
    """Nur Buchstaben und Ziffern, groß – Barcodes enthalten oft Trenner oder Präfixe."""
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def find_disks(session: Session, code: str, visible: Select | None = None) -> list[Disk]:
    """Genau passende Platten; gibt es keine, dann solche, deren Seriennummer/WWN im Code
    steckt (Etikett mit Zusatztext) oder die den getippten Teil enthalten."""
    wanted = normalize(code)
    if len(wanted) < MIN_CODE:
        return []
    stmt = select(Disk).options(selectinload(Disk.labels), selectinload(Disk.volumes))
    if visible is not None:
        stmt = stmt.where(Disk.id.in_(visible))
    exact, partial = [], []
    for disk in session.scalars(stmt):
        for candidate in (normalize(disk.serial), normalize(disk.wwn)):
            if not candidate:
                continue
            if candidate == wanted:
                exact.append(disk)
                break
            if (len(candidate) >= 6 and candidate in wanted) or wanted in candidate:
                partial.append(disk)
                break
    return exact or partial


@dataclass
class Whereabouts:
    state: str  # "bay" (steckt in einem Schacht) | "connected" | "offline"
    host: str | None = None
    bay: int | None = None
    location: str | None = None  # Lagerort, wenn abgesteckt


def whereabouts(session: Session, disk: Disk) -> Whereabouts:
    if not disk.is_connected:
        return Whereabouts("offline", disk.last_host, None, disk.location)
    config = bays.load_config(session, disk.owner_user_id)  # die Schächte gehören dem Besitzer
    snapshot = hosts.get(session, disk.last_host)
    if snapshot is not None and (config.host in (None, disk.last_host)):
        for info in snapshot.present:
            same = info.disk_key == disk.disk_key or (info.serial and info.serial == disk.serial)
            if same and info.port in config.ports:
                return Whereabouts("bay", disk.last_host, config.ports.index(info.port) + 1)
    return Whereabouts("connected", disk.last_host)
