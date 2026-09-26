"""FastAPI-Abhängigkeiten (Session, Authentifizierung)."""

from __future__ import annotations

import secrets
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from diskatlas.db.models import User

bearer = HTTPBearer(auto_error=False)


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.db.session() as session:
        yield session


def require_ingest_token(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> None:
    expected = request.app.state.config.server.api_token
    if not expected:
        return
    supplied = credentials.credentials if credentials else ""
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungültiges oder fehlendes API-Token")


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
