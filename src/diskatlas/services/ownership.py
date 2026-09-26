"""Besitzwechsel, Freigaben und Zuweisung herrenloser Platten."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from diskatlas.db.models import Command, Disk, DiskShare, DiskTransferRequest, Label, User
from diskatlas.services.ingest import utcnow


class OwnershipError(ValueError):
    """Unzulässige Aktion; der Text darf dem Benutzer angezeigt werden."""


def share(session: Session, disk: Disk, viewer: User) -> None:
    """Gibt `disk` lesend an `viewer` frei."""
    if viewer.status != "active":
        raise OwnershipError("Dieser Benutzer ist nicht freigeschaltet.")
    if viewer.id == disk.owner_user_id:
        raise OwnershipError("Die Platte gehört diesem Benutzer bereits.")
    if session.get(DiskShare, (disk.id, viewer.id)) is None:
        session.add(DiskShare(disk_id=disk.id, viewer_user_id=viewer.id))


def unshare(session: Session, disk: Disk, viewer_user_id: int) -> None:
    session.execute(
        delete(DiskShare).where(
            DiskShare.disk_id == disk.id, DiskShare.viewer_user_id == viewer_user_id
        )
    )


def shared_with(session: Session, disk: Disk) -> list[User]:
    stmt = (
        select(User)
        .join(DiskShare, DiskShare.viewer_user_id == User.id)
        .where(DiskShare.disk_id == disk.id)
        .order_by(func.lower(User.nickname))
    )
    return list(session.scalars(stmt))


def pending_transfers(session: Session, user_id: int | None) -> list[DiskTransferRequest]:
    """Offene Übernahmeanträge an den Besitzer `user_id` (None: alle, für den Admin)."""
    stmt = (
        select(DiskTransferRequest)
        .where(DiskTransferRequest.status == "pending")
        .order_by(DiskTransferRequest.id)
    )
    if user_id is not None:
        stmt = stmt.where(DiskTransferRequest.from_user_id == user_id)
    return list(session.scalars(stmt))


def approve_transfer(
    session: Session, request: DiskTransferRequest, now: datetime | None = None
) -> None:
    """Die Platte geht an den Antragsteller. Alles, was der bisherige Besitzer an ihr gepflegt
    hat (Name, Lagerort, Notizen, Labels, Freigaben, offene Aufträge), bleibt bei ihm."""
    now = now or utcnow()
    disk = request.disk
    if request.status != "pending" or disk.owner_user_id != request.from_user_id:
        raise OwnershipError("Der Antrag ist nicht mehr aktuell.")
    disk.owner_user_id = request.to_user_id
    disk.custom_name = disk.location = disk.notes = None
    disk.labels = []
    session.execute(delete(DiskShare).where(DiskShare.disk_id == disk.id))
    session.execute(
        update(Command)
        .where(Command.disk_key == disk.disk_key, Command.status.in_(("pending", "running")))
        .values(status="failed", result="Besitzer der Platte gewechselt", updated_at=now)
    )
    session.execute(  # weitere Anträge auf dieselbe Platte entscheidet nun der neue Besitzer
        update(DiskTransferRequest)
        .where(
            DiskTransferRequest.disk_id == disk.id,
            DiskTransferRequest.status == "pending",
            DiskTransferRequest.id != request.id,
        )
        .values(from_user_id=request.to_user_id)
    )
    request.status = "approved"
    request.resolved_at = now


def reject_transfer(
    session: Session, request: DiskTransferRequest, now: datetime | None = None
) -> None:
    if request.status != "pending":
        raise OwnershipError("Der Antrag ist nicht mehr aktuell.")
    request.status = "rejected"
    request.resolved_at = now or utcnow()


def assign_unowned(session: Session, user: User) -> tuple[int, int]:
    """Weist alle herrenlosen Platten und Labels dem Benutzer zu (Übernahme von Altbestand).

    Gibt (Platten, Labels) zurück. Namensgleiche Labels des Benutzers werden nicht doppelt
    angelegt: Die Platten bekommen dann das vorhandene Label.
    """
    disks = list(session.scalars(select(Disk).where(Disk.owner_user_id.is_(None))))
    for disk in disks:
        disk.owner_user_id = user.id
    labels = list(session.scalars(select(Label).where(Label.owner_user_id.is_(None))))
    mine = session.scalars(select(Label).where(Label.owner_user_id == user.id))
    own = {lab.name: lab for lab in mine}
    adopted = 0
    for label in labels:
        twin = own.get(label.name)
        if twin is None:
            label.owner_user_id = user.id
            own[label.name] = label
            adopted += 1
            continue
        for disk in list(label.disks):
            if twin not in disk.labels:
                disk.labels.append(twin)
        session.delete(label)
    session.flush()
    return len(disks), adopted
