"""Dateibrowser: Ordnerindex je Volume und Auflistung eines Ordners.

Der Ordnerindex (`directories`) entsteht beim Abschluss der Indizierung; für Bestand aus früheren
Versionen wird er bei der ersten Ansicht nachgebaut.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from diskatlas.db.models import Directory, FileEntry, Volume

PAGE = 200
MAX_DIRS = 1000
_BATCH = 5000


def normalize_path(raw: str) -> str | None:
    """Ordnerpfad aus der Adresszeile; `..` und NUL werden abgelehnt (None)."""
    if "\0" in raw:
        return None
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def build_directories(session: Session, volume: Volume) -> None:
    """Baut den Ordnerindex des aktiven Scans neu (Anzahl und Größe je Ordner samt Unterordnern)."""
    session.execute(delete(Directory).where(Directory.volume_id == volume.id))
    scan = volume.active_scan_id
    if not scan:
        volume.dirs_scan_id = None
        return
    rows = session.execute(
        select(FileEntry.parent, func.count(), func.coalesce(func.sum(FileEntry.size), 0))
        .where(FileEntry.volume_id == volume.id, FileEntry.scan_id == scan)
        .group_by(FileEntry.parent)
    )
    totals: dict[str, list[int]] = {}
    for parent, count, size in rows:
        path = parent
        while path:  # jeden Vorfahren mitzählen (die Wurzel '' wird nicht als Ordner geführt)
            entry = totals.setdefault(path, [0, 0])
            entry[0] += count
            entry[1] += int(size)
            path = path.rpartition("/")[0]
    batch: list[dict] = []
    for path, (count, size) in totals.items():
        parent, _, name = path.rpartition("/")
        batch.append({"volume_id": volume.id, "scan_id": scan, "path": path, "parent": parent,
                      "name": name[:1024], "file_count": count, "total_size": size})
        if len(batch) >= _BATCH:
            session.execute(insert(Directory), batch)
            batch = []
    if batch:
        session.execute(insert(Directory), batch)
    volume.dirs_scan_id = scan


def ensure_directories(session: Session, volume: Volume) -> None:
    if volume.active_scan_id and volume.dirs_scan_id != volume.active_scan_id:
        build_directories(session, volume)
        session.commit()


@dataclass
class Listing:
    path: str
    folders: list[Directory]
    files: list[FileEntry]
    next_after: str | None  # Name der letzten Datei, wenn es weitere gibt
    crumbs: list[tuple[str, str]]  # (Name, Pfad) von der Wurzel bis zum aktuellen Ordner
    exists: bool


def crumbs(path: str) -> list[tuple[str, str]]:
    out, acc = [], ""
    for part in path.split("/") if path else []:
        acc = f"{acc}/{part}" if acc else part
        out.append((part, acc))
    return out


def list_dir(session: Session, volume: Volume, path: str, after: str = "") -> Listing:
    """Inhalt eines Ordners: alle Unterordner (bis MAX_DIRS) und Dateien seitenweise nach Name."""
    ensure_directories(session, volume)
    scan = volume.active_scan_id
    if not scan:
        return Listing(path, [], [], None, crumbs(path), path == "")
    folders = list(session.scalars(
        select(Directory)
        .where(Directory.volume_id == volume.id, Directory.scan_id == scan,
               Directory.parent == path)
        .order_by(Directory.name).limit(MAX_DIRS)
    )) if not after else []
    stmt = (select(FileEntry)
            .where(FileEntry.volume_id == volume.id, FileEntry.scan_id == scan,
                   FileEntry.parent == path)
            .order_by(FileEntry.name).limit(PAGE + 1))
    if after:
        stmt = stmt.where(FileEntry.name > after)
    files = list(session.scalars(stmt))
    more = len(files) > PAGE
    files = files[:PAGE]
    exists = path == "" or bool(folders or files) or session.scalar(
        select(Directory.id).where(Directory.volume_id == volume.id, Directory.scan_id == scan,
                                   Directory.path == path)
    ) is not None
    return Listing(path, folders, files, files[-1].name if more else None, crumbs(path), exists)
