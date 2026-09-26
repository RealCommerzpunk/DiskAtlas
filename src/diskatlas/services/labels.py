"""Labels gehören einem Benutzer; Namen sind je Besitzer eindeutig."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from diskatlas.db.models import Label


def find(
    session: Session, owner_user_id: int | None, name: str, except_id: int | None = None
) -> Label | None:
    stmt = select(Label).where(
        Label.owner_user_id.is_(None) if owner_user_id is None
        else Label.owner_user_id == owner_user_id,
        Label.name == name,
    )
    if except_id is not None:
        stmt = stmt.where(Label.id != except_id)
    return session.scalar(stmt)


def name_taken(
    session: Session, owner_user_id: int | None, name: str, except_id: int | None = None
) -> bool:
    """Bei fehlendem Besitzer (lokaler Betrieb) prüft das die Datenbank nicht, deshalb hier."""
    return find(session, owner_user_id, name, except_id) is not None
