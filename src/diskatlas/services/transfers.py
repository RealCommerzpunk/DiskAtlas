"""Übertragungsaufträge für Agenten: abholen, Fortschritt, Ergebnis.

Der Server führt nie selbst etwas aus. Ein Agent holt sich Aufträge, die genau für seinen Client
gelten, übernimmt sie atomar (`queued` → `running` mit Lease) und meldet Fortschritt und Ergebnis.
Berechtigungen werden bei jeder Meldung neu geprüft; der Agent leitet Laufwerke und Pfade zusätzlich
aus seinem eigenen Stand ab.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from diskatlas.db.models import Client, CopyItem, CopyRequest
from diskatlas.services import copies
from diskatlas.services.ingest import utcnow

LEASE = timedelta(minutes=10)
MAX_BATCH = 4
MAX_MESSAGE = 500


class TransferError(LookupError):
    """Der Auftrag existiert nicht (für diesen Client) oder ist nicht mehr aktuell."""


def register_capabilities(
    session: Session, client: Client, transfer: bool, pubkey: str | None
) -> None:
    """Heartbeat: darf der Agent Übertragungen ausführen, und mit welchem öffentlichen Schlüssel?"""
    client.transfer_enabled = bool(transfer)
    if pubkey is not None:
        client.pubkey = pubkey[:64] or None


def _job(item: CopyItem, role: str) -> dict:
    req = item.request
    return {
        "id": item.id, "role": role, "phase": item.phase, "name": item.name, "size": item.size,
        "mtime": item.mtime.isoformat() if item.mtime else None,
        "source": {"disk_key": item.source_disk.disk_key,
                   "volume_key": item.source_volume.volume_key, "path": item.source_path},
        "target": {"disk_key": req.target_disk.disk_key,
                   "volume_key": req.target_volume.volume_key if req.target_volume else None,
                   "path": req.target_path},
    }


def allowed(session: Session, item: CopyItem) -> bool:
    """Gilt die Berechtigung des Anfordernden für diese Datei noch?"""
    mode = copies.permission(session, item.request.requester_user_id, item.source_disk)
    return mode in ("own", "always") or (mode == "ask" and item.approved_at is not None)


RECENT = timedelta(minutes=5)  # so frisch muss der Heartbeat des Empfängers sein


def _claim(session: Session, item: CopyItem, client: Client, now: datetime) -> bool:
    claimed = session.execute(
        update(CopyItem)
        .where(CopyItem.id == item.id, CopyItem.state == "queued")
        .values(state="running", lease_until=now + LEASE, attempts=CopyItem.attempts + 1,
                claimed_by_client_id=client.id, updated_at=now, wait_reason=None)
    ).rowcount
    if claimed:
        session.refresh(item)
    return bool(claimed)


def _candidates(session: Session, *conditions, state: str = "queued", limit: int = 16):
    return session.scalars(
        select(CopyItem).join(CopyRequest)
        .where(CopyItem.state == state, *conditions)
        .options(selectinload(CopyItem.request).selectinload(CopyRequest.target_disk),
                 selectinload(CopyItem.request).selectinload(CopyRequest.target_volume),
                 selectinload(CopyItem.request).selectinload(CopyRequest.target_client),
                 selectinload(CopyItem.source_disk), selectinload(CopyItem.source_volume))
        .order_by(CopyItem.created_at).limit(limit)
    ).all()


def _receiver_ready(target: Client | None, now: datetime) -> bool:
    return bool(
        target is not None and target.transfer_enabled and target.pubkey
        and target.last_seen is not None and now - target.last_seen < RECENT
    )


def jobs_for(session: Session, client: Client, now: datetime | None = None) -> list[dict]:
    """Neue Aufträge für `client` (werden übernommen). Nur, wenn der Agent Übertragungen erlaubt.

    Rollen: `local` (Quelle und Ziel an diesem Rechner), `send` (Quelle hier, Ziel woanders: über
    den Relay, höchstens `relay.SLOTS` Dateien gleichzeitig), `receive` (Ziel hier, Sender läuft).
    """
    from diskatlas.services import relay

    now = now or utcnow()
    if not client.transfer_enabled:
        return []
    jobs: list[dict] = []
    for item in _candidates(session, CopyItem.phase == "local",
                            CopyRequest.target_client_id == client.id, limit=MAX_BATCH * 4):
        if len(jobs) >= MAX_BATCH:
            break
        copies.advance(session, item, now)  # Berechtigung und Platten neu prüfen
        if item.state == "queued" and item.phase == "local" and _claim(session, item, client, now):
            jobs.append(_job(item, "local"))
    for item in _candidates(session, CopyItem.phase == "upload",
                            CopyItem.source_disk.has(last_client_id=client.id)):
        if relay.active_uploads(session) >= relay.SLOTS:
            break
        copies.advance(session, item, now)
        if item.state != "queued" or item.phase != "upload":
            continue
        target = item.request.target_client
        if not _receiver_ready(target, now):
            copies.set_state(item, "queued", now, "Der Ziel-Agent nimmt gerade keine Dateien an.")
            continue
        if _claim(session, item, client, now):
            job = _job(item, "send")
            job["target_pubkey"] = target.pubkey
            jobs.append(job)
            break  # eine Datei zur Zeit
    for item in _candidates(session, CopyItem.phase == "upload",
                            CopyRequest.target_client_id == client.id, state="running"):
        if item.claimed_by_client_id is not None and item.claimed_by_client_id != client.id:
            jobs.append(_job(item, "receive"))
    return jobs


def _party(item: CopyItem, client: Client) -> str | None:
    if item.phase == "local":
        return "local" if item.claimed_by_client_id == client.id else None
    if item.claimed_by_client_id == client.id:
        return "sender"
    return "receiver" if item.request.target_client_id == client.id else None


def _mine(session: Session, client: Client, item_id: str, roles: tuple[str, ...]) -> CopyItem:
    item = session.get(CopyItem, item_id)
    if item is None or item.state != "running" or _party(item, client) not in roles:
        raise TransferError("Auftrag nicht gefunden oder nicht mehr aktuell")
    return item


def _end(item: CopyItem, state: str, now: datetime, reason: str | None = None) -> None:
    copies.set_state(item, state, now, reason)  # räumt auch den Relay auf


def progress(
    session: Session, client: Client, item_id: str, bytes_done: int, now: datetime | None = None
) -> dict:
    """Lease verlängern. `abort`: der Agent soll aufhören (abgebrochen oder nicht mehr erlaubt)."""
    now = now or utcnow()
    try:
        item = _mine(session, client, item_id, ("local", "sender", "receiver"))
    except TransferError:
        return {"abort": True}
    if item.request.cancelled_at is not None or not allowed(session, item):
        _end(item, "cancelled", now, "Abgebrochen (zurückgezogen oder nicht erlaubt).")
        return {"abort": True}
    item.bytes_done = max(0, min(int(bytes_done), item.size if item.size else int(bytes_done)))
    item.lease_until = now + LEASE
    item.updated_at = now
    return {"abort": False}


def finish(
    session: Session, client: Client, item_id: str, sha256: str, size: int, result_name: str,
    message: str = "", now: datetime | None = None,
) -> None:
    """Fertig melden: der Agent, der die Datei geschrieben hat (lokal, sonst der Empfänger)."""
    now = now or utcnow()
    item = _mine(session, client, item_id, ("local", "receiver"))
    item.sha256 = sha256[:64]
    item.bytes_done = max(0, int(size))
    item.result_name = result_name.replace("\\", "/").rsplit("/", 1)[-1][:1024] or item.name
    item.message = message[:MAX_MESSAGE] or None
    _end(item, "done", now)


def fail(
    session: Session, client: Client, item_id: str, message: str, retry: bool = False,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    item = _mine(session, client, item_id, ("local", "sender", "receiver"))
    item.message = message[:MAX_MESSAGE]
    if retry and item.attempts < copies.MAX_ATTEMPTS:
        item.claimed_by_client_id = None
        _end(item, "queued", now, "Neuer Versuch folgt.")
    else:
        _end(item, "failed", now, message[:MAX_MESSAGE])
