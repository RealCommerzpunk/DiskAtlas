"""„Datei anfordern“: Vorschau, Anlegen, Übersicht, Entscheidung des Besitzers, Abbrechen."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from diskatlas.db.models import User
from diskatlas.services import copies
from diskatlas.services.authz import Viewer
from diskatlas.web.deps import get_current_user, get_session, get_viewer
from diskatlas.web.views import templates

router = APIRouter(include_in_schema=False)


def _enabled(request: Request) -> None:
    if not request.app.state.config.server.copy_enabled:
        raise HTTPException(404, "„Datei anfordern“ ist auf diesem Server nicht freigeschaltet.")


def _preview_page(request, session, user, viewer, sel, error=""):
    try:
        selection = copies.resolve(session, viewer, sel)
    except copies.CopyError as exc:
        raise HTTPException(400, str(exc)) from exc
    choices = copies.target_choices(session, user.id)
    defaults = {c.id: copies.default_target(session, c.id) for c in {x.client for x in choices}}
    return templates.TemplateResponse(request, "request_new.html", {
        "sel": sel, "selection": selection,
        "summary": copies.summarize(session, user.id, selection),
        "choices": choices, "defaults": defaults, "error": error,
        "STATE_TEXT": copies.STATE_TEXT,
    })


@router.post("/requests/preview", response_class=HTMLResponse)
def preview(
    request: Request,
    sel: list[str] = Form(default=[]),
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
    user: User = Depends(get_current_user),
):
    _enabled(request)
    return _preview_page(request, session, user, viewer, sel)


@router.post("/requests")
def create(
    request: Request,
    sel: list[str] = Form(default=[]),
    target: str = Form(""),  # "<client_id>:<volume_id>"
    path: str = Form(""),
    remember: str = Form(""),
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
    user: User = Depends(get_current_user),
):
    _enabled(request)
    client_part, _, volume_part = target.partition(":")
    if not (client_part.isdigit() and volume_part.isdigit()):
        return _preview_page(request, session, user, viewer, sel, "Bitte ein Ziel wählen.")
    try:
        created = copies.create_request(
            session, user.id, sel, viewer, int(client_part), int(volume_part), path,
            remember=bool(remember),
        )
        session.flush()
    except copies.CopyError as exc:
        session.rollback()
        return _preview_page(request, session, user, viewer, sel, str(exc))
    return RedirectResponse(f"/requests/{created.id}", status_code=303)


@router.get("/requests", response_class=HTMLResponse)
def overview(
    request: Request,
    msg: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _enabled(request)
    copies.sweep(session)
    session.commit()
    return templates.TemplateResponse(request, "requests.html", {
        "mine": copies.requests_of(session, user.id),
        "approvals": copies.approvals_for(session, user.id),
        "connect": copies.disks_to_connect(session, user.id),
        "user_name": user.nickname, "counts": copies.counts,
        "STATE_TEXT": copies.STATE_TEXT, "msg": msg,
    })


def _mine_or_404(session: Session, user: User, request_id: str):
    found = copies.get_request(session, request_id)
    if found is None or found.requester_user_id != user.id:
        raise HTTPException(404, "Anfrage nicht gefunden")
    return found


@router.get("/requests/{request_id}", response_class=HTMLResponse)
def detail(
    request: Request,
    request_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _enabled(request)
    found = _mine_or_404(session, user, request_id)
    copies.sweep(session)
    session.commit()
    return templates.TemplateResponse(request, "request_detail.html", {
        "req": found, "counts": copies.counts(found), "STATE_TEXT": copies.STATE_TEXT,
    })


@router.post("/requests/{request_id}/cancel")
def cancel(
    request: Request,
    request_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _enabled(request)
    copies.cancel(session, _mine_or_404(session, user, request_id))
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@router.post("/requests-decide")
def decide(
    request: Request,
    item: list[str] = Form(default=[]),
    action: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _enabled(request)
    if action not in ("approve", "deny"):
        raise HTTPException(400, "Unbekannte Aktion")
    n = copies.decide(session, user.id, item, approve=action == "approve")
    return RedirectResponse("/requests?" + urlencode({"msg": f"{n} Datei(en) bearbeitet."}),
                            status_code=303)


