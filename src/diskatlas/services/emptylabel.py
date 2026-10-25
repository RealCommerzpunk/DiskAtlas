"""Automatisches Label für komplett leere Platten.

Leer heißt: keine Partition (typisch nach nwipe), nur Partitionen ohne Dateisystem, oder jedes
Dateisystem ist indiziert und enthält zusammen 0 Dateien. Ist ein Dateisystem nicht lesbar
(verschlüsselt, unbekannt, nicht eingehängt, nie indiziert) oder passt die Belegung nicht zu
„0 Dateien“, bleibt der Zustand unbekannt – dann ändert sich nichts. Die Automatik nimmt nur das
Label wieder ab, das sie selbst angeheftet hat (`Disk.empty_label_id`); von Hand gesetzte Labels
fasst sie nie an.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from diskatlas.db.models import Disk, Label, Setting, User
from diskatlas.services import labels as label_service

DEFAULT = "leer"
LOCAL_KEY = "empty_label"  # Einstellung ohne Anmeldung (lokaler Betrieb)
MAX_NAME = 100

EMPTY, USED, UNKNOWN = "empty", "used", "unknown"
# Ein leeres Dateisystem belegt nur Verwaltungsdaten (NTFS auf 10 TB: rund 0,4 GB). Meldet der
# Index 0 Dateien, die Belegung aber deutlich mehr, war die Indizierung unvollständig (z. B.
# fehlende Rechte) – dann lieber „unbekannt“ als ein falsches „leer“.
OVERHEAD_BYTES = 1024**3
OVERHEAD_SHARE = 0.001


def _looks_used(volume) -> bool:
    if volume.used_bytes is None:
        return False
    limit = OVERHEAD_BYTES + OVERHEAD_SHARE * (volume.size_bytes or 0)
    return volume.used_bytes > limit


def state(disk: Disk) -> str:
    volumes = disk.present_volumes
    if not volumes:
        return EMPTY
    files = 0
    for volume in volumes:
        if not volume.fs_type:
            continue  # Partition ohne Dateisystem: nichts drauf
        if volume.index_status != "done" or not volume.active_scan_id:
            if volume.file_count:
                return USED  # alter Index mit Dateien: offensichtlich nicht leer
            return UNKNOWN
        if not volume.file_count and _looks_used(volume):
            return UNKNOWN
        files += volume.file_count or 0
    return USED if files else EMPTY


def _local_mode(session: Session) -> bool:
    return session.scalar(select(User.id).limit(1)) is None


def setting(session: Session, user_id: int | None) -> str:
    """Der eingestellte Labelname ("" = aus). Ohne Anmeldung gilt eine Servereinstellung."""
    if user_id is not None:
        user = session.get(User, user_id)
        return user.empty_label if user is not None else ""
    row = session.get(Setting, LOCAL_KEY)
    return DEFAULT if row is None else row.value


def _owner_name(session: Session, disk: Disk) -> str:
    if disk.owner_user_id is not None:
        return setting(session, disk.owner_user_id)
    return setting(session, None) if _local_mode(session) else ""  # herrenlos: nie


def apply(session: Session, disk: Disk) -> None:
    """Heftet das Label an bzw. nimmt das automatisch gesetzte wieder ab."""
    auto = session.get(Label, disk.empty_label_id) if disk.empty_label_id else None
    if auto is not None and auto.owner_user_id != disk.owner_user_id:
        disk.empty_label_id = auto = None  # stammt vom früheren Besitzer
    current = state(disk)
    if current == USED:
        if auto is not None and auto in disk.labels:
            disk.labels.remove(auto)
        disk.empty_label_id = None
        return
    if current != EMPTY or disk.empty_label_id is not None:
        return  # unbekannt, oder schon einmal gesetzt (von Hand abgenommen: so lassen)
    name = _owner_name(session, disk)
    if not name:
        return
    label = label_service.find(session, disk.owner_user_id, name)
    if label is None:
        label = Label(owner_user_id=disk.owner_user_id, name=name, category="Status",
                      color="#8a94a6")
        session.add(label)
        session.flush()
    if label in disk.labels:
        return  # von Hand gesetzt: bleibt in Hand des Benutzers
    disk.labels.append(label)
    disk.empty_label_id = label.id


def apply_everywhere(session: Session) -> None:
    """Beim Start: auch Platten bewerten, die seit der Einführung nicht mehr gemeldet wurden."""
    for disk in session.scalars(select(Disk)):
        apply(session, disk)


def apply_all(session: Session, owner_user_id: int | None) -> int:
    """Alle Platten des Besitzers neu bewerten (nach Änderung der Einstellung)."""
    stmt = select(Disk)
    stmt = stmt.where(Disk.owner_user_id.is_(None) if owner_user_id is None
                      else Disk.owner_user_id == owner_user_id)
    disks = list(session.scalars(stmt))
    for disk in disks:
        apply(session, disk)
    return len(disks)


class SettingError(ValueError):
    pass


def change(session: Session, user_id: int | None, new_name: str) -> None:
    """Neuer Labelname. Das bisherige Label wird umbenannt, damit Platten und Filter stimmen."""
    new_name = new_name.strip()[:MAX_NAME]
    old_name = setting(session, user_id)
    if new_name and new_name != old_name:
        old = label_service.find(session, user_id, old_name) if old_name else None
        clash = label_service.find(session, user_id, new_name)
        if old is not None and clash is not None:
            raise SettingError(f"Es gibt schon ein Label „{new_name}“. Bitte einen anderen Namen "
                               "wählen oder eines der beiden Labels löschen.")
        if old is not None:
            old.name = new_name
    if user_id is not None:
        session.get(User, user_id).empty_label = new_name
    else:
        row = session.get(Setting, LOCAL_KEY) or Setting(key=LOCAL_KEY)
        row.value = new_name
        session.add(row)
    session.flush()
    if new_name:
        apply_all(session, user_id)


def label_renamed(session: Session, label: Label, old_name: str) -> None:
    """Wird das Label auf der Labels-Seite umbenannt, zieht die Einstellung mit."""
    if old_name and old_name == setting(session, label.owner_user_id):
        if label.owner_user_id is not None:
            session.get(User, label.owner_user_id).empty_label = label.name
        else:
            row = session.get(Setting, LOCAL_KEY) or Setting(key=LOCAL_KEY)
            row.value = label.name
            session.add(row)
