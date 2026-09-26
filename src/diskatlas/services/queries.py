"""Lesende Abfragen für Dashboard, Suche und API."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from diskatlas.db.models import Disk, FileEntry, Label, SmartSnapshot, Volume, disk_labels
from diskatlas.probe.smart import health_reasons as smart_health_reasons

HEALTH_LABELS = {"ok": "Gut", "warning": "Warnung", "failed": "Kritisch", "unknown": "Unbekannt"}

GROUP_OPTIONS = {
    "none": "Keine Gruppierung",
    "health": "Gesundheit",
    "connection": "Verbindung",
    "label": "Label",
    "category": "Label-Kategorie",
    "host": "Rechner",
    "transport": "Anschluss",
    "media": "Typ (HDD/SSD)",
    "fs": "Dateisystem",
    "vendor": "Hersteller",
    "series": "Serie (Verkaufsbezeichnung)",
}

SORT_OPTIONS: dict[str, tuple[str, Callable[[Disk], object]]] = {
    "name": ("Name", lambda d: d.display_name.lower()),
    "fs": ("Dateisystem", lambda d: ", ".join(d.fs_types) or "~"),  # ohne Dateisystem zuletzt
    "size": ("Kapazität", lambda d: d.size_bytes or 0),
    "free": ("Freier Speicher", lambda d: d.free_bytes or 0),
    "usage": ("Belegung %", lambda d: d.usage_percent or 0),
    "files": ("Anzahl Dateien", lambda d: d.file_count),
    "health": ("Gesundheit", lambda d: d.health_rank),
    "temperature": ("Temperatur", lambda d: d.temperature_c or 0),
    "hours": ("Betriebsstunden", lambda d: d.power_on_hours or 0),
    "last_seen": ("Zuletzt gesehen", lambda d: d.last_seen or datetime.min),
}

FILE_SORTS = {
    "name": FileEntry.name,
    "size": FileEntry.size,
    "mtime": FileEntry.mtime,
    "path": FileEntry.path,
}


@dataclass
class DiskFilter:
    q: str = ""
    health: str = ""
    connected: str = ""  # "", "yes", "no"
    label_id: int | None = None
    fs: str = ""  # Dateisystem, oder NO_FS für Platten ohne erkanntes Dateisystem
    usage: str = ""  # "", "known", "unknown"


NO_FS = "__none__"


def known_fs_types(disks: list[Disk]) -> list[str]:
    return sorted({fs for d in disks for fs in d.fs_types})


@dataclass
class DashboardStats:
    disk_count: int = 0
    connected: int = 0
    capacity: int = 0
    used: int = 0
    free: int = 0
    unknown: int = 0  # Kapazität von Platten ohne bekannte Belegung
    files: int = 0
    health: dict[str, int] = field(default_factory=dict)


def load_disks(session: Session) -> list[Disk]:
    stmt = select(Disk).options(selectinload(Disk.labels), selectinload(Disk.volumes))
    return list(session.scalars(stmt))


SYSTEM_LABEL = "system"


def system_disk_ids(session: Session) -> tuple[int, ...]:
    """IDs aller Festplatten mit dem Label „System“ (Groß-/Kleinschreibung egal)."""
    stmt = (
        select(disk_labels.c.disk_id)
        .join(Label, Label.id == disk_labels.c.label_id)
        .where(func.lower(Label.name) == SYSTEM_LABEL)
    )
    return tuple(sorted(set(session.scalars(stmt))))


def get_disk(session: Session, disk_id: int) -> Disk | None:
    return session.scalar(
        select(Disk)
        .where(Disk.id == disk_id)
        .options(selectinload(Disk.labels), selectinload(Disk.volumes))
    )


def filter_disks(disks: list[Disk], flt: DiskFilter) -> list[Disk]:
    result = []
    terms = flt.q.lower().split()
    for disk in disks:
        if flt.health and disk.health != flt.health:
            continue
        if flt.connected == "yes" and not disk.is_connected:
            continue
        if flt.connected == "no" and disk.is_connected:
            continue
        if flt.label_id and flt.label_id not in {lab.id for lab in disk.labels}:
            continue
        if flt.fs == NO_FS and disk.fs_types:
            continue
        if flt.fs and flt.fs != NO_FS and flt.fs.lower() not in disk.fs_types:
            continue
        if flt.usage == "unknown" and disk.usage_known:
            continue
        if flt.usage == "known" and not disk.usage_known:
            continue
        if terms:
            haystack = " ".join(
                str(x)
                for x in (
                    disk.display_name,
                    disk.model,
                    disk.serial,
                    disk.vendor,
                    disk.product_line,
                    disk.notes,
                    disk.location,
                    disk.last_host,
                    *[v.label for v in disk.volumes],
                    *disk.fs_types,
                    *[lab.name for lab in disk.labels],
                    *[lab.category for lab in disk.labels],
                )
                if x
            ).lower()
            if not all(t in haystack for t in terms):
                continue
        result.append(disk)
    return result


def sort_disks(disks: list[Disk], key: str, descending: bool = False) -> list[Disk]:
    _, getter = SORT_OPTIONS.get(key, SORT_OPTIONS["name"])
    return sorted(disks, key=getter, reverse=descending)


def group_disks(disks: list[Disk], by: str) -> list[tuple[str, list[Disk]]]:
    """Gruppiert bereits sortierte Festplatten. Bei Labels kann eine Disk mehrfach auftauchen."""
    if by not in GROUP_OPTIONS or by == "none":
        return [("", disks)]
    groups: dict[str, list[Disk]] = defaultdict(list)
    for disk in disks:
        for name in _group_names(disk, by):
            groups[name].append(disk)
    if by == "health":
        order = {HEALTH_LABELS[k]: v for k, v in (("failed", 0), ("warning", 1), ("unknown", 2))}
        return sorted(groups.items(), key=lambda kv: (order.get(kv[0], 3), kv[0]))
    return sorted(groups.items(), key=lambda kv: (kv[0].startswith("("), kv[0].lower()))


def _group_names(disk: Disk, by: str) -> list[str]:
    if by == "health":
        return [HEALTH_LABELS.get(disk.health, disk.health)]
    if by == "connection":
        return ["Angeschlossen" if disk.is_connected else "Nicht angeschlossen"]
    if by == "label":
        return [lab.name for lab in disk.labels] or ["(ohne Label)"]
    if by == "category":
        cats = {lab.category or "(ohne Kategorie)" for lab in disk.labels}
        return sorted(cats) or ["(ohne Label)"]
    if by == "host":
        return [disk.last_host or "(unbekannt)"]
    if by == "transport":
        return [(disk.transport or "unbekannt").upper()]
    if by == "media":
        return [disk.media_type or "(unbekannt)"]
    if by == "vendor":
        return [disk.vendor or "(unbekannt)"]
    if by == "series":
        return [disk.brand or "(unbekannt)"]
    if by == "fs":
        return disk.fs_types or ["(kein Dateisystem)"]
    return [""]


def dashboard_stats(disks: list[Disk]) -> DashboardStats:
    stats = DashboardStats(health={k: 0 for k in HEALTH_LABELS})
    for disk in disks:
        stats.disk_count += 1
        stats.connected += int(disk.is_connected)
        stats.capacity += disk.size_bytes or 0
        stats.used += disk.used_bytes or 0
        stats.free += disk.free_bytes or 0
        if disk.used_bytes is None or disk.free_bytes is None:
            stats.unknown += disk.size_bytes or 0
        stats.files += disk.file_count
        stats.health[disk.health] = stats.health.get(disk.health, 0) + 1
    return stats


def list_labels(session: Session) -> list[tuple[Label, int]]:
    stmt = (
        select(Label, func.count(disk_labels.c.disk_id))
        .outerjoin(disk_labels, disk_labels.c.label_id == Label.id)
        .group_by(Label.id)
        .order_by(func.coalesce(Label.category, ""), Label.name)
    )
    return [(label, count) for label, count in session.execute(stmt)]


def smart_history(session: Session, disk_id: int, limit: int = 20) -> list[SmartSnapshot]:
    stmt = (
        select(SmartSnapshot)
        .where(SmartSnapshot.disk_id == disk_id)
        .order_by(SmartSnapshot.taken_at.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def health_reasons(session: Session, disk_id: int) -> list[str]:
    """Gründe für eine Nicht-„Gut“-Bewertung aus der letzten SMART-Messung."""
    raw = session.scalar(
        select(SmartSnapshot.raw_json)
        .where(SmartSnapshot.disk_id == disk_id)
        .order_by(SmartSnapshot.taken_at.desc())
        .limit(1)
    )
    if not raw:
        return []
    try:
        return smart_health_reasons(json.loads(raw))
    except (ValueError, TypeError, AttributeError):
        return []


def extension_summary(session: Session, disk_id: int, limit: int = 15) -> list[tuple]:
    stmt = (
        select(
            func.coalesce(FileEntry.extension, ""),
            func.count(FileEntry.id),
            func.sum(FileEntry.size),
        )
        .join(Volume, FileEntry.volume_id == Volume.id)
        .where(Volume.disk_id == disk_id, FileEntry.scan_id == Volume.active_scan_id)
        .group_by(func.coalesce(FileEntry.extension, ""))
        .order_by(func.sum(FileEntry.size).desc())
        .limit(limit)
    )
    return list(session.execute(stmt))


@dataclass
class FileQuery:
    q: str = ""
    extension: str = ""
    disk_id: int | None = None
    label_id: int | None = None
    min_size: int | None = None
    max_size: int | None = None
    sort: str = "name"
    descending: bool = False
    limit: int = 100
    offset: int = 0
    exclude_disk_ids: tuple[int, ...] = ()


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _term_condition(term: str):
    """Suchbegriff → Bedingung. Ohne Wildcards Teilstring im Pfad; mit `*`/`?` ein Muster,
    das bei Begriffen ohne `/` auf den Dateinamen passt (z. B. `*.mkv`, `film?.avi`)."""
    if "*" not in term and "?" not in term:
        return FileEntry.path.ilike(f"%{_escape_like(term)}%", escape="\\")
    pattern = _escape_like(term).replace("*", "%").replace("?", "_")
    if "/" in term:
        return FileEntry.path.ilike(f"%{pattern}", escape="\\")
    return FileEntry.name.ilike(pattern, escape="\\")


def _file_base_query(fq: FileQuery) -> Select:
    stmt = (
        select(FileEntry, Volume, Disk)
        .join(Volume, FileEntry.volume_id == Volume.id)
        .join(Disk, Volume.disk_id == Disk.id)
        .where(FileEntry.scan_id == Volume.active_scan_id)
    )
    if fq.exclude_disk_ids:
        stmt = stmt.where(Disk.id.notin_(fq.exclude_disk_ids))
    for term in fq.q.split():
        stmt = stmt.where(_term_condition(term))
    if fq.extension:
        exts = [e.strip().lstrip(".").lower() for e in fq.extension.replace(";", ",").split(",")]
        exts = [e for e in exts if e]
        if exts:
            stmt = stmt.where(FileEntry.extension.in_(exts))
    if fq.disk_id:
        stmt = stmt.where(Disk.id == fq.disk_id)
    if fq.label_id:
        stmt = stmt.where(
            Disk.id.in_(select(disk_labels.c.disk_id).where(disk_labels.c.label_id == fq.label_id))
        )
    if fq.min_size is not None:
        stmt = stmt.where(FileEntry.size >= fq.min_size)
    if fq.max_size is not None:
        stmt = stmt.where(FileEntry.size <= fq.max_size)
    return stmt


def search_files(session: Session, fq: FileQuery) -> tuple[list[tuple], int]:
    stmt = _file_base_query(fq)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = FILE_SORTS.get(fq.sort, FileEntry.name)
    order = column.desc() if fq.descending else column.asc()
    rows = session.execute(stmt.order_by(order, FileEntry.id).limit(fq.limit).offset(fq.offset))
    return [tuple(r) for r in rows], total


@dataclass
class RunningIndex:
    disk_id: int
    disk_name: str
    volume_label: str | None
    mountpoint: str | None
    files_so_far: int


def running_indexes(session: Session) -> list[RunningIndex]:
    """Volumes angeschlossener Festplatten, die gerade indiziert werden (mit Zwischenstand).

    Der Zwischenstand sind die bereits geschriebenen Zeilen des neuen Scans; der bisherige
    Index bleibt bis zum erfolgreichen Abschluss unverändert aktiv.
    """
    rows = session.execute(
        select(Volume, Disk)
        .join(Disk, Volume.disk_id == Disk.id)
        .where(Volume.index_status == "running", Disk.is_connected.is_(True))
        .order_by(Disk.id, Volume.id)
    ).all()
    result = []
    for volume, disk in rows:
        so_far = session.scalar(
            select(func.count(FileEntry.id)).where(
                FileEntry.volume_id == volume.id,
                FileEntry.scan_id != (volume.active_scan_id or ""),
            )
        )
        result.append(
            RunningIndex(
                disk_id=disk.id, disk_name=disk.display_name, volume_label=volume.label,
                mountpoint=volume.mountpoint, files_so_far=so_far or 0,
            )
        )
    return result
