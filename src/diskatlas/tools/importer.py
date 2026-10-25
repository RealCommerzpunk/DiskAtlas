"""Altbestand importieren: eine andere DiskAtlas-Datenbank in die laufende einfügen.

Anders als `db copy` (ganze, leere Zieldatenbank) fügt der Import die Platten einer alten
Datenbank – z. B. aus dem früheren Betrieb „nur dieser PC“ – samt Partitionen, Dateiindex,
SMART-Verlauf, eigenen Angaben und Labels in die bestehende ein und gibt sie einem Benutzer.

* Die Quelle wird nie verändert: Sie wird per SQLite-Backup kopiert (auch mit WAL) und die Kopie
  auf das aktuelle Schema gebracht.
* Platten, die das Ziel schon kennt, werden übersprungen (der neuere Stand bleibt).
* Geschrieben wird in kleinen Transaktionen, damit Agenten nicht warten müssen. Eine Platte wird
  erst am Ende „scharf“ geschaltet (Dateiindex aktiv); bricht der Import ab, setzt ein erneuter
  Aufruf bei dieser Platte wieder an.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from diskatlas.db import Database
from diskatlas.db.models import (
    Disk,
    FileEntry,
    Label,
    Setting,
    SmartSnapshot,
    User,
    Volume,
    disk_labels,
)
from diskatlas.services import emptylabel
from diskatlas.services import labels as label_service

log = logging.getLogger(__name__)

BATCH = 10_000
SQLITE_MAGIC = b"SQLite format 3\x00"
MARKER = "import_incomplete:"  # Settings-Schlüssel: diese Platte wird gerade importiert

# Diese Spalten bestimmt der Import selbst (Zuordnung im Ziel), sie werden nicht übernommen.
_DISK_SKIP = {"id", "owner_user_id", "last_client_id", "empty_label_id", "is_connected"}
_VOLUME_SKIP = {"id", "disk_id", "active_scan_id", "dirs_scan_id", "pending_scan_id",
                "index_touched_at", "index_status"}
_SMART_SKIP = {"id", "disk_id"}


class ImportError_(RuntimeError):  # noqa: N801 - eigener Name, um Pythons ImportError nicht zu verdecken
    """Fehler mit einer für Benutzer lesbaren Meldung."""


@dataclass
class ImportReport:
    disks: int = 0
    files: int = 0
    smart: int = 0
    labels_created: int = 0
    skipped: list[str] = field(default_factory=list)  # schon vorhandene Platten

    def summary(self) -> str:
        text = (f"{self.disks} Platte(n) mit {self.files:,} Dateien importiert"
                .replace(",", "."))
        if self.labels_created:
            text += f", {self.labels_created} Label(s) angelegt"
        if self.skipped:
            text += f"; {len(self.skipped)} schon vorhanden und übersprungen"
        return text + "."


def check_file(path: Path) -> None:
    try:
        with open(path, "rb") as handle:
            head = handle.read(len(SQLITE_MAGIC))
    except OSError as exc:
        raise ImportError_(f"Datei nicht lesbar: {exc}") from exc
    if head != SQLITE_MAGIC:
        raise ImportError_("Das ist keine SQLite-Datenbank (DiskAtlas-Datei *.db erwartet).")


def _snapshot(source: Path, workdir: Path) -> Path:
    """Konsistente Kopie der Quelle (SQLite-Backup, berücksichtigt WAL), nur lesend geöffnet."""
    target = workdir / "quelle.db"
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        if src.execute("select count(*) from sqlite_master where name='disks'").fetchone()[0] != 1:
            raise ImportError_("Die Datei enthält keine DiskAtlas-Daten (Tabelle „disks“ fehlt).")
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    except sqlite3.DatabaseError as exc:
        raise ImportError_(f"Die Datenbank ist beschädigt oder nicht lesbar: {exc}") from exc
    finally:
        src.close()
    return target


def _row(obj, skip: set[str]) -> dict:
    table = obj.__table__
    return {c.name: getattr(obj, c.key) for c in table.columns if c.name not in skip}


def import_database(
    target: Database, source_path: str | Path, owner_nickname: str,
    progress: Callable[[str], None] = lambda _msg: None,
) -> ImportReport:
    """Fügt die Platten aus `source_path` in `target` ein und gibt sie `owner_nickname`
    (ohne Benutzerverwaltung, also im lokalen Betrieb: "")."""
    source_path = Path(source_path)
    check_file(source_path)
    workdir = Path(tempfile.mkdtemp(prefix="diskatlas-import-"))
    try:
        copy = _snapshot(source_path, workdir)
        progress("Quelle wird auf das aktuelle Schema gebracht …")
        source = Database(f"sqlite:///{copy.as_posix()}")
        try:
            source.upgrade()
            return _import(target, source, owner_nickname, progress)
        finally:
            source.dispose()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _owner(session: Session, nickname: str) -> int | None:
    has_users = session.scalar(select(func.count()).select_from(User)) > 0
    if not has_users:
        return None
    from diskatlas.services.users import find_user

    user = find_user(session, nickname) if nickname else None
    if user is None or user.status != "active":
        raise ImportError_(f"Benutzer „{nickname}“ nicht gefunden oder nicht freigeschaltet.")
    return user.id


def _import(target: Database, source: Database, nickname: str, progress) -> ImportReport:
    report = ImportReport()
    with target.session() as dst:
        owner_id = _owner(dst, nickname)
    with source.session() as src:
        src_disks = list(src.scalars(select(Disk).order_by(Disk.id)))
        for number, disk in enumerate(src_disks, start=1):
            progress(f"Platte {number}/{len(src_disks)}: {disk.display_name}")
            with target.session() as dst:
                existing = dst.scalar(select(Disk).where(Disk.disk_key == disk.disk_key))
                resume = existing is not None and dst.get(Setting, MARKER + disk.disk_key)
                if existing is not None and not resume:
                    report.skipped.append(disk.display_name)
                    continue
                if existing is None:
                    existing = Disk(**_row(disk, _DISK_SKIP), owner_user_id=owner_id,
                                    is_connected=False)
                    dst.add(existing)
                    dst.add(Setting(key=MARKER + disk.disk_key, value="1"))
                    dst.flush()
                    report.labels_created += _copy_labels(dst, disk, existing, owner_id)
                    report.smart += _copy_smart(src, dst, disk, existing)
                disk_id = existing.id
            report.files += _copy_volumes(src, target, disk, disk_id, progress)
            with target.session() as dst:
                dst.execute(delete(Setting).where(Setting.key == MARKER + disk.disk_key))
                emptylabel.apply(dst, dst.get(Disk, disk_id))
            report.disks += 1
    progress(report.summary())
    return report


def _copy_labels(dst: Session, disk: Disk, new: Disk, owner_id: int | None) -> int:
    created = 0
    for label in disk.labels:
        own = label_service.find(dst, owner_id, label.name)
        if own is None:
            own = Label(owner_user_id=owner_id, name=label.name, category=label.category,
                        color=label.color)
            dst.add(own)
            dst.flush()
            created += 1
        dst.execute(insert(disk_labels).values(disk_id=new.id, label_id=own.id))
    return created


def _copy_smart(src: Session, dst: Session, disk: Disk, new: Disk) -> int:
    rows = [{**_row(s, _SMART_SKIP), "disk_id": new.id}
            for s in src.scalars(select(SmartSnapshot).where(SmartSnapshot.disk_id == disk.id))]
    if rows:
        dst.execute(insert(SmartSnapshot), rows)
    return len(rows)


def _copy_volumes(src: Session, target: Database, disk: Disk, disk_id: int, progress) -> int:
    total = 0
    for volume in src.scalars(select(Volume).where(Volume.disk_id == disk.id)):
        with target.session() as dst:
            new = dst.scalar(select(Volume).where(Volume.disk_id == disk_id,
                                                  Volume.volume_key == volume.volume_key))
            if new is None:
                new = Volume(**_row(volume, _VOLUME_SKIP), disk_id=disk_id,
                             index_status="never")
                dst.add(new)
                dst.flush()
            elif new.active_scan_id:
                continue  # schon vollständig übernommen (Import wird fortgesetzt)
            # Reste eines abgebrochenen Imports dieser Partition entfernen
            dst.execute(delete(FileEntry).where(FileEntry.volume_id == new.id))
            volume_id = new.id
        if not volume.active_scan_id:
            continue
        scan_id = uuid.uuid4().hex
        copied = 0
        stmt = (select(FileEntry.path, FileEntry.parent, FileEntry.name, FileEntry.extension,
                       FileEntry.size, FileEntry.mtime)
                .where(FileEntry.volume_id == volume.id,
                       FileEntry.scan_id == volume.active_scan_id)
                .order_by(FileEntry.id).execution_options(yield_per=BATCH))
        batch: list[dict] = []
        for row in src.execute(stmt).mappings():
            batch.append({**row, "volume_id": volume_id, "scan_id": scan_id})
            if len(batch) >= BATCH:
                copied += _flush(target, batch)
                batch = []
                progress(f"{disk.display_name}: {copied:,} Dateien".replace(",", "."))
        if batch:
            copied += _flush(target, batch)
        with target.session() as dst:  # erst jetzt ist der Index der Partition aktiv
            new = dst.get(Volume, volume_id)
            new.active_scan_id = scan_id
            new.index_status = "done"
            new.file_count = copied
            new.file_bytes = dst.scalar(select(func.coalesce(func.sum(FileEntry.size), 0))
                                        .where(FileEntry.volume_id == volume_id)) or 0
        total += copied
    return total


def _flush(target: Database, rows: list[dict]) -> int:
    with target.session() as dst:
        dst.execute(insert(FileEntry), rows)
    return len(rows)


# ------------------------------------------------------------------ Hintergrund (Weboberfläche)
@dataclass
class ImportJob:
    state: str = "idle"  # idle | running | done | failed
    message: str = ""
    started_by: str = ""


JOB = ImportJob()
_LOCK = threading.Lock()


def start_background(target: Database, path: Path, owner: str, started_by: str) -> bool:
    """Startet den Import in einem eigenen Thread; False, wenn schon einer läuft."""
    with _LOCK:
        if JOB.state == "running":
            return False
        JOB.state, JOB.message, JOB.started_by = "running", "Import startet …", started_by

    def run() -> None:
        def say(msg: str) -> None:
            JOB.message = msg

        try:
            report = import_database(target, path, owner, say)
            JOB.state, JOB.message = "done", report.summary()
        except ImportError_ as exc:
            JOB.state, JOB.message = "failed", str(exc)
        except Exception as exc:  # unerwartet: protokollieren, nicht den Server stören
            log.exception("Import fehlgeschlagen")
            JOB.state, JOB.message = "failed", f"Unerwarteter Fehler: {exc}"
        finally:  # samt Begleitdateien, die SQLite beim Lesen anlegt
            for leftover in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
                leftover.unlink(missing_ok=True)

    threading.Thread(target=run, name="diskatlas-import", daemon=True).start()
    return True
