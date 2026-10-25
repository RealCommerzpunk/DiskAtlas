"""Löschprotokolle (PDF) an Platten: hochladen, herunterladen, löschen."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from diskatlas.db.models import DiskAttachment
from diskatlas.services import attachments
from diskatlas.services.authz import Viewer
from diskatlas.web.deps import disk_or_404, get_session, get_viewer

router = APIRouter(include_in_schema=False)


def _back(disk_id: int, **params: str) -> RedirectResponse:
    query = "?" + urlencode(params) if params else ""
    return RedirectResponse(f"/disks/{disk_id}{query}#protocols", status_code=303)


@router.post("/disks/{disk_id}/attachments")
def upload(
    disk_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    disk = disk_or_404(session, viewer, disk_id, write=True)
    data = file.file.read(attachments.MAX_BYTES + 1)
    try:
        attachments.add(session, disk, file.filename or "", data, viewer.user_id)
    except attachments.AttachmentError as exc:
        session.rollback()
        return _back(disk_id, perr=str(exc))
    return _back(disk_id, pmsg="Löschprotokoll angehängt.")


def _attachment(session: Session, viewer: Viewer, attachment_id: str, write: bool):
    attachment = session.get(DiskAttachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, "Anhang nicht gefunden")
    disk_or_404(session, viewer, attachment.disk_id, write=write)  # unsichtbar: 404
    return attachment


@router.get("/attachments/{attachment_id}")
def download(
    attachment_id: str,
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    attachment = _attachment(session, viewer, attachment_id, write=False)
    path = attachments.path_of(attachment)
    if not path.is_file():
        raise HTTPException(404, "Die Datei fehlt in der Ablage.")
    return FileResponse(
        path, media_type="application/pdf", filename=attachment.filename,
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                 "Cache-Control": "private, no-store"},
    )


@router.post("/attachments/{attachment_id}/delete")
def delete(
    attachment_id: str,
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    attachment = _attachment(session, viewer, attachment_id, write=True)
    disk_id = attachment.disk_id
    attachments.remove(session, attachment)
    return _back(disk_id, pmsg="Löschprotokoll entfernt.")
