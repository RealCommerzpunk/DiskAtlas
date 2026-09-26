"""Server-gerenderte Oberfläche (Dashboard, Festplatten, Dateisuche, Labels)."""

from __future__ import annotations

import contextlib
import socket
from pathlib import Path
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from diskatlas import __version__
from diskatlas.db.models import Disk, FileEntry, Label, Volume
from diskatlas.probe import ports as sata
from diskatlas.services import bays as bay_service
from diskatlas.services import duplicates, fslabel, queries
from diskatlas.web import formatting
from diskatlas.web.deps import get_session

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
formatting.register(templates.env)
templates.env.globals.update(
    version=__version__,
    HEALTH_LABELS=queries.HEALTH_LABELS,
    GROUP_OPTIONS=queries.GROUP_OPTIONS,
    SORT_OPTIONS={k: v[0] for k, v in queries.SORT_OPTIONS.items()},
)

router = APIRouter(include_in_schema=False)
PAGE_SIZE = 100


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


HIDE_SYSTEM_COOKIE = "diskatlas_hide_system"


def _hide_system(request: Request) -> bool:
    """Standard: an. Nur ein ausdrücklich gesetztes „0“ zeigt Systemdatenträger wieder an."""
    return request.cookies.get(HIDE_SYSTEM_COOKIE, "1") != "0"


def _excluded_disks(request: Request, session: Session) -> tuple[int, ...]:
    return queries.system_disk_ids(session) if _hide_system(request) else ()


@router.post("/prefs/hide-system")
def set_hide_system(request: Request, hide: list[str] = Form(default=[])):
    response = _redirect(_safe_back(request.headers.get("referer", "/")))
    response.set_cookie(
        HIDE_SYSTEM_COOKIE, "1" if "1" in hide else "0", max_age=365 * 24 * 3600, samesite="lax"
    )
    return response


def _safe_back(referer: str) -> str:
    """Nur den lokalen Pfad des Referers verwenden (kein Open-Redirect)."""
    parsed = urlparse(referer)
    return (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


# ------------------------------------------------------------------ Dashboard
@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    q: str = "",
    health: str = "",
    connected: str = "",
    label: str = "",
    fs: str = "",
    usage: str = "",
    group: str = "none",
    sort: str = "name",
    desc: bool = False,
    session: Session = Depends(get_session),
):
    excluded = set(_excluded_disks(request, session))
    all_disks = [d for d in queries.load_disks(session) if d.id not in excluded]
    flt = queries.DiskFilter(
        q=q, health=health, connected=connected, label_id=_int_or_none(label), fs=fs, usage=usage
    )
    bays = _bays(request, session)
    in_bay = {b.disk.id for b in bays or [] if b.disk}  # stehen als Schachtzeilen oben
    disks = queries.sort_disks(
        queries.filter_disks([d for d in all_disks if d.id not in in_bay], flt), sort, desc
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": queries.dashboard_stats(all_disks),
            "hide_system": _hide_system(request),
            "hidden_system_count": len(excluded),
            "bays": bays,
            "filtered_count": len(disks),
            "groups": queries.group_disks(disks, group),
            "labels": [lab for lab, _ in queries.list_labels(session)],
            "fs_options": queries.known_fs_types(all_disks),
            "NO_FS": queries.NO_FS,
            "f": {"q": q, "health": health, "connected": connected, "label": label,
                  "fs": fs, "usage": usage, "group": group, "sort": sort, "desc": desc},
        },
    )


def _bays(request: Request, session: Session):
    """Schachtansicht; None, wenn dieser Rechner keine SATA-Ports hat (Windows, Docker …)."""
    if not sata.all_ports():
        return None
    path = request.app.state.bays_path
    result = bay_service.build_bays(session, bay_service.load_assignment(path), sata.sata_ports())
    return result[::-1] if bay_service.load_reverse(path) else result


@router.get("/bays/setup", response_class=HTMLResponse)
def bays_setup(request: Request, err: str = ""):
    assignment = bay_service.load_assignment(request.app.state.bays_path)
    return templates.TemplateResponse(
        request,
        "bays_setup.html",
        {
            "assignment": assignment,
            "reverse": bay_service.load_reverse(request.app.state.bays_path),
            "all_ports": sata.all_ports(),
            "live": sata.sata_ports(),
            "bay_count": bay_service.BAY_COUNT,
            "flash_err": err,
        },
    )


@router.post("/bays/setup")
def bays_save(
    request: Request, port: list[str] = Form(default=[]), reverse: bool = Form(False)
):
    valid = set(sata.all_ports())
    chosen = [p if p in valid else None for p in port]
    try:
        bay_service.save_assignment(request.app.state.bays_path, chosen, reverse)
    except (ValueError, OSError) as exc:
        return _redirect("/bays/setup?" + urlencode({"err": str(exc)}))
    return _redirect("/")


# ------------------------------------------------------------------ Festplatte
def _disk_or_404(session: Session, disk_id: int) -> Disk:
    disk = queries.get_disk(session, disk_id)
    if disk is None:
        raise HTTPException(404, "Festplatte nicht gefunden")
    return disk


@router.get("/disks/{disk_id}", response_class=HTMLResponse)
def disk_detail(
    request: Request,
    disk_id: int,
    msg: str = "",
    err: str = "",
    session: Session = Depends(get_session),
):
    disk = _disk_or_404(session, disk_id)
    assigned = {lab.id for lab in disk.labels}
    return templates.TemplateResponse(
        request,
        "disk.html",
        {
            "disk": disk,
            "flash_ok": msg,
            "flash_err": err,
            "relabel_block": _relabel_block_reason(request, disk),
            "history": queries.smart_history(session, disk_id),
            "health_reasons": (
                queries.health_reasons(session, disk_id) if disk.health != "ok" else []
            ),
            "extensions": queries.extension_summary(session, disk_id),
            "available_labels": [
                lab for lab, _ in queries.list_labels(session) if lab.id not in assigned
            ],
        },
    )


@router.post("/disks/{disk_id}")
def disk_update(
    disk_id: int,
    custom_name: str = Form(""),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    disk = _disk_or_404(session, disk_id)
    disk.custom_name = custom_name.strip() or None
    disk.notes = notes.strip() or None
    return _redirect(f"/disks/{disk_id}")


@router.post("/disks/{disk_id}/labels")
def disk_add_label(
    disk_id: int,
    label_id: str = Form(""),
    new_name: str = Form(""),
    new_category: str = Form(""),
    new_color: str = Form("#4f7cff"),
    session: Session = Depends(get_session),
):
    disk = _disk_or_404(session, disk_id)
    label = None
    if new_name.strip():
        name = new_name.strip()
        label = session.scalar(select(Label).where(Label.name == name))
        if label is None:
            label = Label(name=name, category=new_category.strip() or None, color=new_color)
            session.add(label)
    elif _int_or_none(label_id):
        label = session.get(Label, int(label_id))
    if label is not None and label not in disk.labels:
        disk.labels.append(label)
    return _redirect(f"/disks/{disk_id}#labels")


@router.post("/disks/{disk_id}/labels/{label_id}/remove")
def disk_remove_label(disk_id: int, label_id: int, session: Session = Depends(get_session)):
    disk = _disk_or_404(session, disk_id)
    disk.labels = [lab for lab in disk.labels if lab.id != label_id]
    return _redirect(f"/disks/{disk_id}#labels")


@router.post("/disks/{disk_id}/delete")
def disk_delete(disk_id: int, session: Session = Depends(get_session)):
    session.delete(_disk_or_404(session, disk_id))
    return _redirect("/")


def _relabel_block_reason(request: Request, disk: Disk) -> str:
    """Leer, wenn das Umbenennen hier möglich ist; sonst der Grund (für die Oberfläche)."""
    config = request.app.state.config
    if config.server.host not in ("127.0.0.1", "::1", "localhost"):
        return "Umbenennen ist nur möglich, wenn der Server ausschließlich lokal erreichbar ist."
    if not disk.is_connected:
        return "Die Festplatte ist nicht angeschlossen."
    local = config.agent.host_name or socket.gethostname()
    if disk.last_host and disk.last_host != local:
        return f"Die Festplatte hängt an „{disk.last_host}“ – dort umbenennen."
    return ""


@router.post("/volumes/{volume_id}/label")
def volume_label(
    request: Request,
    volume_id: int,
    label: str = Form(""),
    session: Session = Depends(get_session),
):
    volume = session.get(Volume, volume_id)
    if volume is None:
        raise HTTPException(404, "Volume nicht gefunden")
    disk = volume.disk
    base = f"/disks/{disk.id}"

    def back(kind: str, text: str) -> RedirectResponse:
        return _redirect(f"{base}?{urlencode({kind: text})}#volumes")

    if reason := _relabel_block_reason(request, disk):
        return back("err", reason)
    if not volume.present:
        return back("err", "Das Volume ist nicht mehr vorhanden.")
    if volume.index_status == "running":
        return back("err", "Das Volume wird gerade indiziert – bitte danach erneut versuchen.")
    try:
        written = fslabel.set_label(volume.device, volume.fs_type, volume.mountpoint, label)
    except fslabel.LabelError as exc:
        return back("err", str(exc))
    volume.label = written or None
    done = f"Bezeichnung geändert: „{written}“." if written else "Bezeichnung entfernt."
    return back("msg", done)


@router.post("/volumes/{volume_id}/delete")
def volume_delete(volume_id: int, session: Session = Depends(get_session)):
    volume = session.get(Volume, volume_id)
    if volume is None:
        raise HTTPException(404, "Volume nicht gefunden")
    disk_id = volume.disk_id
    session.execute(delete(FileEntry).where(FileEntry.volume_id == volume_id))
    session.delete(volume)
    return _redirect(f"/disks/{disk_id}#volumes")


# ------------------------------------------------------------------ Dateisuche
@router.get("/files", response_class=HTMLResponse)
def files(
    request: Request,
    q: str = "",
    ext: str = "",
    disk: str = "",
    label: str = "",
    min_mb: str = "",
    sort: str = "name",
    desc: bool = False,
    page: int = 1,
    session: Session = Depends(get_session),
):
    page = max(page, 1)
    excluded = _excluded_disks(request, session)
    min_mb_value = None
    with contextlib.suppress(ValueError):
        min_mb_value = float(min_mb.replace(",", ".")) if min_mb else None
    params = {"q": q, "ext": ext, "disk": disk, "label": label, "min_mb": min_mb,
              "sort": sort, "desc": desc}
    has_query = any([q, ext, disk, label, min_mb])
    rows, total = [], 0
    if has_query:
        rows, total = queries.search_files(
            session,
            queries.FileQuery(
                q=q, extension=ext, disk_id=_int_or_none(disk), label_id=_int_or_none(label),
                min_size=int(min_mb_value * 1_000_000) if min_mb_value else None,
                sort=sort, descending=desc, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE,
                exclude_disk_ids=excluded,
            ),
        )
    pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    base = {k: v for k, v in params.items() if v not in ("", False)}
    return templates.TemplateResponse(
        request,
        "files.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "pages": pages,
            "has_query": has_query,
            "f": params,
            "page_url": lambda p: "/files?" + urlencode({**base, "page": p}),
            "sort_url": lambda s: "/files?" + urlencode(
                {**base, "sort": s, "desc": (not desc) if s == sort else (s in ("size", "mtime"))}
            ),
            "disks": sorted(
                (d for d in queries.load_disks(session) if d.id not in excluded),
                key=lambda d: d.display_name.lower(),
            ),
            "labels": [lab for lab, _ in queries.list_labels(session)],
        },
    )


# ------------------------------------------------------------------ Doubletten
def _mb_to_bytes(value: str) -> int | None:
    try:
        return int(float(value.replace(",", ".")) * 1_000_000) if value else None
    except ValueError:
        return None


@router.get("/duplicates", response_class=HTMLResponse)
def duplicate_page(
    request: Request,
    mode: str = "files",
    ext: str = "",
    min_mb: str = "",
    min_files: int = 2,
    hidden: bool = False,
    page: int = 1,
    session: Session = Depends(get_session),
):
    mode = mode if mode in ("files", "folders") else "files"
    page = max(page, 1)
    excluded = _excluded_disks(request, session)
    min_size = _mb_to_bytes(min_mb)
    ctx = {"mode": mode, "f": {"ext": ext, "min_mb": min_mb, "min_files": max(min_files, 1),
                             "hidden": hidden},
           "page": page, "pages": 1}
    if mode == "files":
        groups, total, wasted = duplicates.find_file_duplicates(
            session,
            duplicates.FileDupQuery(
                min_size=min_size or 1, extension=ext, limit=PAGE_SIZE // 2,
                offset=(page - 1) * (PAGE_SIZE // 2), exclude_disk_ids=excluded,
            ),
        )
        base = {k: v for k, v in {"mode": mode, "ext": ext, "min_mb": min_mb}.items() if v}
        ctx.update(
            groups=groups, total=total, wasted=wasted,
            pages=max((total + PAGE_SIZE // 2 - 1) // (PAGE_SIZE // 2), 1),
            page_url=lambda p: "/duplicates?" + urlencode({**base, "page": p}),
        )
    else:
        groups, wasted = duplicates.find_folder_duplicates(
            session,
            duplicates.FolderDupQuery(
                min_files=max(min_files, 1), min_size=min_size or 0, include_hidden=hidden,
                exclude_disk_ids=excluded,
            ),
        )
        ctx.update(groups=groups, total=len(groups), wasted=wasted,
                   capped=len(groups) >= duplicates.MAX_FOLDER_GROUPS)
    return templates.TemplateResponse(request, "duplicates.html", ctx)


# ------------------------------------------------------------------ Labels
@router.get("/labels", response_class=HTMLResponse)
def labels(request: Request, error: str = "", session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request, "labels.html", {"labels": queries.list_labels(session), "error": error}
    )


@router.post("/labels")
def label_create(
    name: str = Form(...),
    category: str = Form(""),
    color: str = Form("#4f7cff"),
    session: Session = Depends(get_session),
):
    if not name.strip():
        return _redirect("/labels")
    exists = session.scalar(select(Label).where(Label.name == name.strip()))
    if exists:
        message = f"Label „{name.strip()}“ existiert bereits"
        return _redirect("/labels?" + urlencode({"error": message}))
    session.add(Label(name=name.strip(), category=category.strip() or None, color=color))
    return _redirect("/labels")


@router.post("/labels/{label_id}")
def label_update(
    label_id: int,
    name: str = Form(...),
    category: str = Form(""),
    color: str = Form("#4f7cff"),
    session: Session = Depends(get_session),
):
    label = session.get(Label, label_id)
    if label is None:
        raise HTTPException(404, "Label nicht gefunden")
    label.name = name.strip() or label.name
    label.category = category.strip() or None
    label.color = color
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return _redirect("/labels?" + urlencode({"error": "Name bereits vergeben"}))
    return _redirect("/labels")


@router.post("/labels/{label_id}/delete")
def label_delete(label_id: int, session: Session = Depends(get_session)):
    label = session.get(Label, label_id)
    if label is not None:
        session.delete(label)
    return _redirect("/labels")
