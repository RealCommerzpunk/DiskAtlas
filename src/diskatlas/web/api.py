"""REST-API unter /api/v1 (Dokumentation: /docs)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from diskatlas import __version__
from diskatlas.db.models import Client, Disk, Label, User
from diskatlas.probe.types import FileRecord
from diskatlas.services import authz, commands, copies, duplicates, hosts, ingest, labels, queries
from diskatlas.services import lookup as lookup_service
from diskatlas.services.authz import Viewer
from diskatlas.web.deps import (
    disk_or_404,
    get_current_user,
    get_session,
    get_viewer,
    label_or_404,
    require_client,
)
from diskatlas.web.schemas import (
    CommandResult,
    DiskDetailOut,
    DiskOut,
    DiskPatch,
    DuplicateLocation,
    FileDuplicateGroup,
    FileDuplicateResult,
    FileOut,
    FileSearchResult,
    FolderDuplicateGroup,
    FolderDuplicateResult,
    IngestConnected,
    IngestDisk,
    IngestFiles,
    IngestFinish,
    IngestVolumeRef,
    LabelAssignment,
    LabelIn,
    LabelOut,
    LabelPatch,
    LookupOut,
    RunningIndexOut,
    StatsOut,
    UserOut,
)

router = APIRouter()


@router.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok", "version": __version__}


# ------------------------------------------------------------------ Festplatten
@router.get("/disks", response_model=list[DiskOut], tags=["disks"])
def list_disks(
    q: str = "",
    health: str = "",
    connected: str = "",
    label_id: int | None = None,
    fs: str = "",
    usage: str = "",
    client: str = Query("", description="Client-ID, an dem die Platte zuletzt hing, oder 'none'"),
    sort: str = "name",
    desc: bool = False,
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    disks = queries.filter_disks(
        queries.load_disks(session, authz.visible_ids(viewer)),
        queries.DiskFilter(
            q=q, health=health, connected=connected, label_id=label_id, fs=fs, usage=usage,
            client=client,
        ),
    )
    return queries.sort_disks(disks, sort, desc)


@router.get("/disks/{disk_id}", response_model=DiskDetailOut, tags=["disks"])
def get_disk(
    disk_id: int, session: Session = Depends(get_session), viewer: Viewer = Depends(get_viewer)
):
    return disk_or_404(session, viewer, disk_id)


@router.patch("/disks/{disk_id}", response_model=DiskDetailOut, tags=["disks"])
def patch_disk(
    disk_id: int,
    body: DiskPatch,
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    disk = disk_or_404(session, viewer, disk_id, write=True)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(disk, field, (value or "").strip() or None)
    session.flush()
    return disk


@router.delete("/disks/{disk_id}", status_code=204, tags=["disks"])
def delete_disk(
    disk_id: int, session: Session = Depends(get_session), viewer: Viewer = Depends(get_viewer)
):
    session.delete(disk_or_404(session, viewer, disk_id, write=True))
    return Response(status_code=204)


@router.put("/disks/{disk_id}/labels", response_model=DiskDetailOut, tags=["disks"])
def set_disk_labels(
    disk_id: int,
    body: LabelAssignment,
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    disk = disk_or_404(session, viewer, disk_id, write=True)
    labels = list(session.scalars(select(Label).where(Label.id.in_(body.label_ids))))
    usable = [
        lab for lab in labels
        if authz.can_write_label(viewer, lab) and authz.label_fits_disk(lab, disk)
    ]
    if len(usable) != len(set(body.label_ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unbekannte Label-ID")
    disk.labels = labels
    session.flush()
    return disk


# ------------------------------------------------------------------ Labels
@router.get("/labels", response_model=list[LabelOut], tags=["labels"])
def list_labels(session: Session = Depends(get_session), viewer: Viewer = Depends(get_viewer)):
    return [label for label, _ in queries.list_labels(session, viewer)]


@router.post("/labels", response_model=LabelOut, status_code=201, tags=["labels"])
def create_label(
    body: LabelIn, session: Session = Depends(get_session), viewer: Viewer = Depends(get_viewer)
):
    name = body.name.strip()
    if labels.name_taken(session, viewer.user_id, name):
        raise HTTPException(status.HTTP_409_CONFLICT, "Label existiert bereits")
    label = Label(owner_user_id=viewer.user_id, name=name,
                  category=(body.category or "").strip() or None, color=body.color)
    session.add(label)
    try:
        session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Label existiert bereits") from exc
    return label


@router.patch("/labels/{label_id}", response_model=LabelOut, tags=["labels"])
def patch_label(
    label_id: int,
    body: LabelPatch,
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    label = label_or_404(session, viewer, label_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "category":
            value = (value or "").strip() or None
        if field == "name" and labels.name_taken(session, label.owner_user_id, value, label.id):
            raise HTTPException(status.HTTP_409_CONFLICT, "Label existiert bereits")
        setattr(label, field, value)
    try:
        session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Label existiert bereits") from exc
    return label


@router.delete("/labels/{label_id}", status_code=204, tags=["labels"])
def delete_label(
    label_id: int, session: Session = Depends(get_session), viewer: Viewer = Depends(get_viewer)
):
    label = label_or_404(session, viewer, label_id)
    session.delete(label)
    return Response(status_code=204)


# ------------------------------------------------------------------ Dateien & Statistik
@router.get("/files", response_model=FileSearchResult, tags=["files"])
def search_files(
    q: str = "",
    ext: str = "",
    disk_id: int | None = None,
    label_id: int | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    client: str = Query("", description="Client-ID, an dem die Platte zuletzt hing, oder 'none'"),
    sort: str = "name",
    desc: bool = False,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    rows, total = queries.search_files(
        session,
        queries.FileQuery(
            q=q, extension=ext, disk_id=disk_id, label_id=label_id, min_size=min_size,
            max_size=max_size, sort=sort, descending=desc, limit=limit, offset=offset,
            visible=authz.visible_ids(viewer), client=client,
        ),
    )
    items = [
        FileOut(
            id=f.id, path=f.path, name=f.name, extension=f.extension, size=f.size, mtime=f.mtime,
            disk_id=d.id, disk_name=d.display_name, disk_connected=d.is_connected,
            volume_label=v.label, mountpoint=v.mountpoint,
        )
        for f, v, d in rows
    ]
    return FileSearchResult(total=total, items=items)


def _location(disk: Disk, volume, path: str) -> DuplicateLocation:
    return DuplicateLocation(
        disk_id=disk.id, disk_name=disk.display_name, disk_connected=disk.is_connected,
        volume_label=volume.label, path=path,
    )


@router.get("/duplicates/files", response_model=FileDuplicateResult, tags=["duplicates"])
def duplicate_files(
    ext: str = "",
    min_size: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    """Dateien mit gleichem Namen und gleicher Größe (aus dem Index, ohne Prüfsummen)."""
    groups, total, wasted = duplicates.find_file_duplicates(
        session,
        duplicates.FileDupQuery(
            min_size=min_size, extension=ext, limit=limit, offset=offset,
            visible=authz.visible_ids(viewer),
        ),
    )
    items = [
        FileDuplicateGroup(
            name=g.name, size=g.size, wasted_bytes=g.wasted,
            locations=[_location(d, v, f.path) for f, v, d in g.members],
        )
        for g in groups
    ]
    return FileDuplicateResult(total_groups=total, wasted_bytes=wasted, items=items)


@router.get("/duplicates/folders", response_model=FolderDuplicateResult, tags=["duplicates"])
def duplicate_folders(
    min_files: int = Query(2, ge=1),
    min_size: int = Query(0, ge=0),
    include_hidden: bool = False,
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    """Ordner mit identischem Inhalt (gleiche relative Pfade und Größen); größte zuerst."""
    groups, wasted = duplicates.find_folder_duplicates(
        session, duplicates.FolderDupQuery(
            min_files=min_files, min_size=min_size, include_hidden=include_hidden,
            visible=authz.visible_ids(viewer),
        )
    )
    items = [
        FolderDuplicateGroup(
            file_count=g.file_count, size=g.size, wasted_bytes=g.wasted,
            locations=[_location(r.disk, r.volume, r.path) for r in g.folders],
        )
        for g in groups
    ]
    return FolderDuplicateResult(total_groups=len(items), wasted_bytes=wasted, items=items)


@router.get("/activity", response_model=list[RunningIndexOut], tags=["system"])
def activity(session: Session = Depends(get_session), viewer: Viewer = Depends(get_viewer)):
    """Laufende Indizierungen angeschlossener Festplatten (Festplatte dann nicht abziehen)."""
    return [r.__dict__ for r in queries.running_indexes(session, authz.visible_ids(viewer))]


@router.get("/bays/live", tags=["system"])
def bays_live(
    host: str = "",
    client_id: int | None = None,
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    """Portbelegung (für den Schacht-Assistenten und die Live-Anzeige).

    Lokal (ohne Anmeldung): der Rechner `host` bzw. der erste, der Ports meldet. Mit Anmeldung:
    `client_id` (nur eigene Clients, sonst 404) oder ohne Angabe alle eigenen Clients mit
    Wechselschächten (`clients`; `occupied` ist dann die Vereinigung).
    """
    from diskatlas.services import bays as bay_service

    def payload(snapshot, label: str | None = None) -> dict:
        if snapshot is None:
            return {"host": label, "online": False, "ports": [], "occupied": []}
        return {
            "host": label or snapshot.host,
            "online": snapshot.is_online,
            "ports": snapshot.all_ports,
            "occupied": [p.__dict__ for p in snapshot.present],
        }

    if viewer.user_id is None:
        config = bay_service.load_config(session)
        return payload(bay_service.local_snapshot(session, config, host))
    own = list(session.scalars(
        select(Client).where(Client.user_id == viewer.user_id).order_by(Client.id)
    ))
    if client_id is not None:
        client = next((c for c in own if c.id == client_id), None)
        if client is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Client nicht gefunden")
        one = payload(bay_service.client_snapshot(session, client), client.nickname)
        return {**one, "clients": {str(client.id): one}}
    per = {
        str(c.id): payload(bay_service.client_snapshot(session, c), c.nickname)
        for c in own if c.has_bays
    }
    return {
        "host": None,
        "online": any(p["online"] for p in per.values()),
        "ports": [],
        "occupied": [{**o, "client_id": int(cid)} for cid, p in per.items()
                     for o in p["occupied"]],
        "clients": per,
    }


@router.get("/lookup", response_model=list[LookupOut], tags=["disks"])
def lookup(
    code: str = Query(..., min_length=1, max_length=500),
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    """Festplatte per Seriennummer/WWN finden (Barcode oder Text) und ihren Ort nennen."""
    result = []
    for disk in lookup_service.find_disks(session, code, authz.visible_ids(viewer))[:10]:
        where = lookup_service.whereabouts(session, disk)
        result.append(
            LookupOut(
                id=disk.id, name=disk.display_name, brand=disk.brand, model=disk.model,
                serial=disk.serial, size_bytes=disk.size_bytes, health=disk.health,
                is_connected=disk.is_connected, state=where.state, host=where.host,
                bay=where.bay, location=disk.location,
            )
        )
    return result


@router.get("/commands/recent", tags=["system"])
def commands_recent(
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    """Letzte Aufträge an Agenten mit Status (für die Live-Anzeige)."""
    return [
        {"id": c.id, "kind": c.kind, "status": c.status, "disk_key": c.disk_key, "result": c.result}
        for c in commands.recent(session, None, limit, viewer.scope_id)
    ]


@router.get("/stats", response_model=StatsOut, tags=["system"])
def stats(session: Session = Depends(get_session), viewer: Viewer = Depends(get_viewer)):
    return queries.dashboard_stats(queries.load_disks(session, authz.visible_ids(viewer))).__dict__


# ------------------------------------------------------------------ Benutzer
@router.get("/users", response_model=list[UserOut], tags=["users"])
def list_users(
    session: Session = Depends(get_session), _: User = Depends(get_current_user)
):
    """Namen der freigeschalteten Benutzer und ihrer Clients (nie Passwörter oder Tokens)."""
    rows = session.scalars(
        select(User).where(User.status == "active").order_by(func.lower(User.nickname))
    )
    return [
        UserOut(id=u.id, nickname=u.nickname, is_master=u.is_master,
                clients=[c.nickname for c in u.clients])
        for u in rows
    ]


# ------------------------------------------------------------------ Ingest (für Agenten)
ingest_router = APIRouter(
    prefix="/ingest", tags=["ingest"], dependencies=[Depends(require_client)]
)


def _owner(client: Client | None) -> int | None:
    """Benutzer des Clients; None = ohne Anmeldung (lokal), dann gibt es keine Beschränkung."""
    return client.user_id if client else None


@ingest_router.post("/disk")
def ingest_disk(
    body: IngestDisk,
    session: Session = Depends(get_session),
    client: Client | None = Depends(require_client),
):
    disk = ingest.upsert_disk(session, body.host, body.disk, client=client)
    pending = client is not None and disk.owner_user_id != client.user_id
    return {"disk_id": disk.id, "transfer_pending": pending}


@ingest_router.post("/connected")
def ingest_connected(
    body: IngestConnected,
    session: Session = Depends(get_session),
    client: Client | None = Depends(require_client),
):
    ingest.mark_connected(session, body.host, body.disk_keys, user_id=_owner(client),
                          client_id=client.id if client else None)
    copies.sweep(session)  # wartende Kopieraufträge: eine Platte ist jetzt (nicht mehr) da
    hosts.record(session, hosts.state_key(client, body.host), body.ports_info,
                 user_id=_owner(client))
    if client is not None and body.ports_info:
        from diskatlas.services import bays as bay_service

        bay_service.adopt_legacy(session, client, body.host)
    return {"ok": True}


@ingest_router.get("/commands")
def ingest_commands(
    host: str,
    session: Session = Depends(get_session),
    client: Client | None = Depends(require_client),
):
    """Offene Aufträge des Benutzers für den Agenten `host` (werden als „läuft“ markiert)."""
    return commands.claim_pending(session, host, user_id=_owner(client))


@ingest_router.post("/commands/{command_id}/result")
def ingest_command_result(
    command_id: int,
    body: CommandResult,
    session: Session = Depends(get_session),
    client: Client | None = Depends(require_client),
):
    _lookup(commands.finish, session, command_id, body.ok, body.message,
            user_id=_owner(client))
    return {"ok": True}


def _lookup(func, session: Session, *args, **kwargs):
    try:
        return func(session, *args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@ingest_router.post("/index/begin")
def ingest_begin(
    body: IngestVolumeRef,
    session: Session = Depends(get_session),
    client: Client | None = Depends(require_client),
):
    scan_id = _lookup(ingest.begin_index, session, body.disk_key, body.volume_key,
                      user_id=_owner(client))
    return {"scan_id": scan_id}


@ingest_router.post("/index/files")
def ingest_files(
    body: IngestFiles,
    session: Session = Depends(get_session),
    client: Client | None = Depends(require_client),
):
    records = [FileRecord(*f) for f in body.files]
    count = _lookup(
        ingest.add_files, session, body.disk_key, body.volume_key, body.scan_id, records,
        user_id=_owner(client),
    )
    return {"added": count}


@ingest_router.post("/index/finish")
def ingest_finish(
    body: IngestFinish,
    session: Session = Depends(get_session),
    client: Client | None = Depends(require_client),
):
    volume = _lookup(
        ingest.finish_index, session, body.disk_key, body.volume_key, body.scan_id,
        body.errors, body.success, user_id=_owner(client),
    )
    return {"file_count": volume.file_count}


router.include_router(ingest_router)
