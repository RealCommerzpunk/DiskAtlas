"""FastAPI-Abhängigkeiten (Session, Authentifizierung)."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from diskatlas.db.models import Client, User


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.db.session() as session:
        yield session


def require_client(request: Request, session: Session = Depends(get_session)) -> Client | None:
    """Der Client, dessen Token die Anfrage trägt (die Middleware hat es schon geprüft).

    Ohne Anmeldung (lokaler Betrieb) gibt es keine Clients: dann None.
    """
    if not request.app.state.auth_enabled:
        return None
    identity = getattr(request.state, "identity", None)
    client = session.get(Client, identity.client_id) if identity and identity.client_id else None
    if client is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungültiges oder fehlendes Client-Token")
    return client


def get_current_user(request: Request, session: Session = Depends(get_session)) -> User:
    """Angemeldeter Benutzer (Sitzung oder Client-Token)."""
    if not request.app.state.auth_enabled:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Ohne Anmeldung gibt es keine Benutzerverwaltung."
        )
    identity = getattr(request.state, "identity", None)
    user = session.get(User, identity.user_id) if identity else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Anmeldung erforderlich")
    return user


def require_master(user: User = Depends(get_current_user)) -> User:
    if not user.is_master:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nur für den Master.")
    return user
