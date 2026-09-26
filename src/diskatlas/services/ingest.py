"""Schreibt Scan-Ergebnisse in die Datenbank (genutzt von lokalem Agenten und Ingest-API)."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session, selectinload

from diskatlas.db.models import Client, Disk, DiskTransferRequest, FileEntry, SmartSnapshot, Volume
from diskatlas.probe import catalog
from diskatlas.probe.types import DiskInfo, FileRecord, SmartInfo, VolumeInfo

# Diese Felder werden nur überschrieben, wenn der neue Wert bekannt ist – z. B. kennt
# Windows kein ext4 und würde sonst Label/Dateisystem eines Linux-Volumes löschen.
_DISK_FIELDS = ("serial", "model", "vendor", "wwn", "transport", "size_bytes", "rotational")
_VOLUME_KEEP_FIELDS = ("device", "fs_type", "label", "fs_uuid", "size_bytes")
_SMART_FIELDS = (
    "temperature_c",
    "power_on_hours",
    "power_cycles",
    "reallocated_sectors",
    "pending_sectors",
    "uncorrectable_sectors",
    "percentage_used",
)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


REJECTED_TRANSFER_PAUSE = timedelta(days=30)  # so lange stellt ein abgelehnter Antrag nicht neu


def request_transfer(
    session: Session, disk: Disk, client: Client, now: datetime
) -> DiskTransferRequest:
    """Übernahmeantrag für `disk`; ein offener/frisch abgelehnter Antrag wird wiederverwendet."""
    existing = session.scalar(
        select(DiskTransferRequest)
        .where(
            DiskTransferRequest.disk_id == disk.id,
            DiskTransferRequest.to_user_id == client.user_id,
            (DiskTransferRequest.status == "pending")
            | (
                (DiskTransferRequest.status == "rejected")
                & (DiskTransferRequest.resolved_at > now - REJECTED_TRANSFER_PAUSE)
            ),
        )
        .order_by(DiskTransferRequest.id.desc())
    )
    if existing is not None:
        return existing
    request = DiskTransferRequest(
        disk_id=disk.id, from_user_id=disk.owner_user_id, to_user_id=client.user_id,
        requested_by_client_id=client.id, status="pending", created_at=now,
    )
    session.add(request)
    return request


def upsert_disk(
    session: Session, host: str, info: DiskInfo, now: datetime | None = None,
    client: Client | None = None,
) -> Disk:
    """Legt die Platte an bzw. aktualisiert sie.

    Mit `client` (Server-Betrieb) gehört eine neue oder herrenlose Platte dessen Benutzer. Gehört
    sie schon jemand anderem, wird nichts verändert, sondern nur ein Übernahmeantrag für den
    Besitzer angelegt – fremde Clients schreiben nie in Platten anderer Benutzer.
    """
    now = now or utcnow()
    disk = session.scalar(
        select(Disk).where(Disk.disk_key == info.key).options(selectinload(Disk.volumes))
    )
    if disk is None:
        disk = Disk(disk_key=info.key, first_seen=now, health="unknown", volumes=[],
                    owner_user_id=client.user_id if client else None)
        session.add(disk)
    elif client is not None:
        if disk.owner_user_id is None:
            disk.owner_user_id = client.user_id
        elif disk.owner_user_id != client.user_id:
            request_transfer(session, disk, client, now)
            session.flush()
            return disk

    for attr in _DISK_FIELDS:
        value = getattr(info, attr)
        if value is not None:
            setattr(disk, attr, value)
    disk.removable = info.removable
    disk.is_system = info.is_system
    disk.is_connected = True
    disk.last_seen = now
    disk.last_host = host
    disk.last_device = info.device

    if info.smart is not None:
        apply_smart(session, disk, info.smart, now)
    apply_identity(disk, ((info.smart.raw or {}) if info.smart else {}).get("model_family"))

    existing = {v.volume_key: v for v in disk.volumes}
    reported = set()
    for vol_info in info.volumes:
        reported.add(vol_info.key)
        volume = existing.get(vol_info.key)
        if volume is None:
            volume = Volume(volume_key=vol_info.key, index_status="never", file_count=0)
            disk.volumes.append(volume)
            existing[vol_info.key] = volume
        if _was_reformatted(volume, vol_info):
            _reset_index(session, volume)
        _update_volume(volume, vol_info, now)
    for key, volume in existing.items():
        if key not in reported:
            volume.present = False
            volume.mountpoint = None
    session.flush()
    return disk


def _was_reformatted(volume: Volume, info: VolumeInfo) -> bool:
    """Gleiche Partition, aber neue Dateisystem-UUID: Die Partition wurde neu formatiert."""
    return bool(volume.id and volume.fs_uuid and info.fs_uuid and volume.fs_uuid != info.fs_uuid)


def _reset_index(session: Session, volume: Volume) -> None:
    """Verwirft den (nun ungültigen) Dateiindex, bis der Agent neu indiziert hat."""
    session.execute(delete(FileEntry).where(FileEntry.volume_id == volume.id))
    volume.active_scan_id = None
    volume.file_count = 0
    volume.file_bytes = 0
    volume.indexed_at = None
    volume.index_errors = 0
    volume.index_status = "never"
    volume.used_bytes = volume.free_bytes = None


def _update_volume(volume: Volume, info: VolumeInfo, now: datetime) -> None:
    for attr in _VOLUME_KEEP_FIELDS:
        value = getattr(info, attr)
        if value is not None:
            setattr(volume, attr, value)
    # Belegung nur übernehmen, wenn gemessen – sonst bleibt der letzte bekannte Wert stehen.
    if info.used_bytes is not None and info.free_bytes is not None:
        volume.used_bytes = info.used_bytes
        volume.free_bytes = info.free_bytes
    volume.mountpoint = info.mountpoint
    volume.is_system = info.is_system
    volume.present = True
    volume.last_seen = now


def apply_identity(disk: Disk, family: str | None = None) -> None:
    """Trägt Hersteller und Verkaufsbezeichnung ein, soweit sie sich aus dem Modell ergeben.

    Ein vom Betriebssystem gemeldeter Hersteller wird nicht überschrieben.
    """
    if not disk.model:
        return
    identity = catalog.identify(disk.model, family)
    if identity.vendor and not disk.vendor:
        disk.vendor = identity.vendor
    if identity.product_line:
        disk.product_line = identity.product_line


def backfill_identity(session: Session) -> int:
    """Ergänzt Hersteller/Verkaufsbezeichnung bei bereits erfassten Platten (z. B. offline)."""
    changed = 0
    disks = session.scalars(
        select(Disk).where(Disk.model.is_not(None), Disk.product_line.is_(None))
    ).all()
    for disk in disks:
        raw = session.scalar(
            select(SmartSnapshot.raw_json)
            .where(SmartSnapshot.disk_id == disk.id, SmartSnapshot.raw_json.is_not(None))
            .order_by(SmartSnapshot.taken_at.desc())
            .limit(1)
        )
        try:
            family = json.loads(raw).get("model_family") if raw else None
        except (ValueError, AttributeError):
            family = None
        before = (disk.vendor, disk.product_line)
        apply_identity(disk, family)
        changed += (disk.vendor, disk.product_line) != before
    return changed


def apply_smart(session: Session, disk: Disk, smart: SmartInfo, now: datetime) -> None:
    if not smart.available:
        disk.smart_error = smart.error
        return
    disk.health = smart.health
    disk.smart_passed = smart.passed
    for attr in _SMART_FIELDS:
        setattr(disk, attr, getattr(smart, attr))
    disk.smart_checked_at = now
    disk.smart_error = None
    session.add(
        SmartSnapshot(
            disk=disk,
            taken_at=now,
            health=smart.health,
            passed=smart.passed,
            temperature_c=smart.temperature_c,
            power_on_hours=smart.power_on_hours,
            reallocated_sectors=smart.reallocated_sectors,
            pending_sectors=smart.pending_sectors,
            uncorrectable_sectors=smart.uncorrectable_sectors,
            percentage_used=smart.percentage_used,
            raw_json=json.dumps(smart.raw, separators=(",", ":")) if smart.raw else None,
        )
    )


def mark_connected(
    session: Session, host: str, disk_keys: Iterable[str], now: datetime | None = None,
    user_id: int | None = None,
) -> None:
    """Heartbeat eines Agenten: genau diese Festplatten hängen gerade an `host`.

    Mit `user_id` betrifft das nur Platten dieses Benutzers (der Rechnername ist frei wählbar
    und darf fremde Platten nicht „abstecken“).
    """
    now = now or utcnow()
    keys = list(disk_keys)
    gone = update(Disk).where(
        Disk.last_host == host, Disk.is_connected.is_(True), Disk.disk_key.not_in(keys)
    )
    if user_id is not None:
        gone = gone.where(Disk.owner_user_id == user_id)
    session.execute(gone.values(is_connected=False))
    if keys:
        here = update(Disk).where(Disk.disk_key.in_(keys))
        if user_id is not None:
            here = here.where(Disk.owner_user_id == user_id)
        session.execute(here.values(is_connected=True, last_seen=now, last_host=host))


def get_volume(
    session: Session, disk_key: str, volume_key: str, user_id: int | None = None
) -> Volume:
    """Das Volume; mit `user_id` nur, wenn die Platte ihm gehört (sonst PermissionError)."""
    row = session.execute(
        select(Volume, Disk.owner_user_id)
        .join(Disk)
        .where(Disk.disk_key == disk_key, Volume.volume_key == volume_key)
    ).first()
    if row is None:
        raise LookupError(f"Volume {volume_key!r} auf {disk_key!r} unbekannt")
    volume, owner_user_id = row
    if user_id is not None and owner_user_id != user_id:
        raise PermissionError("Die Platte gehört einem anderen Benutzer.")
    return volume


def begin_index(
    session: Session, disk_key: str, volume_key: str, user_id: int | None = None
) -> str:
    volume = get_volume(session, disk_key, volume_key, user_id)
    volume.index_status = "running"
    return uuid.uuid4().hex


def add_files(
    session: Session, disk_key: str, volume_key: str, scan_id: str, records: Iterable[FileRecord],
    user_id: int | None = None,
) -> int:
    volume = get_volume(session, disk_key, volume_key, user_id)
    rows = [_file_row(volume.id, scan_id, rec) for rec in records]
    if rows:
        session.execute(insert(FileEntry), rows)
    return len(rows)


def finish_index(
    session: Session,
    disk_key: str,
    volume_key: str,
    scan_id: str,
    errors: int = 0,
    success: bool = True,
    now: datetime | None = None,
    user_id: int | None = None,
) -> Volume:
    volume = get_volume(session, disk_key, volume_key, user_id)
    if not success:
        session.execute(
            delete(FileEntry).where(FileEntry.volume_id == volume.id, FileEntry.scan_id == scan_id)
        )
        volume.index_status = "failed"
        return volume
    session.execute(
        delete(FileEntry).where(FileEntry.volume_id == volume.id, FileEntry.scan_id != scan_id)
    )
    count, total = session.execute(
        select(func.count(FileEntry.id), func.coalesce(func.sum(FileEntry.size), 0)).where(
            FileEntry.volume_id == volume.id
        )
    ).one()
    volume.active_scan_id = scan_id
    volume.file_count = count
    volume.file_bytes = total
    volume.index_errors = errors
    volume.indexed_at = now or utcnow()
    volume.index_status = "done"
    return volume


def _file_row(volume_id: int, scan_id: str, rec: FileRecord) -> dict:
    path, size, mtime = rec
    name = path.rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1][1:].lower()[:50] or None
    return {
        "volume_id": volume_id,
        "scan_id": scan_id,
        "path": path,
        "name": name[:1024],
        "extension": ext,
        "size": int(size or 0),
        "mtime": _to_datetime(mtime),
    }


def _to_datetime(timestamp: float | None) -> datetime | None:
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None
