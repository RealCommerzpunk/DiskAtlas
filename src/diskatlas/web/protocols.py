"""Löschprotokolle (PDF) an Platten: hochladen (einzeln oder gesammelt), zuordnen,
herunterladen, löschen."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from diskatlas.db.models import DiskAttachment
from diskatlas.services import attachments, authz, pdftext, queries
from diskatlas.services.authz import Viewer
from diskatlas.web.deps import disk_or_404, get_session, get_viewer
from diskatlas.web.views import templates

router = APIRouter(include_in_schema=False)
MAX_FILES = 50


def _back(disk_id: int, **params: str) -> RedirectResponse:
    query = "?" + urlencode(params) if params else ""
    return RedirectResponse(f"/disks/{disk_id}{query}#protocols", status_code=303)


def _writable_disks(session: Session, viewer: Viewer):
    disks = queries.load_disks(session, authz.visible_ids(viewer))
    return sorted((d for d in disks if authz.can_write(viewer, d)),
                  key=lambda d: d.display_name.lower())


# ------------------------------------------------------------------ einzeln an einer Platte
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
        attachments.check(data)
        text, created = pdftext.read(data)
        attachment = attachments.add(session, disk, file.filename or "", data, viewer.user_id,
                                     text, created)
    except attachments.AttachmentError as exc:
        session.rollback()
        return _back(disk_id, perr=str(exc))
    message = "Löschprotokoll angehängt."
    if text.strip() and len(pdftext.normalize(disk.serial or "")) >= pdftext.MIN_SERIAL \
            and not attachment.detected_serial:
        message += " Hinweis: Die Seriennummer dieser Platte steht nicht im PDF – richtige Platte?"
    return _back(disk_id, pmsg=message)


# ------------------------------------------------------------------ gesammelt
@router.get("/protocols", response_class=HTMLResponse)
def overview(
    request: Request,
    msg: str = "",
    err: str = "",
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    return templates.TemplateResponse(request, "protocols.html", {
        "open": attachments.unassigned(session, viewer.user_id),
        "disks": _writable_disks(session, viewer), "msg": msg, "err": err,
        "max_files": MAX_FILES, "max_mb": attachments.MAX_BYTES // 1024**2,
    })


@router.post("/protocols")
def upload_many(
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    if len(files) > MAX_FILES:
        return RedirectResponse("/protocols?" + urlencode(
            {"err": f"Höchstens {MAX_FILES} Dateien auf einmal."}), status_code=303)
    payload = [(f.filename or "", f.file.read(attachments.MAX_BYTES + 1)) for f in files]
    result = attachments.add_many(session, payload, _writable_disks(session, viewer),
                                  viewer.user_id)
    parts = [f"{len(result.assigned)} zugeordnet"]
    if result.unassigned:
        parts.append(f"{len(result.unassigned)} offen (bitte unten zuordnen)")
    params = {"msg": ", ".join(parts) + "."}
    if result.rejected:
        params["err"] = "Abgelehnt: " + "; ".join(f"{n} ({why})" for n, why in result.rejected)
    return RedirectResponse("/protocols?" + urlencode(params), status_code=303)


def _attachment(session: Session, viewer: Viewer, attachment_id: str, write: bool):
    attachment = session.get(DiskAttachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, "Anhang nicht gefunden")
    if attachment.disk_id is None:  # offen: nur für den, der es hochgeladen hat
        if attachment.uploaded_by_user_id != viewer.user_id:
            raise HTTPException(404, "Anhang nicht gefunden")
        return attachment
    disk_or_404(session, viewer, attachment.disk_id, write=write)  # unsichtbar: 404
    return attachment


@router.post("/attachments/{attachment_id}/assign")
def assign(
    attachment_id: str,
    disk_id: int = Form(...),
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    attachment = _attachment(session, viewer, attachment_id, write=True)
    if attachment.disk_id is not None:
        raise HTTPException(409, "Das Protokoll ist schon zugeordnet.")
    disk = disk_or_404(session, viewer, disk_id, write=True)
    attachments.assign(attachment, disk)
    return RedirectResponse("/protocols?" + urlencode(
        {"msg": f"„{attachment.filename}“ an {disk.display_name} gehängt."}), status_code=303)


# ------------------------------------------------------------------ Datei
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
    if disk_id is None:
        return RedirectResponse("/protocols?" + urlencode({"msg": "Protokoll entfernt."}),
                                status_code=303)
    return _back(disk_id, pmsg="Löschprotokoll entfernt.")
