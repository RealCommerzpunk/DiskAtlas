"""FastAPI-Abhängigkeiten (Session, Authentifizierung)."""

from __future__ import annotations

import secrets
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

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
