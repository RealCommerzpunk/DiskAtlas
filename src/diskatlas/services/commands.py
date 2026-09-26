"""Aufträge vom Server an Agenten. Der Server führt nie selbst etwas auf Platten aus: Er legt einen
Auftrag ab, der zuständige Agent holt ihn ab, prüft ihn gegen seinen eigenen Stand und meldet das
Ergebnis zurück."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from diskatlas.db.models import Command
from diskatlas.services.ingest import utcnow

KINDS = {"rename_label", "rescan", "notice"}
STALE_RUNNING_SECONDS = 600  # ein hängengebliebener „läuft“-Auftrag wird nicht endlos angezeigt


def enqueue(
    session: Session, host: str, kind: str, payload: dict, disk_key: str | None = None,
    now: datetime | None = None, user_id: int | None = None,
) -> Command:
    """`user_id`: Benutzer, dessen Client den Auftrag ausführen darf (Besitzer der Platte)."""
    if kind not in KINDS:
        raise ValueError(f"Unbekannter Auftrag: {kind}")
    now = now or utcnow()
    command = Command(
        host=host, kind=kind, payload=json.dumps(payload), status="pending",
        disk_key=disk_key, user_id=user_id, created_at=now, updated_at=now,
    )
    session.add(command)
    session.flush()
    return command


def claim_pending(
    session: Session, host: str, now: datetime | None = None, user_id: int | None = None
) -> list[dict]:
    """Gibt die offenen Aufträge des Rechners aus und markiert sie als „läuft“.

    Mit `user_id` (Client eines Benutzers) nur dessen Aufträge – der Rechnername allein
    berechtigt nicht, er ist frei wählbar.
    """
    now = now or utcnow()
    stmt = (
        select(Command)
        .where(Command.host == host, Command.status == "pending")
        .order_by(Command.id)
    )
    if user_id is not None:
        stmt = stmt.where(Command.user_id == user_id)
    rows = session.scalars(stmt).all()
    result = []
    for command in rows:
        command.status = "running"
        command.updated_at = now
        payload = json.loads(command.payload)
        result.append({"id": command.id, "kind": command.kind, "payload": payload})
    return result


def finish(
    session: Session, command_id: int, ok: bool, message: str = "", now: datetime | None = None,
    user_id: int | None = None,
) -> None:
    command = session.get(Command, command_id)
    if command is None or (user_id is not None and command.user_id != user_id):
        raise LookupError(f"Auftrag {command_id} nicht gefunden")
    command.status = "done" if ok else "failed"
    command.result = message[:1000]
    command.updated_at = now or utcnow()


def recent(
    session: Session, disk_key: str | None = None, limit: int = 5, user_id: int | None = None
) -> list[Command]:
    stmt = select(Command).order_by(Command.id.desc()).limit(limit)
    if disk_key:
        stmt = stmt.where(Command.disk_key == disk_key)
    if user_id is not None:
        stmt = stmt.where(Command.user_id == user_id)
    return list(session.scalars(stmt))


def open_count(session: Session, user_id: int | None = None) -> int:
    stmt = select(Command.id).where(Command.status.in_(("pending", "running")))
    if user_id is not None:
        stmt = stmt.where(Command.user_id == user_id)
    return len(session.scalars(stmt).all())
