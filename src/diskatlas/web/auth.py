"""Anmeldung an der Weboberfläche: ein Passwort, signierte Sitzungs-Cookies, Anmeldebremse.

Ohne konfiguriertes Passwort ist keine Anmeldung nötig – das ist nur erlaubt, solange der Server
ausschließlich lokal (Loopback) lauscht (siehe `check_exposure`). Agenten weisen sich weiterhin
mit dem API-Token aus (`/api/v1/ingest/*`); dasselbe Token darf auch Skripte für die übrige API
authentifizieren (`Authorization: Bearer …`).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from diskatlas.config import Config
from diskatlas.db.models import Setting

COOKIE = "diskatlas_session"
SESSION_SECONDS = 30 * 24 * 3600
LOOPBACK = ("127.0.0.1", "::1", "localhost")
OPEN_EXACT = {"/login", "/api/v1/health", "/manifest.webmanifest", "/sw.js"}
OPEN_PREFIX = ("/static/", "/api/v1/ingest/")  # Ingest prüft das Agenten-Token selbst
MAX_FAILURES, FAILURE_WINDOW = 5, 300.0


def check_exposure(config: Config) -> None:
    """Verhindert, dass ein im Netz erreichbarer Server ungeschützt startet."""
    server = config.server
    if server.host in LOOPBACK or server.allow_insecure:
        return
    missing = [
        name
        for name, value in (("DISKATLAS_PASSWORD", server.password),
                            ("DISKATLAS_API_TOKEN", server.api_token))
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Der Server lauscht auf {server.host}, aber {' und '.join(missing)} "
            "fehlt/fehlen. Ohne Passwort und Token wäre die Weboberfläche für jeden im Netz "
            "offen. Bitte setzen (siehe config.example.toml / docker-compose.yml) oder den "
            "Server nur lokal betreiben (host = 127.0.0.1)."
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


def _sign(secret: bytes, password: str, expires: int) -> str:
    # Das Passwort fließt in die Signatur ein: Wer es ändert, meldet alle Sitzungen ab.
    key = hashlib.sha256(secret + password.encode()).digest()
    return hmac.new(key, str(expires).encode(), hashlib.sha256).hexdigest()


def make_cookie(request: Request, password: str) -> str:
    expires = int(time.time()) + SESSION_SECONDS
    return f"{expires}.{_sign(_secret(request), password, expires)}"


def valid_cookie(request: Request, password: str) -> bool:
    raw = request.cookies.get(COOKIE, "")
    stamp, _, signature = raw.partition(".")
    if not stamp.isdigit() or int(stamp) < time.time():
        return False
    return hmac.compare_digest(signature, _sign(_secret(request), password, int(stamp)))


def password_ok(supplied: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())


def _bearer_ok(request: Request, token: str) -> bool:
    header = request.headers.get("authorization", "")
    return bool(token) and header.lower().startswith("bearer ") and hmac.compare_digest(
        header[7:].encode(), token.encode()
    )


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


def set_session(request: Request, response: Response, password: str) -> None:
    response.set_cookie(
        COOKIE, make_cookie(request, password), max_age=SESSION_SECONDS, httponly=True,
        samesite="lax", secure=is_secure(request),
    )


async def auth_middleware(request: Request, call_next):
    server = request.app.state.config.server
    path = request.url.path
    if (
        not server.password
        or path in OPEN_EXACT
        or path.startswith(OPEN_PREFIX)
        or valid_cookie(request, server.password)
        or _bearer_ok(request, server.api_token)
    ):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Anmeldung erforderlich"}, status_code=401)
    target = path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
