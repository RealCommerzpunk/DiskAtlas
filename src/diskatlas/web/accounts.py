"""Konto-Seiten: Zugang beantragen, eigenes Konto samt Clients, Verwaltung durch den Admin."""

from __future__ import annotations

import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from diskatlas.db.models import Client, Disk, HostState, User
from diskatlas.services import bays as bay_service
from diskatlas.services import hosts, users
from diskatlas.web import auth
from diskatlas.web.deps import get_current_user, get_session, require_master
from diskatlas.web.views import setup_context, templates

router = APIRouter(include_in_schema=False)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


# ------------------------------------------------------------------ Zugang beantragen
def _register_page(request: Request, **context) -> HTMLResponse:
    if not request.app.state.auth_enabled:
        raise HTTPException(404, "Ohne Anmeldung gibt es keine Benutzerverwaltung.")
    context.setdefault("error", "")
    context.setdefault("done", False)
    context.setdefault("nickname", "")
    context.setdefault("note", "")
    return templates.TemplateResponse(request, "register.html", context)


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return _register_page(request)


@router.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    nickname: str = Form(""),
    password: str = Form(""),
    note: str = Form(""),
    session: Session = Depends(get_session),
):
    if not request.app.state.auth_enabled:
        raise HTTPException(404, "Ohne Anmeldung gibt es keine Benutzerverwaltung.")
    throttle = request.app.state.login_throttle
    key = f"register:{_client_ip(request)}"
    if throttle.blocked(key):
        return _register_page(
            request, nickname=nickname, note=note,
            error="Zu viele Anträge – bitte in einigen Minuten erneut versuchen.",
        )
    throttle.fail(key)  # jeder Antrag zählt, nicht nur Fehler
    try:
        users.register(session, nickname, password, note)
    except users.UserError as exc:
        return _register_page(request, nickname=nickname, note=note, error=str(exc))
    return _register_page(request, done=True)


# ------------------------------------------------------------------ eigenes Konto
def _account_page(
    request: Request, user: User, *, error: str = "", info: str = "", new_token: str = "",
    new_client: str = "",
) -> HTMLResponse:
    return templates.TemplateResponse(request, "account.html", {
        "user": user, "error": error, "info": info,
        "bay_configs": {c.id: bay_service.client_config(c) for c in user.clients},
        "max_bays": bay_service.MAX_BAYS,
        "new_token": new_token, "new_client": new_client,
    })


@router.get("/account", response_class=HTMLResponse)
def account(request: Request, user: User = Depends(get_current_user)):
    return _account_page(request, user)


@router.post("/account/password", response_class=HTMLResponse)
def account_password(
    request: Request,
    current: str = Form(""),
    new: str = Form(""),
    repeat: str = Form(""),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if new != repeat:
        return _account_page(request, user, error="Die neuen Passwörter sind nicht gleich.")
    try:
        users.change_password(session, user, current, new)
    except users.UserError as exc:
        time.sleep(0.4)
        return _account_page(request, user, error=str(exc))
    response = _account_page(request, user, info="Passwort geändert.")
    auth.set_session(request, response, user)  # alte Sitzungen sind damit ungültig
    return response


@router.post("/account/clients", response_class=HTMLResponse)
def account_client_create(
    request: Request,
    nickname: str = Form(""),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    try:
        client, token = users.create_client(session, user, nickname)
    except users.UserError as exc:
        return _account_page(request, user, error=str(exc))
    return _account_page(request, user, new_token=token, new_client=client.nickname)


@router.post("/account/clients/{client_id}/delete")
def account_client_delete(
    client_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    client = session.get(Client, client_id)
    if client is None or client.user_id != user.id:
        raise HTTPException(404, "Client nicht gefunden")
    # SQLite vergibt Ids wieder: Verweise auf den gelöschten Client ausdrücklich entfernen
    session.execute(
        update(Disk).where(Disk.last_client_id == client.id).values(last_client_id=None)
    )
    session.execute(delete(HostState).where(HostState.host == hosts.state_key(client, "")))
    session.delete(client)
    session.commit()
    return RedirectResponse("/account", status_code=303)


# ------------------------------------------------------------------ Schächte je Client
def _own_client(session: Session, user: User, client_id: int) -> Client:
    client = session.get(Client, client_id)
    if client is None or client.user_id != user.id:  # auch der Admin verwaltet nur eigene
        raise HTTPException(404, "Client nicht gefunden")
    return client


@router.post("/account/clients/{client_id}/bays", response_class=HTMLResponse)
def account_client_bays(
    request: Request,
    client_id: int,
    bay_count: int = Form(...),
    no_bays: bool = Form(False),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Einstellungen des Clients: Anzahl der Wechselschächte bzw. „Keine Wechselschächte“."""
    client = _own_client(session, user, client_id)
    config = bay_service.client_config(client)
    try:
        ports = bay_service.resize(config.ports, bay_count)
        bay_service.save_client_config(
            client, bay_service.BayConfig(ports=ports, reverse=config.reverse), has_bays=not no_bays
        )
    except bay_service.BayError as exc:
        return _account_page(request, user, error=str(exc))
    text = "Keine Wechselschächte." if no_bays else f"{bay_count} Wechselschächte."
    return _account_page(request, user, info=f"„{client.nickname}“: {text}")


@router.get("/clients/{client_id}/bays", response_class=HTMLResponse)
def client_bays(
    request: Request,
    client_id: int,
    err: str = "",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    client = _own_client(session, user, client_id)
    return templates.TemplateResponse(request, "bays_setup.html", setup_context(
        heading=f"Schächte von „{client.nickname}“", action=f"/clients/{client.id}/bays",
        poll_url=f"/api/v1/bays/live?client_id={client.id}",
        snapshot=bay_service.client_snapshot(session, client),
        config=bay_service.client_config(client), back_url="/account#clients", err=err,
    ))


@router.post("/clients/{client_id}/bays")
def client_bays_save(
    client_id: int,
    port: list[str] = Form(default=[]),
    reverse: bool = Form(False),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    client = _own_client(session, user, client_id)
    config = bay_service.client_config(client)
    snapshot = bay_service.client_snapshot(session, client)
    # bisherige Zuordnungen bleiben gültig, auch wenn der Agent gerade offline ist
    valid = set(snapshot.all_ports if snapshot else []) | {p for p in config.ports if p}
    chosen = bay_service.clean_ports([p if p in valid else None for p in port], config.count)
    try:
        bay_service.save_client_config(
            client, bay_service.BayConfig(ports=chosen, reverse=reverse), has_bays=True
        )
    except bay_service.BayError as exc:
        return RedirectResponse(
            f"/clients/{client.id}/bays?" + urlencode({"err": str(exc)}), status_code=303
        )
    return RedirectResponse("/", status_code=303)


# ------------------------------------------------------------------ Verwaltung (Admin)
@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    msg: str = "",
    user: User = Depends(require_master),
    session: Session = Depends(get_session),
):
    everyone = session.scalars(select(User).order_by(User.created_at, User.id)).all()
    unowned = session.scalar(
        select(func.count()).select_from(Disk).where(Disk.owner_user_id.is_(None))
    )
    return templates.TemplateResponse(request, "admin_users.html", {
        "pending": [u for u in everyone if u.status == "pending"],
        "active": [u for u in everyone if u.status == "active"],
        "unowned": unowned,
        "msg": msg,
    })


def _pending_user(session: Session, user_id: int) -> User:
    target = session.get(User, user_id)
    if target is None or target.status != "pending":
        raise HTTPException(404, "Kein offener Antrag")
    return target


@router.post("/admin/users/{user_id}/approve")
def admin_approve(
    user_id: int,
    _: User = Depends(require_master),
    session: Session = Depends(get_session),
):
    _pending_user(session, user_id).status = "active"
    session.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/reject")
def admin_reject(
    user_id: int,
    _: User = Depends(require_master),
    session: Session = Depends(get_session),
):
    session.delete(_pending_user(session, user_id))
    session.commit()
    return RedirectResponse("/admin/users", status_code=303)
