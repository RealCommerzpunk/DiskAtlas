"""Doublettenprüfung auf Basis des Dateiindex (ohne Prüfsummen, ohne Zugriff auf die Platten).

Dateien gelten als Doublette bei gleichem Namen (ohne Groß-/Kleinschreibung) und gleicher Größe.
Ordner gelten als Doublette bei identischem Inhalt: dieselben relativen Pfade mit denselben
Größen im gesamten Unterbaum. Der Ordnername selbst spielt keine Rolle. Berücksichtigt wird
jeweils der aktive Index aller Festplatten – auch der gerade abgesteckten.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from diskatlas.db.models import Disk, FileEntry, Volume

_MASK = (1 << 64) - 1
MAX_FOLDER_GROUPS = 300


@dataclass
class FileDupQuery:
    min_size: int = 1
    extension: str = ""
    limit: int = 50
    offset: int = 0
    exclude_disk_ids: tuple[int, ...] = ()


@dataclass
class FileDupGroup:
    name: str
    size: int
    members: list[tuple[FileEntry, Volume, Disk]] = field(default_factory=list)

    @property
    def wasted(self) -> int:
        return self.size * (len(self.members) - 1)


@dataclass
class FolderRef:
    volume: Volume
    disk: Disk
    path: str  # "" = Wurzel des Volumes


@dataclass
class FolderDupGroup:
    file_count: int
    size: int
    folders: list[FolderRef] = field(default_factory=list)

    @property
    def wasted(self) -> int:
        return self.size * (len(self.folders) - 1)


@dataclass
class FolderDupQuery:
    min_files: int = 2
    min_size: int = 0
    include_hidden: bool = False  # Ordner, deren Pfad eine Komponente ".xyz" enthält
    exclude_disk_ids: tuple[int, ...] = ()


# ------------------------------------------------------------------ Dateien
def find_file_duplicates(
    session: Session, q: FileDupQuery
) -> tuple[list[FileDupGroup], int, int]:
    """Liefert (Gruppen der aktuellen Seite, Anzahl Gruppen, insgesamt verschwendeter Platz)."""
    key = func.lower(FileEntry.name)
    base = (
        select(key.label("k"), FileEntry.size.label("size"), func.count().label("n"))
        .join(Volume, FileEntry.volume_id == Volume.id)
        .where(FileEntry.scan_id == Volume.active_scan_id, FileEntry.size >= max(q.min_size, 1))
    )
    if q.exclude_disk_ids:
        base = base.where(Volume.disk_id.notin_(q.exclude_disk_ids))
    exts = [e.strip().lstrip(".").lower() for e in q.extension.replace(";", ",").split(",")]
    if exts := [e for e in exts if e]:
        base = base.where(FileEntry.extension.in_(exts))
    grouped = base.group_by(key, FileEntry.size).having(func.count() > 1).subquery()

    total = session.scalar(select(func.count()).select_from(grouped)) or 0
    wasted_total = session.scalar(
        select(func.coalesce(func.sum(grouped.c.size * (grouped.c.n - 1)), 0))
    ) or 0
    page = session.execute(
        select(grouped.c.k, grouped.c.size)
        .order_by((grouped.c.size * (grouped.c.n - 1)).desc(), grouped.c.k)
        .limit(q.limit).offset(q.offset)
    ).all()
    if not page:
        return [], total, int(wasted_total)

    groups = {(k, size): FileDupGroup(name=k, size=size) for k, size in page}
    members = (
        select(FileEntry, Volume, Disk)
        .join(Volume, FileEntry.volume_id == Volume.id)
        .join(Disk, Volume.disk_id == Disk.id)
        .where(
            FileEntry.scan_id == Volume.active_scan_id,
            tuple_(key, FileEntry.size).in_(list(groups)),
        )
        .order_by(FileEntry.path)
    )
    if q.exclude_disk_ids:
        members = members.where(Volume.disk_id.notin_(q.exclude_disk_ids))
    rows = session.execute(members)
    for entry, volume, disk in rows:
        groups[(entry.name.lower(), entry.size)].members.append((entry, volume, disk))
    ordered = [groups[(k, size)] for k, size in page]
    for g in ordered:
        g.name = g.members[0][0].name if g.members else g.name
    return ordered, total, int(wasted_total)


# ------------------------------------------------------------------ Ordner
def find_folder_duplicates(
    session: Session, q: FolderDupQuery
) -> tuple[list[FolderDupGroup], int]:
    """Liefert (Gruppen, insgesamt verschwendeter Platz); maximal MAX_FOLDER_GROUPS Gruppen.

    Je Ordner wird über alle enthaltenen Dateien eine reihenfolgeunabhängige Signatur aus
    Anzahl, Gesamtgröße und Summe der Hashes (relativer Pfad, Größe) gebildet. Gleiche
    Signatur = gleicher Inhalt. Unterordner, die nur Teil einer bereits gemeldeten
    Doublette sind, werden ausgeblendet.
    """
    acc: dict[tuple[int, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    stmt = (
        select(FileEntry.volume_id, FileEntry.path, FileEntry.size)
        .join(Volume, FileEntry.volume_id == Volume.id)
        .where(FileEntry.scan_id == Volume.active_scan_id)
        .execution_options(yield_per=20000)
    )
    if q.exclude_disk_ids:
        stmt = stmt.where(Volume.disk_id.notin_(q.exclude_disk_ids))
    for volume_id, path, size in session.execute(stmt):
        parts = path.split("/")
        size = size or 0
        for depth in range(len(parts)):  # depth 0 = Wurzel des Volumes
            entry = acc[(volume_id, "/".join(parts[:depth]))]
            entry[0] += 1
            entry[1] += size
            entry[2] = (entry[2] + hash(("/".join(parts[depth:]).lower(), size))) & _MASK

    by_signature: dict[tuple[int, int, int], list[tuple[int, str]]] = defaultdict(list)
    for folder, (count, size, digest) in acc.items():
        if count < max(q.min_files, 1) or size < q.min_size:
            continue
        if not q.include_hidden and any(p.startswith(".") for p in folder[1].split("/")):
            continue
        by_signature[(count, size, digest)].append(folder)
    duplicates = {sig: folders for sig, folders in by_signature.items() if len(folders) > 1}

    member_group = {folder: sig for sig, folders in duplicates.items() for folder in folders}

    def is_redundant(folders: list[tuple[int, str]]) -> bool:
        parents = []
        for volume_id, path in folders:
            if not path:
                return False
            parents.append((volume_id, path.rpartition("/")[0]))
        sigs = {member_group.get(p) for p in parents}
        return len(set(parents)) == len(parents) and len(sigs) == 1 and None not in sigs

    reported = {sig: f for sig, f in duplicates.items() if not is_redundant(f)}
    wasted_total = sum(sig[1] * (len(f) - 1) for sig, f in reported.items())
    top = sorted(reported.items(), key=lambda kv: kv[0][1] * (len(kv[1]) - 1), reverse=True)
    top = top[:MAX_FOLDER_GROUPS]

    volume_ids = {vid for _, folders in top for vid, _ in folders}
    volumes = {
        v.id: (v, d)
        for v, d in session.execute(
            select(Volume, Disk)
            .join(Disk, Volume.disk_id == Disk.id)
            .where(Volume.id.in_(volume_ids))
        )
    }
    groups = []
    for (count, size, _), folders in top:
        groups.append(
            FolderDupGroup(
                file_count=count,
                size=size,
                folders=[
                    FolderRef(volume=volumes[vid][0], disk=volumes[vid][1], path=path)
                    for vid, path in sorted(folders, key=lambda f: (volumes[f[0]][1].id, f[1]))
                ],
            )
        )
    return groups, wasted_total
