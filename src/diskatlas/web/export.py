"""Export der Plattenliste und von Dateilisten als CSV (für Tabellenkalkulationen) oder JSON.

Es gelten dieselben Filter und Sichtrechte wie in der Oberfläche. Dateilisten werden gestreamt,
damit auch Hunderttausende Zeilen ohne großen Speicherbedarf gehen.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from diskatlas.services import authz, queries
from diskatlas.services.authz import Viewer
from diskatlas.web.deps import get_session, get_viewer
from diskatlas.web.views import _excluded_disks

router = APIRouter(include_in_schema=False)
MAX_FILE_ROWS = 2_000_000
_BATCH = 2000

DISK_COLUMNS = [
    ("name", "Name"), ("vendor", "Hersteller"), ("model", "Modell"), ("serial", "Seriennummer"),
    ("size_bytes", "Größe (Bytes)"), ("used_bytes", "Belegt (Bytes)"),
    ("free_bytes", "Frei (Bytes)"), ("filesystems", "Dateisysteme"), ("health", "Gesundheit"),
    ("temperature_c", "Temperatur (°C)"), ("power_on_hours", "Betriebsstunden"),
    ("files", "Dateien"), ("labels", "Labels"), ("location", "Lagerort"), ("notes", "Notizen"),
    ("connected", "Angeschlossen"), ("last_seen", "Zuletzt gesehen"),
    ("last_client", "Zuletzt an Client"),
]
FILE_COLUMNS = [
    ("disk", "Festplatte"), ("volume", "Partition"), ("path", "Pfad"), ("name", "Name"),
    ("extension", "Endung"), ("size", "Größe (Bytes)"), ("mtime", "Geändert"),
]


def _text(value) -> str:
    """Zellwert für CSV; Formeln (=, +, -, @ am Anfang) werden entschärft."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _json_value(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _csv_line(values: list) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";", lineterminator="\r\n").writerow(
        [_text(v) for v in values])
    return buffer.getvalue()


def _disk_row(disk) -> dict:
    return {
        "name": disk.display_name, "vendor": disk.brand, "model": disk.model,
        "serial": disk.serial, "size_bytes": disk.size_bytes, "used_bytes": disk.used_bytes,
        "free_bytes": disk.free_bytes, "filesystems": ", ".join(disk.fs_types),
        "health": disk.health, "temperature_c": disk.temperature_c,
        "power_on_hours": disk.power_on_hours, "files": disk.file_count,
        "labels": ", ".join(sorted(lab.name for lab in disk.labels)),
        "location": disk.location, "notes": disk.notes, "connected": disk.is_connected,
        "last_seen": disk.last_seen, "last_client": disk.last_client_label,
    }


def _download(name: str, fmt: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{name}.{fmt}"',
            "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}


def _format(fmt: str) -> str:
    if fmt not in ("csv", "json"):
        raise HTTPException(404, "Unbekanntes Format")
    return fmt


@router.get("/export/disks.{fmt}")
def export_disks(
    request: Request,
    fmt: str,
    q: str = "", health: str = "", connected: str = "", label: str = "", fs: str = "",
    usage: str = "", client: str = "", sort: str = "name", desc: bool = False,
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    fmt = _format(fmt)
    flt = queries.DiskFilter(q=q, health=health, connected=connected,
                             label_id=int(label) if label.isdigit() else None, fs=fs,
                             usage=usage, client=client)
    hidden = set(_excluded_disks(request, session, viewer))  # wie im Dashboard (Systemplatten)
    disks = queries.sort_disks(queries.filter_disks(
        [d for d in queries.load_disks(session, authz.visible_ids(viewer)) if d.id not in hidden],
        flt), sort, desc)
    rows = [_disk_row(d) for d in disks]
    stamp = datetime.now().strftime("%Y-%m-%d")
    if fmt == "json":
        body = json.dumps([{k: _json_value(v) for k, v in r.items()} for r in rows],
                          ensure_ascii=False, indent=1)
        return Response(body, media_type="application/json",
                        headers=_download(f"diskatlas-festplatten-{stamp}", "json"))
    text = "﻿" + _csv_line([title for _, title in DISK_COLUMNS]) + "".join(
        _csv_line([r[key] for key, _ in DISK_COLUMNS]) for r in rows)
    return Response(text.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers=_download(f"diskatlas-festplatten-{stamp}", "csv"))


@router.get("/export/files.{fmt}")
def export_files(
    request: Request,
    fmt: str,
    q: str = "", ext: str = "", disk: str = "", label: str = "", min_mb: str = "",
    client: str = "", sort: str = "path", desc: bool = False,
    session: Session = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    fmt = _format(fmt)
    hidden = _excluded_disks(request, session, viewer)
    try:
        min_size = int(float(min_mb.replace(",", ".")) * 1_000_000) if min_mb else None
    except ValueError:
        min_size = None
    if not any([q, ext, disk, label, min_size, client]):
        raise HTTPException(400, "Bitte einen Filter angeben (Suchbegriff, Endung, Platte …).")
    fq = queries.FileQuery(
        q=q, extension=ext, disk_id=int(disk) if disk.isdigit() else None,
        label_id=int(label) if label.isdigit() else None, min_size=min_size, sort=sort,
        descending=desc, limit=MAX_FILE_ROWS, visible=authz.visible_ids(viewer), client=client,
        exclude_disk_ids=tuple(hidden),
    )
    db = request.app.state.db

    def rows() -> Iterator[dict]:
        # eigene Sitzung: die Antwort läuft weiter, nachdem der Endpunkt zurückgekehrt ist
        with db.session() as session:
            for file, volume, disk_ in queries.iter_files(session, fq, _BATCH):
                yield {"disk": disk_.display_name, "volume": volume.label or volume.mountpoint,
                       "path": file.path, "name": file.name, "extension": file.extension,
                       "size": file.size, "mtime": file.mtime}

    def as_csv() -> Iterator[bytes]:
        yield ("﻿" + _csv_line([title for _, title in FILE_COLUMNS])).encode("utf-8")
        for row in rows():
            yield _csv_line([row[key] for key, _ in FILE_COLUMNS]).encode("utf-8")

    def as_json() -> Iterator[bytes]:
        yield b"["
        for i, row in enumerate(rows()):
            yield (("," if i else "") + "\n" + json.dumps(
                {k: _json_value(v) for k, v in row.items()}, ensure_ascii=False)).encode("utf-8")
        yield b"\n]\n"

    stamp = datetime.now().strftime("%Y-%m-%d")
    media = "text/csv; charset=utf-8" if fmt == "csv" else "application/json"
    return StreamingResponse(as_csv() if fmt == "csv" else as_json(), media_type=media,
                             headers=_download(f"diskatlas-dateien-{stamp}", fmt))
