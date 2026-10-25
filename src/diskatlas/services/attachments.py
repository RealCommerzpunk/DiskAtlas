"""Anhänge an Platten (Löschprotokolle als PDF).

Dateien liegen unter Zufallsnamen in einem eigenen Ordner neben der Datenbank; der vom Benutzer
gelieferte Dateiname dient nur der Anzeige. Angenommen werden nur PDFs bis `MAX_BYTES`.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from diskatlas.db.models import Disk, DiskAttachment
from diskatlas.services import pdftext
from diskatlas.services.ingest import utcnow

MAX_BYTES = 20 * 1024 * 1024
PDF_MAGIC = b"%PDF-"
_ID = re.compile(r"^[0-9a-f]{32}$")


class AttachmentError(ValueError):
    """Fehler mit einer für Benutzer lesbaren Meldung."""


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)  # der Ordner entsteht erst beim ersten Anhang

    def file(self, attachment_id: str) -> Path:
        if not _ID.match(attachment_id or ""):
            raise ValueError("ungültige Anhang-ID")
        return self.path / attachment_id

    def write(self, attachment_id: str, data: bytes) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        tmp = self.path / f"{attachment_id}.tmp"
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.file(attachment_id))

    def delete(self, attachment_id: str) -> None:
        with contextlib.suppress(OSError, ValueError):
            self.file(attachment_id).unlink()

    def names(self) -> set[str]:
        if not self.path.is_dir():
            return set()
        return {p.name for p in self.path.iterdir() if _ID.match(p.name)}


STORE: Store | None = None


def configure(store: Store | None) -> None:
    global STORE
    STORE = store


def default_dir(config, database_url: str | None = None) -> Path:
    """Ordner neben der tatsächlich benutzten SQLite-Datenbank (nie ein fremder Datenordner)."""
    if config.server.attachments_dir:
        return Path(config.server.attachments_dir)
    url = database_url or config.effective_database_url
    if url.startswith("sqlite:///") and ":memory:" not in url:
        return Path(url.removeprefix("sqlite:///")).parent / "attachments"
    from diskatlas.config import default_data_dir

    return default_data_dir() / "attachments"


def _store() -> Store:
    if STORE is None:
        raise AttachmentError("Ablage für Anhänge ist nicht eingerichtet.")
    return STORE


def clean_filename(raw: str) -> str:
    """Anzeigename: nur der letzte Pfadteil, ohne Steuerzeichen, höchstens 200 Zeichen, `.pdf`."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in unicodedata.normalize("NFC", name)
                   if unicodedata.category(ch)[0] != "C").strip(" .")
    name = name[:200] or "protokoll"
    return name if name.lower().endswith(".pdf") else f"{name}.pdf"


def check(data: bytes) -> None:
    if len(data) > MAX_BYTES:
        raise AttachmentError(f"Die Datei ist zu groß (höchstens {MAX_BYTES // 1024**2} MB).")
    if not data.startswith(PDF_MAGIC):
        raise AttachmentError("Nur PDF-Dateien können angehängt werden.")


def add(
    session: Session, disk: Disk | None, filename: str, data: bytes, user_id: int | None,
    text: str | None = None, document_date=None,
) -> DiskAttachment:
    """Legt den Anhang an; `disk` None = noch nicht zugeordnet (Sammel-Upload)."""
    check(data)
    if text is None:
        text, document_date = pdftext.read(data)
    attachment = DiskAttachment(
        id=uuid.uuid4().hex, disk_id=disk.id if disk else None, kind="wipe_report",
        filename=clean_filename(filename), size=len(data),
        sha256=hashlib.sha256(data).hexdigest(), uploaded_by_user_id=user_id,
        created_at=utcnow(), document_date=document_date,
        detected_serial=disk.serial if disk and pdftext.mentions(text, disk.serial) else None,
    )
    _store().write(attachment.id, data)
    session.add(attachment)
    try:
        session.flush()
    except Exception:
        _store().delete(attachment.id)
        raise
    return attachment


def matching_disks(text: str, disks: list[Disk]) -> list[Disk]:
    """Platten, deren Seriennummer im PDF-Text vorkommt. Ist eine Seriennummer Teil einer
    längeren, die ebenfalls vorkommt, zählt nur die längere."""
    found = [d for d in disks if pdftext.mentions(text, d.serial)]
    keys = {d.id: pdftext.normalize(d.serial) for d in found}
    return [d for d in found
            if not any(o.id != d.id and keys[d.id] in keys[o.id] and keys[d.id] != keys[o.id]
                       for o in found)]


@dataclass
class BulkResult:
    assigned: list[DiskAttachment] = field(default_factory=list)
    unassigned: list[DiskAttachment] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (Dateiname, Grund)


def add_many(
    session: Session, files: list[tuple[str, bytes]], disks: list[Disk], user_id: int | None,
) -> BulkResult:
    """Sammel-Upload: jedes PDF der Platte zuordnen, deren Seriennummer darin steht.

    `disks` sind die Platten, die der Benutzer ändern darf. Wird keine oder mehr als eine
    gefunden, bleibt das Protokoll offen und wird von Hand zugeordnet."""
    result = BulkResult()
    for name, data in files:
        try:
            check(data)
        except AttachmentError as exc:
            result.rejected.append((clean_filename(name), str(exc)))
            continue
        text, created = pdftext.read(data)
        found = matching_disks(text, disks)
        disk = found[0] if len(found) == 1 else None
        attachment = add(session, disk, name, data, user_id, text, created)
        (result.assigned if disk else result.unassigned).append(attachment)
    return result


def unassigned(session: Session, user_id: int | None) -> list[DiskAttachment]:
    """Offene Protokolle des Benutzers (ohne Anmeldung: alle offenen)."""
    stmt = select(DiskAttachment).where(DiskAttachment.disk_id.is_(None))
    stmt = stmt.where(DiskAttachment.uploaded_by_user_id.is_(None) if user_id is None
                      else DiskAttachment.uploaded_by_user_id == user_id)
    return list(session.scalars(stmt.order_by(DiskAttachment.created_at.desc())))


def assign(attachment: DiskAttachment, disk: Disk) -> None:
    attachment.disk_id = disk.id
    attachment.disk = disk


def for_disk(session: Session, disk_id: int) -> list[DiskAttachment]:
    return list(session.scalars(
        select(DiskAttachment).where(DiskAttachment.disk_id == disk_id)
        .order_by(DiskAttachment.created_at.desc())
    ))


def path_of(attachment: DiskAttachment) -> Path:
    return _store().file(attachment.id)


def remove(session: Session, attachment: DiskAttachment) -> None:
    session.delete(attachment)
    session.flush()
    _store().delete(attachment.id)


def remove_for_disk(session: Session, disk: Disk) -> None:
    """Vor dem Löschen einer Platte: ihre Dateien entfernen (die Zeilen löscht die Datenbank)."""
    for attachment in for_disk(session, disk.id):
        _store().delete(attachment.id)


def purge_orphans(session: Session) -> int:
    """Beim Start: Dateien ohne Datensatz und halb geschriebene Dateien entfernen."""
    store = _store()
    known = set(session.scalars(select(DiskAttachment.id)))
    removed = 0
    for name in store.names() - known:
        store.delete(name)
        removed += 1
    if store.path.is_dir():
        for tmp in store.path.glob("*.tmp"):
            with contextlib.suppress(OSError):
                tmp.unlink()
    return removed
