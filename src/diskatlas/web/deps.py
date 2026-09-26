"""FastAPI-Abhängigkeiten (Session, Authentifizierung)."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from diskatlas.db.models import Client, Disk, Label, User
from diskatlas.services import authz, queries
from diskatlas.services.authz import Viewer


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
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nur für den Admin.")
    return user


def get_viewer(request: Request) -> Viewer:
    """Wer fragt (für Sichtbarkeit und Schreibrechte); ohne Anmeldung gibt es keine Beschränkung."""
    if not request.app.state.auth_enabled:
        return authz.OPEN
    identity = getattr(request.state, "identity", None)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Anmeldung erforderlich")
    return Viewer(identity.user_id, identity.is_master)


def disk_or_404(session: Session, viewer: Viewer, disk_id: int, write: bool = False) -> Disk:
    """Die Platte, falls der Benutzer sie sehen darf (sonst 404, das verrät nichts); mit
    `write` außerdem nur, wenn sie ihm gehört (sonst 403: freigegebene Platten sind nur lesbar)."""
    disk = queries.get_disk(session, disk_id, authz.visible_ids(viewer))
    if disk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Festplatte nicht gefunden")
    if write and not authz.can_write(viewer, disk):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Die Festplatte gehört einem anderen Benutzer (nur lesbar)."
        )
    return disk


def label_or_404(session: Session, viewer: Viewer, label_id: int) -> Label:
    """Das Label, falls es dem Benutzer gehört (fremde bleiben unsichtbar: 404)."""
    label = session.get(Label, label_id)
    if label is None or not authz.can_write_label(viewer, label):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Label nicht gefunden")
    return label
