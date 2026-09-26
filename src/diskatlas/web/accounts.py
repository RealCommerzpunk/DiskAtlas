"""Konto-Seiten: Zugang beantragen, eigenes Konto samt Clients, Verwaltung durch den Master."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from diskatlas.db.models import Client, User
from diskatlas.services import users
from diskatlas.web import auth
from diskatlas.web.deps import get_current_user, get_session, require_master
from diskatlas.web.views import templates

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
    session.delete(client)
    session.commit()
    return RedirectResponse("/account", status_code=303)


# ------------------------------------------------------------------ Verwaltung (Master)
@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    user: User = Depends(require_master),
    session: Session = Depends(get_session),
):
    everyone = session.scalars(select(User).order_by(User.created_at, User.id)).all()
    return templates.TemplateResponse(request, "admin_users.html", {
        "pending": [u for u in everyone if u.status == "pending"],
        "active": [u for u in everyone if u.status == "active"],
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
