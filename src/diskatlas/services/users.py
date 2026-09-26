"""Benutzer, Anträge und Clients (Agent-Installationen mit eigenem Token)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from functools import cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from diskatlas.db.models import Client, User
from diskatlas.services.ingest import utcnow
from diskatlas.web import security

MASTER_NICKNAME = "Admin"
MIN_PASSWORD = 10
MAX_PASSWORD = 200
LAST_SEEN_INTERVAL = timedelta(minutes=1)  # so oft wird `last_seen` höchstens geschrieben
_NICKNAME = re.compile(r"^[\w][\w .\-]{0,38}[\w]$")


@cache
def _dummy_hash() -> str:
    # Für unbekannte Benutzer wird trotzdem gerechnet, damit die Antwortzeit nichts verrät.
    return security.hash_password("dummy")


class UserError(ValueError):
    """Eingabefehler mit Text, der dem Benutzer angezeigt werden darf."""


def clean_nickname(raw: str) -> str:
    nickname = " ".join(raw.split())
    if not _NICKNAME.match(nickname):
        raise UserError(
            "Der Name muss 2–40 Zeichen lang sein (Buchstaben, Ziffern, Leerzeichen, . _ -)."
        )
    return nickname


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise UserError(f"Das Passwort muss mindestens {MIN_PASSWORD} Zeichen lang sein.")
    if len(password) > MAX_PASSWORD:
        raise UserError(f"Das Passwort darf höchstens {MAX_PASSWORD} Zeichen lang sein.")


def find_user(session: Session, nickname: str) -> User | None:
    """Sucht ohne Beachtung der Groß-/Kleinschreibung („Anna“ und „anna“ wären verwechselbar)."""
    return session.scalar(select(User).where(func.lower(User.nickname) == nickname.lower()))


def has_users(session: Session) -> bool:
    return session.scalar(select(func.count()).select_from(User)) > 0


def bootstrap_master(session: Session, password: str) -> bool:
    """Legt beim allerersten Start den Admin an (Passwort aus DISKATLAS_PASSWORD).

    Ein Konto, das bis Version 0.6 „Master“ hieß, wird zu „Admin“ (sofern der Name frei ist).
    """
    legacy = session.scalar(select(User).where(User.is_master, User.nickname == "Master"))
    if legacy is not None and find_user(session, MASTER_NICKNAME) is None:
        legacy.nickname = MASTER_NICKNAME
        session.commit()
    if not password or has_users(session):
        return False
    session.add(User(nickname=MASTER_NICKNAME, password_hash=security.hash_password(password),
                     is_master=True, status="active"))
    session.commit()
    return True


def register(session: Session, nickname: str, password: str, note: str = "") -> User:
    nickname = clean_nickname(nickname)
    check_password(password)
    if find_user(session, nickname):
        raise UserError("Dieser Name ist schon vergeben.")
    user = User(nickname=nickname, password_hash=security.hash_password(password),
                status="pending", note=note.strip()[:500] or None)
    session.add(user)
    session.commit()
    return user


def authenticate(session: Session, nickname: str, password: str) -> User | None:
    """Der Benutzer bei richtigem Passwort (auch wenn noch nicht freigeschaltet), sonst None."""
    user = find_user(session, nickname.strip())
    if user is None:
        security.verify_password(password, _dummy_hash())
        return None
    return user if security.verify_password(password, user.password_hash) else None


def change_password(session: Session, user: User, current: str, new: str) -> None:
    if not security.verify_password(current, user.password_hash):
        raise UserError("Das aktuelle Passwort stimmt nicht.")
    check_password(new)
    user.password_hash = security.hash_password(new)
    session.commit()


def create_client(session: Session, user: User, nickname: str) -> tuple[Client, str]:
    """Legt einen Client an. Das Token gibt es nur hier im Klartext, gespeichert wird sein Hash."""
    nickname = clean_nickname(nickname)
    if any(c.nickname.lower() == nickname.lower() for c in user.clients):
        raise UserError("Du hast schon einen Client mit diesem Namen.")
    token = security.generate_token()
    client = Client(user_id=user.id, nickname=nickname, token_hash=security.hash_token(token))
    session.add(client)
    session.commit()
    return client, token


def find_client_by_token(
    session: Session, token: str, now: datetime | None = None
) -> Client | None:
    """Client eines freigeschalteten Benutzers zum Token; aktualisiert `last_seen` sparsam."""
    if not token:
        return None
    client = session.scalar(
        select(Client).where(Client.token_hash == security.hash_token(token))
    )
    if client is None or client.user.status != "active":
        return None
    now = now or utcnow()
    if client.last_seen is None or now - client.last_seen > LAST_SEEN_INTERVAL:
        client.last_seen = now
        session.commit()
    return client
