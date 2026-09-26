"""Anmeldung an der Weboberfläche: Benutzer, signierte Sitzungs-Cookies, Anmeldebremse.

Ohne Benutzerkonten (kein Passwort konfiguriert) ist keine Anmeldung nötig – das ist nur erlaubt,
solange der Server ausschließlich lokal (Loopback) lauscht (siehe `check_exposure`). Das Passwort
aus `DISKATLAS_PASSWORD` legt beim ersten Start den Master an. Agenten weisen sich mit dem Token
ihres Clients aus (`Authorization: Bearer …`); das gilt für `/api/v1/ingest/*` (nur so, keine
Sitzung) und darf auch Skripte für die übrige API authentifizieren – jeweils als Besitzer des
Clients.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from diskatlas.config import Config
from diskatlas.db.models import Setting, User
from diskatlas.services import users

COOKIE = "diskatlas_session"
SESSION_SECONDS = 30 * 24 * 3600
LOOPBACK = ("127.0.0.1", "::1", "localhost")
OPEN_EXACT = {"/login", "/register", "/api/v1/health", "/manifest.webmanifest", "/sw.js"}
OPEN_PREFIX = ("/static/",)
INGEST_PREFIX = "/api/v1/ingest/"
MAX_FAILURES, FAILURE_WINDOW = 5, 300.0


def check_exposure(config: Config) -> None:
    """Verhindert, dass ein im Netz erreichbarer Server ungeschützt startet."""
    server = config.server
    if server.host in LOOPBACK or server.allow_insecure:
        return
    if not server.password:
        raise RuntimeError(
            f"Der Server lauscht auf {server.host}, aber DISKATLAS_PASSWORD fehlt. Ohne "
            "Anmeldung wäre die Weboberfläche für jeden im Netz offen. Bitte setzen (siehe "
            "config.example.toml / docker-compose.yml) oder den Server nur lokal betreiben "
            "(host = 127.0.0.1)."
        )


def _secret(request: Request) -> bytes:
    app = request.app
    cached = getattr(app.state, "session_secret", None)
    if cached:
        return cached
    with app.state.db.session() as session:
        row = session.get(Setting, "session_secret")
        if row is None:
            row = Setting(key="session_secret", value=secrets.token_hex(32))
            session.add(row)
        value = row.value
    app.state.session_secret = value.encode()
    return app.state.session_secret


@dataclass(frozen=True)
class Identity:
    """Wer eine Anfrage stellt (`client_id` nur bei Anmeldung per Client-Token)."""

    user_id: int
    nickname: str
    is_master: bool
    client_id: int | None = None


def _sign(secret: bytes, user: User, expires: int) -> str:
    # Der Passwort-Hash fließt in die Signatur ein: Wer sein Passwort ändert, meldet alle
    # Sitzungen dieses Benutzers ab (ohne Sitzungstabelle).
    key = hashlib.sha256(secret + user.password_hash.encode()).digest()
    return hmac.new(key, f"{user.id}:{expires}".encode(), hashlib.sha256).hexdigest()


def make_cookie(request: Request, user: User) -> str:
    expires = int(time.time()) + SESSION_SECONDS
    return f"{user.id}.{expires}.{_sign(_secret(request), user, expires)}"


def _user_from_cookie(request: Request, session: Session) -> User | None:
    user_id, _, rest = request.cookies.get(COOKIE, "").partition(".")
    stamp, _, signature = rest.partition(".")
    if not (user_id.isdigit() and stamp.isdigit()) or int(stamp) < time.time():
        return None
    user = session.get(User, int(user_id))
    if user is None or user.status != "active":
        return None
    if not hmac.compare_digest(signature, _sign(_secret(request), user, int(stamp))):
        return None
    return user


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def identify(request: Request, allow_cookie: bool = True) -> Identity | None:
    """Benutzer der Sitzung bzw. Besitzer des Client-Tokens; None, wenn beides fehlt/ungültig."""
    with request.app.state.db.session() as session:
        user = _user_from_cookie(request, session) if allow_cookie else None
        if user is not None:
            return Identity(user.id, user.nickname, user.is_master)
        client = users.find_client_by_token(session, _bearer(request))
        if client is not None:
            owner = client.user
            return Identity(owner.id, owner.nickname, owner.is_master, client.id)
    return None


class LoginThrottle:
    """Bremst wiederholte Fehlversuche je Client (im Speicher, reicht für ein Heimnetz)."""

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}

    def blocked(self, client: str) -> bool:
        now = time.time()
        recent = [t for t in self._failures.get(client, []) if now - t < FAILURE_WINDOW]
        self._failures[client] = recent
        return len(recent) >= MAX_FAILURES

    def fail(self, client: str) -> None:
        self._failures.setdefault(client, []).append(time.time())

    def reset(self, client: str) -> None:
        self._failures.pop(client, None)


def is_secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def set_session(request: Request, response: Response, user: User) -> None:
    response.set_cookie(
        COOKIE, make_cookie(request, user), max_age=SESSION_SECONDS, httponly=True,
        samesite="lax", secure=is_secure(request),
    )


async def auth_middleware(request: Request, call_next):
    request.state.identity = None
    if not request.app.state.auth_enabled:
        return await call_next(request)
    path = request.url.path
    if path in OPEN_EXACT or path.startswith(OPEN_PREFIX):
        return await call_next(request)
    ingest = path.startswith(INGEST_PREFIX)
    # Der Ingest gehört den Agenten: nur mit Client-Token, nie mit einer Browser-Sitzung.
    request.state.identity = identify(request, allow_cookie=not ingest)
    if request.state.identity:
        return await call_next(request)
    if ingest:
        return JSONResponse({"detail": "Ungültiges oder fehlendes Client-Token"}, status_code=401)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Anmeldung erforderlich"}, status_code=401)
    target = path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
