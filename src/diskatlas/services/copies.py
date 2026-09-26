"""„Datei anfordern“: Auswahl auflösen, Berechtigung prüfen, Anfragen und ihre Zustände.

Zustände je Datei (`CopyItem.state`):
`waiting_approval` (Besitzer soll zustimmen) → `waiting_disk` (Quelle oder Ziel nicht angeschlossen)
→ `queued` (bereit für den Agenten) → `running` → `done`; Endzustände außerdem `failed`, `denied`,
`cancelled`, `expired`. `advance` ist idempotent und prüft die Berechtigung jedes Mal neu.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from diskatlas.db.models import (
    Client,
    ClientCopyTarget,
    CopyItem,
    CopyRequest,
    Disk,
    DiskShare,
    FileEntry,
    Volume,
)
from diskatlas.services import authz, browse
from diskatlas.services.authz import Viewer
from diskatlas.services.ingest import utcnow
from diskatlas.services.queries import system_disk_ids

MAX_FILES = 2000
ASK_TTL = timedelta(days=7)  # so lange darf der Besitzer sich Zeit lassen
WAIT_TTL = timedelta(days=14)  # so lange darf eine Datei auf eine Platte warten
MAX_ATTEMPTS = 3

OPEN_STATES = ("waiting_approval", "waiting_disk", "queued", "running")
FINAL_STATES = ("done", "failed", "denied", "cancelled", "expired")
STATE_TEXT = {
    "waiting_approval": "wartet auf Zustimmung",
    "waiting_disk": "wartet auf Platte",
    "queued": "bereit",
    "running": "wird kopiert",
    "done": "fertig",
    "failed": "fehlgeschlagen",
    "denied": "nicht erlaubt",
    "cancelled": "abgebrochen",
    "expired": "abgelaufen",
}


class CopyError(ValueError):
    """Fehler mit einer für Benutzer lesbaren Meldung."""


@dataclass
class Source:
    volume: Volume
    disk: Disk
    path: str
    name: str
    size: int
    mtime: datetime | None


@dataclass
class Selection:
    files: list[Source] = field(default_factory=list)
    folders: int = 0

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


# ------------------------------------------------------------------ Berechtigung
def permission(session: Session, requester_user_id: int, disk: Disk) -> str:
    """`own`, `always`, `ask` oder `never` – wie der Anfordernde diese Platte kopieren darf.

    Der Admin hat hier keinen Sonderstatus: fremde Platten kopiert er nur mit Freigabe.
    """
    if disk.owner_user_id is None:
        return "never"
    if disk.owner_user_id == requester_user_id:
        return "own"
    share = session.get(DiskShare, (disk.id, requester_user_id))
    return share.copy_mode if share is not None else "never"


# ------------------------------------------------------------------ Auswahl
def parse_selection(raw: list[str]) -> list[tuple[str, int, str]]:
    """`d:<volume>:<pfad>` / `f:<volume>:<pfad>` → (Art, Volume-ID, normalisierter Pfad)."""
    out = []
    for item in raw[:5000]:
        kind, _, rest = item.partition(":")
        vol, _, path = rest.partition(":")
        norm = browse.normalize_path(path)
        if kind not in ("f", "d") or not vol.isdigit() or norm is None:
            raise CopyError("Ungültige Auswahl.")
        if kind == "f" and not norm:
            raise CopyError("Ungültige Auswahl.")
        out.append((kind, int(vol), norm))
    return out


def _like_prefix(path: str) -> str:
    return path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"


def resolve(session: Session, viewer: Viewer, raw: list[str]) -> Selection:
    """Löst die Auswahl zu Einzeldateien des aktiven Index auf (nur sichtbare Platten)."""
    visible = authz.visible_ids(viewer)
    selection = Selection()
    seen: set[tuple[int, str]] = set()
    for kind, volume_id, path in parse_selection(raw):
        volume = session.get(Volume, volume_id)
        disk = session.get(Disk, volume.disk_id) if volume else None
        if volume is None or disk is None or not volume.active_scan_id:
            raise CopyError("Auswahl nicht gefunden.")
        if visible is not None and disk.id not in set(session.scalars(visible)):
            raise CopyError("Auswahl nicht gefunden.")  # unsichtbar = nicht vorhanden
        stmt = select(FileEntry).where(
            FileEntry.volume_id == volume.id, FileEntry.scan_id == volume.active_scan_id
        )
        if kind == "f":
            stmt = stmt.where(FileEntry.path == path)
        else:
            selection.folders += 1
            if path:
                stmt = stmt.where(or_(
                    FileEntry.parent == path, FileEntry.parent.like(_like_prefix(path), escape="\\")
                ))
        for entry in session.scalars(stmt.order_by(FileEntry.path).limit(MAX_FILES + 1)):
            if (volume.id, entry.path) in seen:
                continue
            seen.add((volume.id, entry.path))
            selection.files.append(
                Source(volume, disk, entry.path, entry.name, entry.size, entry.mtime)
            )
            if len(selection.files) > MAX_FILES:
                raise CopyError(
                    f"Zu viele Dateien auf einmal (mehr als {MAX_FILES}). "
                    "Bitte in Teilen anfordern."
                )
    if not selection.files:
        raise CopyError("Nichts ausgewählt (leere Ordner haben nichts zu kopieren).")
    return selection


@dataclass
class DiskSummary:
    disk: Disk
    mode: str  # own | always | ask | never
    count: int = 0
    size: int = 0


def summarize(session: Session, requester_user_id: int, selection: Selection) -> list[DiskSummary]:
    by_disk: dict[int, DiskSummary] = {}
    for src in selection.files:
        row = by_disk.get(src.disk.id)
        if row is None:
            row = by_disk[src.disk.id] = DiskSummary(
                src.disk, permission(session, requester_user_id, src.disk)
            )
        row.count += 1
        row.size += src.size
    return list(by_disk.values())


# ------------------------------------------------------------------ Ziel
@dataclass
class TargetChoice:
    client: Client
    disk: Disk
    volume: Volume


def target_choices(session: Session, user_id: int) -> list[TargetChoice]:
    """Mögliche Ziele des Benutzers: angeschlossene, eigene Nicht-System-Platten seiner Clients."""
    system = set(system_disk_ids(session))
    clients = {c.id: c for c in session.scalars(select(Client).where(Client.user_id == user_id))}
    if not clients:
        return []
    disks = session.scalars(
        select(Disk).where(
            Disk.owner_user_id == user_id, Disk.is_connected.is_(True),
            Disk.last_client_id.in_(list(clients)),
        ).options(selectinload(Disk.volumes)).order_by(Disk.id)
    )
    out = []
    for disk in disks:
        if disk.id in system:
            continue
        for volume in disk.present_volumes:
            if volume.mountpoint and not volume.is_system:
                out.append(TargetChoice(clients[disk.last_client_id], disk, volume))
    return out


def default_target(session: Session, client_id: int) -> ClientCopyTarget | None:
    return session.get(ClientCopyTarget, client_id)


def save_default_target(
    session: Session, client_id: int, disk_id: int, volume_id: int, path: str
) -> None:
    row = session.get(ClientCopyTarget, client_id)
    if row is None:
        row = ClientCopyTarget(client_id=client_id)
        session.add(row)
    row.disk_id, row.volume_id, row.path, row.updated_at = disk_id, volume_id, path, utcnow()


# ------------------------------------------------------------------ Anlegen
def create_request(
    session: Session, requester_user_id: int, raw_selection: list[str], viewer: Viewer,
    client_id: int, volume_id: int, target_path: str, remember: bool = False,
    now: datetime | None = None,
) -> CopyRequest:
    now = now or utcnow()
    path = browse.normalize_path(target_path)
    if path is None:
        raise CopyError("Der Zielordner ist ungültig.")
    choice = next((c for c in target_choices(session, requester_user_id)
                   if c.client.id == client_id and c.volume.id == volume_id), None)
    if choice is None:
        raise CopyError("Das Ziel ist nicht (mehr) verfügbar. Es muss eine angeschlossene eigene "
                        "Platte eines deiner Clients sein.")
    selection = resolve(session, viewer, raw_selection)
    request = CopyRequest(
        id=uuid.uuid4().hex, requester_user_id=requester_user_id, target_client_id=client_id,
        target_disk_id=choice.disk.id, target_volume_id=volume_id, target_path=path,
        created_at=now,
    )
    session.add(request)
    active = _active_keys(session, requester_user_id, client_id, volume_id, path)
    for src in selection.files:
        if (src.volume.id, src.path) in active:
            continue  # schon angefordert und noch nicht abgeschlossen
        item = CopyItem(
            id=uuid.uuid4().hex, source_disk_id=src.disk.id, source_volume_id=src.volume.id,
            source_path=src.path, name=src.name, size=src.size, mtime=src.mtime,
            owner_user_id=src.disk.owner_user_id, state="waiting_disk", created_at=now,
            updated_at=now,
        )
        item.source_disk, item.source_volume = src.disk, src.volume
        item.request = request  # hängt sie über die Gegenbeziehung an die Anfrage
        session.add(item)
        advance(session, item, now)
    if not request.items:
        session.expunge(request)
        raise CopyError("Alle ausgewählten Dateien sind für dieses Ziel schon angefordert.")
    if remember:
        save_default_target(session, client_id, choice.disk.id, volume_id, path)
    return request


def _active_keys(session, user_id, client_id, volume_id, path) -> set[tuple[int, str]]:
    rows = session.execute(
        select(CopyItem.source_volume_id, CopyItem.source_path)
        .join(CopyRequest)
        .where(
            CopyRequest.requester_user_id == user_id, CopyRequest.target_client_id == client_id,
            CopyRequest.target_volume_id == volume_id, CopyRequest.target_path == path,
            CopyItem.state.in_(OPEN_STATES),
        )
    )
    return {(v, p) for v, p in rows}


# ------------------------------------------------------------------ Zustandsmaschine
def set_state(item: CopyItem, state: str, now: datetime, reason: str | None = None) -> None:
    item.state = state
    item.wait_reason = reason
    item.updated_at = now
    if state in FINAL_STATES:
        item.lease_until = None


def advance(session: Session, item: CopyItem, now: datetime | None = None) -> None:
    """Bringt eine noch nicht laufende Datei in den Zustand, der zur Lage jetzt passt."""
    now = now or utcnow()
    if item.state not in ("waiting_approval", "waiting_disk", "queued"):
        return
    request = item.request
    if request.cancelled_at is not None:
        return set_state(item, "cancelled", now)
    if item.expires_at is not None and item.expires_at < now:
        return set_state(item, "expired", now, "Nicht rechtzeitig beantwortet oder Platte fehlte.")
    mode = permission(session, request.requester_user_id, item.source_disk)
    if mode == "never":
        return set_state(item, "denied", now, "Keine Kopierberechtigung (mehr) für diese Platte.")
    if mode == "ask" and item.approved_at is None:
        item.expires_at = item.expires_at or now + ASK_TTL
        return set_state(item, "waiting_approval", now, "Der Besitzer muss zustimmen.")
    if item.expires_at is None or item.state == "waiting_approval":
        item.expires_at = now + WAIT_TTL
    source, target = item.source_disk, request.target_disk
    if not source.is_connected or not target.is_connected:
        reason = "Quellplatte nicht angeschlossen" if not source.is_connected \
            else "Zielplatte nicht angeschlossen"
        return set_state(item, "waiting_disk", now, reason)
    item.phase = "local" if source.last_client_id == request.target_client_id else "upload"
    set_state(item, "queued", now)


def sweep(session: Session, now: datetime | None = None) -> int:
    """Alle offenen, nicht laufenden Dateien neu bewerten (bei Heartbeat/Seitenaufruf); Anzahl."""
    now = now or utcnow()
    items = session.scalars(
        select(CopyItem).where(CopyItem.state.in_(("waiting_approval", "waiting_disk", "queued")))
        .options(selectinload(CopyItem.request).selectinload(CopyRequest.target_disk),
                 selectinload(CopyItem.source_disk))
    ).all()
    for item in items:
        advance(session, item, now)
    notify_missing_disks(session, now)
    for item in session.scalars(select(CopyItem).where(
            CopyItem.state == "running", CopyItem.lease_until.is_not(None),
            CopyItem.lease_until < now)):
        item.attempts += 1
        if item.attempts >= MAX_ATTEMPTS:
            set_state(item, "failed", now, "Der Agent hat die Übertragung nicht abgeschlossen.")
        else:
            set_state(item, "queued", now)
    return len(items)


# ------------------------------------------------------------------ Platten anschließen
NOTICE_INTERVAL = timedelta(hours=24)


@dataclass
class MissingDisk:
    disk: Disk
    owner_user_id: int | None
    count: int = 0
    requesters: set[str] = field(default_factory=set)


def missing_disks(session: Session) -> list[MissingDisk]:
    """Platten, die nicht angeschlossen sind und auf die wartende Dateien warten."""
    items = session.scalars(
        select(CopyItem).where(CopyItem.state == "waiting_disk")
        .options(selectinload(CopyItem.request).selectinload(CopyRequest.requester),
                 selectinload(CopyItem.request).selectinload(CopyRequest.target_disk),
                 selectinload(CopyItem.source_disk))
    )
    found: dict[int, MissingDisk] = {}
    for item in items:
        who = item.request.requester
        for disk, owner in ((item.source_disk, item.source_disk.owner_user_id),
                            (item.request.target_disk, who.id)):
            if disk.is_connected:
                continue
            row = found.setdefault(disk.id, MissingDisk(disk, owner))
            row.count += 1
            row.requesters.add(who.nickname)
    return list(found.values())


def notify_missing_disks(session: Session, now: datetime | None = None) -> int:
    """Bittet die Besitzer per Hinweis an ihren Agenten, fehlende Platten anzuschließen
    (höchstens ein Hinweis je Platte und 24 Stunden)."""
    from diskatlas.db.models import Command
    from diskatlas.services import commands

    now = now or utcnow()
    sent = 0
    for row in missing_disks(session):
        disk = row.disk
        if row.owner_user_id is None or not disk.last_host:
            continue
        recent = session.scalar(select(Command.id).where(
            Command.kind == "notice", Command.disk_key == disk.disk_key,
            Command.created_at > now - NOTICE_INTERVAL,
        ).limit(1))
        if recent is not None:
            continue
        text = f"Bitte Platte „{disk.display_name}“ anschließen: {row.count} Datei(en) warten."
        commands.enqueue(session, disk.last_host, "notice", {"text": text},
                         disk_key=disk.disk_key, user_id=row.owner_user_id, now=now)
        sent += 1
    return sent


# ------------------------------------------------------------------ Entscheidungen
def decide(
    session: Session, owner_user_id: int, item_ids: list[str], approve: bool,
    now: datetime | None = None,
) -> int:
    """Der Plattenbesitzer stimmt zu oder lehnt ab. Nur er, auch der Admin nicht. Anzahl."""
    now = now or utcnow()
    done = 0
    for item in session.scalars(select(CopyItem).where(CopyItem.id.in_(item_ids[:5000]))):
        if item.state != "waiting_approval" or item.source_disk.owner_user_id != owner_user_id:
            continue
        if approve:
            item.approved_by_user_id, item.approved_at = owner_user_id, now
            item.expires_at = now + WAIT_TTL
            advance(session, item, now)
        else:
            set_state(item, "denied", now, "Der Besitzer hat abgelehnt.")
        done += 1
    return done


def cancel(session: Session, request: CopyRequest, now: datetime | None = None) -> None:
    now = now or utcnow()
    request.cancelled_at = request.cancelled_at or now
    for item in request.items:
        if item.state in ("waiting_approval", "waiting_disk", "queued", "running"):
            set_state(item, "cancelled", now, "Vom Anfordernden abgebrochen.")


# ------------------------------------------------------------------ Abfragen
def requests_of(session: Session, user_id: int) -> list[CopyRequest]:
    return list(session.scalars(
        select(CopyRequest).where(CopyRequest.requester_user_id == user_id)
        .options(selectinload(CopyRequest.items)).order_by(CopyRequest.created_at.desc())
    ))


def get_request(session: Session, request_id: str) -> CopyRequest | None:
    return session.scalar(
        select(CopyRequest).where(CopyRequest.id == request_id)
        .options(selectinload(CopyRequest.items))
    )


def approvals_for(session: Session, owner_user_id: int) -> list[CopyItem]:
    """Dateien, über deren Freigabe der Benutzer als Plattenbesitzer entscheiden soll."""
    return list(session.scalars(
        select(CopyItem).join(Disk, Disk.id == CopyItem.source_disk_id)
        .where(CopyItem.state == "waiting_approval", Disk.owner_user_id == owner_user_id)
        .options(selectinload(CopyItem.request).selectinload(CopyRequest.requester),
                 selectinload(CopyItem.source_disk))
        .order_by(CopyItem.created_at)
    ))


def disks_to_connect(session: Session, user_id: int) -> list[MissingDisk]:
    """Platten des Benutzers, die er anschließen soll, weil Dateien darauf oder dahin warten."""
    return [m for m in missing_disks(session) if m.owner_user_id == user_id]


def pending_approval_count(session: Session, owner_user_id: int) -> int:
    """Offene Aufgaben im Menü: Zustimmungen und anzuschließende Platten."""
    return (len(approvals_for(session, owner_user_id))
            + len(disks_to_connect(session, owner_user_id)))


def counts(request: CopyRequest) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in request.items:
        out[item.state] = out.get(item.state, 0) + 1
    return out


