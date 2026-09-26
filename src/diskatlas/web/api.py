"""REST-API unter /api/v1 (Dokumentation: /docs)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from diskatlas import __version__
from diskatlas.db.models import Disk, Label
from diskatlas.probe.types import FileRecord
from diskatlas.services import commands, duplicates, hosts, ingest, queries
from diskatlas.web.deps import get_session, require_ingest_token
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
    RunningIndexOut,
    StatsOut,
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
    sort: str = "name",
    desc: bool = False,
    session: Session = Depends(get_session),
):
    disks = queries.filter_disks(
        queries.load_disks(session),
        queries.DiskFilter(
            q=q, health=health, connected=connected, label_id=label_id, fs=fs, usage=usage
        ),
    )
    return queries.sort_disks(disks, sort, desc)


def _disk_or_404(session: Session, disk_id: int) -> Disk:
    disk = queries.get_disk(session, disk_id)
    if disk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Festplatte nicht gefunden")
    return disk


@router.get("/disks/{disk_id}", response_model=DiskDetailOut, tags=["disks"])
def get_disk(disk_id: int, session: Session = Depends(get_session)):
    return _disk_or_404(session, disk_id)


@router.patch("/disks/{disk_id}", response_model=DiskDetailOut, tags=["disks"])
def patch_disk(disk_id: int, body: DiskPatch, session: Session = Depends(get_session)):
    disk = _disk_or_404(session, disk_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(disk, field, (value or "").strip() or None)
    session.flush()
    return disk


@router.delete("/disks/{disk_id}", status_code=204, tags=["disks"])
def delete_disk(disk_id: int, session: Session = Depends(get_session)):
    session.delete(_disk_or_404(session, disk_id))
    return Response(status_code=204)


@router.put("/disks/{disk_id}/labels", response_model=DiskDetailOut, tags=["disks"])
def set_disk_labels(
    disk_id: int, body: LabelAssignment, session: Session = Depends(get_session)
):
    disk = _disk_or_404(session, disk_id)
    labels = list(session.scalars(select(Label).where(Label.id.in_(body.label_ids))))
    if len(labels) != len(set(body.label_ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unbekannte Label-ID")
    disk.labels = labels
    session.flush()
    return disk


# ------------------------------------------------------------------ Labels
@router.get("/labels", response_model=list[LabelOut], tags=["labels"])
def list_labels(session: Session = Depends(get_session)):
    return [label for label, _ in queries.list_labels(session)]


@router.post("/labels", response_model=LabelOut, status_code=201, tags=["labels"])
def create_label(body: LabelIn, session: Session = Depends(get_session)):
    label = Label(name=body.name.strip(), category=(body.category or "").strip() or None,
                  color=body.color)
    session.add(label)
    try:
        session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Label existiert bereits") from exc
    return label


@router.patch("/labels/{label_id}", response_model=LabelOut, tags=["labels"])
def patch_label(label_id: int, body: LabelPatch, session: Session = Depends(get_session)):
    label = session.get(Label, label_id)
    if label is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Label nicht gefunden")
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "category":
            value = (value or "").strip() or None
        setattr(label, field, value)
    try:
        session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Label existiert bereits") from exc
    return label


@router.delete("/labels/{label_id}", status_code=204, tags=["labels"])
def delete_label(label_id: int, session: Session = Depends(get_session)):
    label = session.get(Label, label_id)
    if label is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Label nicht gefunden")
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
    sort: str = "name",
    desc: bool = False,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    rows, total = queries.search_files(
        session,
        queries.FileQuery(
            q=q, extension=ext, disk_id=disk_id, label_id=label_id, min_size=min_size,
            max_size=max_size, sort=sort, descending=desc, limit=limit, offset=offset,
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
):
    """Dateien mit gleichem Namen und gleicher Größe (aus dem Index, ohne Prüfsummen)."""
    groups, total, wasted = duplicates.find_file_duplicates(
        session,
        duplicates.FileDupQuery(min_size=min_size, extension=ext, limit=limit, offset=offset),
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
):
    """Ordner mit identischem Inhalt (gleiche relative Pfade und Größen); größte zuerst."""
    groups, wasted = duplicates.find_folder_duplicates(
        session, duplicates.FolderDupQuery(
            min_files=min_files, min_size=min_size, include_hidden=include_hidden
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
def activity(session: Session = Depends(get_session)):
    """Laufende Indizierungen angeschlossener Festplatten (Festplatte dann nicht abziehen)."""
    return [r.__dict__ for r in queries.running_indexes(session)]


@router.get("/bays/live", tags=["system"])
def bays_live(host: str = "", session: Session = Depends(get_session)):
    """Portbelegung des Agenten-Rechners (für den Schacht-Assistenten und die Live-Anzeige)."""
    from diskatlas.services import bays as bay_service

    chosen = host or bay_service.load_config(session).host or ""
    snapshot = hosts.get(session, chosen) if chosen else None
    if snapshot is None:  # noch keine Zuordnung: erster Rechner, der Ports meldet
        snapshot = next((h for h in hosts.known_hosts(session) if h.all_ports), None)
    if snapshot is None:
        return {"host": None, "online": False, "ports": [], "occupied": []}
    return {
        "host": snapshot.host,
        "online": snapshot.is_online,
        "ports": snapshot.all_ports,
        "occupied": [p.__dict__ for p in snapshot.present],
    }


@router.get("/commands/recent", tags=["system"])
def commands_recent(limit: int = Query(20, ge=1, le=100), session: Session = Depends(get_session)):
    """Letzte Aufträge an Agenten mit Status (für die Live-Anzeige)."""
    return [
        {"id": c.id, "kind": c.kind, "status": c.status, "disk_key": c.disk_key, "result": c.result}
        for c in commands.recent(session, None, limit)
    ]


@router.get("/stats", response_model=StatsOut, tags=["system"])
def stats(session: Session = Depends(get_session)):
    return queries.dashboard_stats(queries.load_disks(session)).__dict__


# ------------------------------------------------------------------ Ingest (für Agenten)
ingest_router = APIRouter(
    prefix="/ingest", tags=["ingest"], dependencies=[Depends(require_ingest_token)]
)


@ingest_router.post("/disk")
def ingest_disk(body: IngestDisk, session: Session = Depends(get_session)):
    disk = ingest.upsert_disk(session, body.host, body.disk)
    return {"disk_id": disk.id}


@ingest_router.post("/connected")
def ingest_connected(body: IngestConnected, session: Session = Depends(get_session)):
    ingest.mark_connected(session, body.host, body.disk_keys)
    hosts.record(session, body.host, body.ports_info)
    return {"ok": True}


@ingest_router.get("/commands")
def ingest_commands(host: str, session: Session = Depends(get_session)):
    """Offene Aufträge für den Agenten `host` (werden dabei als „läuft“ markiert)."""
    return commands.claim_pending(session, host)


@ingest_router.post("/commands/{command_id}/result")
def ingest_command_result(
    command_id: int, body: CommandResult, session: Session = Depends(get_session)
):
    _lookup(commands.finish, session, command_id, body.ok, body.message)
    return {"ok": True}


def _lookup(func, session: Session, *args):
    try:
        return func(session, *args)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@ingest_router.post("/index/begin")
def ingest_begin(body: IngestVolumeRef, session: Session = Depends(get_session)):
    return {"scan_id": _lookup(ingest.begin_index, session, body.disk_key, body.volume_key)}


@ingest_router.post("/index/files")
def ingest_files(body: IngestFiles, session: Session = Depends(get_session)):
    records = [FileRecord(*f) for f in body.files]
    count = _lookup(
        ingest.add_files, session, body.disk_key, body.volume_key, body.scan_id, records
    )
    return {"added": count}


@ingest_router.post("/index/finish")
def ingest_finish(body: IngestFinish, session: Session = Depends(get_session)):
    volume = _lookup(
        ingest.finish_index, session, body.disk_key, body.volume_key, body.scan_id,
        body.errors, body.success,
    )
    return {"file_count": volume.file_count}


router.include_router(ingest_router)
