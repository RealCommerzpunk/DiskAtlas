"""Freigaben je Platte, Übernahmeanträge und Zuweisung herrenloser Platten."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from diskatlas.db.models import DiskTransferRequest, User
from diskatlas.services import ownership
from diskatlas.services.authz import Viewer
from diskatlas.web.deps import (
    disk_or_404,
    get_current_user,
    get_session,
    get_viewer,
    require_master,
)
from diskatlas.web.views import templates

router = APIRouter(include_in_schema=False)


def _to_transfers(msg: str) -> RedirectResponse:
    return RedirectResponse("/transfers?" + urlencode({"msg": msg}), status_code=303)


def _back(disk_id: int, error: str = "") -> RedirectResponse:
    query = "?" + urlencode({"share_err": error}) if error else ""
    return RedirectResponse(f"/disks/{disk_id}{query}#share", status_code=303)


# ------------------------------------------------------------------ Freigaben
@router.post("/disks/{disk_id}/shares")
def share_add(
    disk_id: int,
    user_id: int = Form(...),
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
    _: User = Depends(get_current_user),
):
    disk = disk_or_404(session, viewer, disk_id, write=True)
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Benutzer nicht gefunden")
    try:
        ownership.share(session, disk, target)
    except ownership.OwnershipError as exc:
        return _back(disk_id, str(exc))
    return _back(disk_id)


@router.post("/disks/{disk_id}/shares/{user_id}/remove")
def share_remove(
    disk_id: int,
    user_id: int,
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
    _: User = Depends(get_current_user),
):
    ownership.unshare(session, disk_or_404(session, viewer, disk_id, write=True), user_id)
    return _back(disk_id)


@router.post("/disks/{disk_id}/owner")
def assign_owner(
    disk_id: int,
    user_id: int = Form(...),
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
    _: User = Depends(require_master),
):
    """Der Master weist eine herrenlose Platte einem Benutzer zu."""
    disk = disk_or_404(session, viewer, disk_id, write=True)
    target = session.get(User, user_id)
    if target is None or target.status != "active":
        raise HTTPException(404, "Benutzer nicht gefunden")
    if disk.owner_user_id is not None:
        return _back(disk_id, "Die Platte hat schon einen Besitzer (Wechsel per Übernahmeantrag).")
    disk.owner_user_id = target.id
    return _back(disk_id)


# ------------------------------------------------------------------ Übernahmeanträge
@router.get("/transfers", response_class=HTMLResponse)
def transfers(
    request: Request,
    msg: str = "",
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
    user: User = Depends(get_current_user),
):
    incoming = ownership.pending_transfers(session, viewer.scope_id)
    outgoing = list(session.scalars(
        select(DiskTransferRequest)
        .where(DiskTransferRequest.to_user_id == user.id)
        .order_by(DiskTransferRequest.id.desc())
        .limit(30)
    ))
    return templates.TemplateResponse(
        request, "transfers.html", {"incoming": incoming, "outgoing": outgoing, "msg": msg}
    )


def _own_request(
    session: Session, viewer: Viewer, request_id: int
) -> DiskTransferRequest:
    item = session.get(DiskTransferRequest, request_id)
    if item is None or not (viewer.unrestricted or item.from_user_id == viewer.user_id):
        raise HTTPException(404, "Antrag nicht gefunden")
    return item


@router.post("/transfers/{request_id}/approve")
def transfer_approve(
    request_id: int,
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
    _: User = Depends(get_current_user),
):
    item = _own_request(session, viewer, request_id)
    try:
        ownership.approve_transfer(session, item)
    except ownership.OwnershipError as exc:
        return _to_transfers(str(exc))
    return _to_transfers("Platte übergeben.")


@router.post("/transfers/{request_id}/reject")
def transfer_reject(
    request_id: int,
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
    _: User = Depends(get_current_user),
):
    item = _own_request(session, viewer, request_id)
    try:
        ownership.reject_transfer(session, item)
    except ownership.OwnershipError as exc:
        return _to_transfers(str(exc))
    return _to_transfers("Antrag abgelehnt.")


# ------------------------------------------------------------------ Altbestand (Master)
@router.post("/admin/disks/assign-unowned")
def assign_unowned(
    user_id: int = Form(...),
    session: Session = Depends(get_session),
    _: User = Depends(require_master),
):
    target = session.get(User, user_id)
    if target is None or target.status != "active":
        raise HTTPException(404, "Benutzer nicht gefunden")
    disks, labels = ownership.assign_unowned(session, target)
    text = f"{disks} Platte(n) und {labels} Label(s) an {target.nickname} übergeben."
    return RedirectResponse("/admin/users?" + urlencode({"msg": text}), status_code=303)
