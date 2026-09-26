"""Übertragungsaufträge im Agenten ausführen (nur mit `allow_transfer`).

Laufwerke und Pfade stammen aus der eigenen Erkennung des Agenten, nie aus dem Auftrag;
Systemvolumes sind ausgenommen; alle Pfade laufen durch `safepath`.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import shutil
from collections.abc import Callable
from datetime import datetime

from diskatlas.agent import safepath
from diskatlas.probe.types import DiskInfo, VolumeInfo

log = logging.getLogger(__name__)

CHUNK = 8 * 1024 * 1024
FREE_MARGIN = 64 * 1024 * 1024  # so viel bleibt mindestens frei


class TransferFailed(Exception):
    """Die Übertragung ist gescheitert; `retry` = ein neuer Versuch kann sich lohnen."""

    def __init__(self, message: str, retry: bool = False):
        super().__init__(message)
        self.retry = retry


class TransferAborted(Exception):
    """Der Server hat die Übertragung abgebrochen (zurückgezogen oder nicht mehr erlaubt)."""


def find_volume(
    disks: list[DiskInfo], disk_key: str, volume_key: str | None
) -> tuple[DiskInfo, VolumeInfo]:
    """Volume aus dem eigenen Stand; nur eingehängte Nicht-System-Volumes."""
    for disk in disks:
        if disk.key != disk_key:
            continue
        for volume in disk.volumes:
            if volume_key is not None and volume.key != volume_key:
                continue
            if not volume.mountpoint:
                raise TransferFailed("Die Platte ist nicht eingehängt.", retry=True)
            if volume.is_system:
                raise TransferFailed("Systemvolumes werden nicht kopiert.")
            return disk, volume
    raise TransferFailed("Die Platte ist an diesem Rechner nicht angeschlossen.", retry=True)


def _mtime(job: dict) -> float | None:
    raw = job.get("mtime")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def copy_local(
    job: dict, disks: list[DiskInfo], progress: Callable[[int], bool],
    stop: Callable[[], bool] = lambda: False,
) -> tuple[str, int, str, str]:
    """Kopiert Quelle → Ziel auf diesem Rechner. `progress(bytes)` meldet an den Server und gibt
    False zurück, wenn abgebrochen werden soll. Ergebnis: (sha256, Größe, Name, Hinweis)."""
    _, src_vol = find_volume(disks, job["source"]["disk_key"], job["source"]["volume_key"])
    _, dst_vol = find_volume(disks, job["target"]["disk_key"], job["target"]["volume_key"])
    try:
        handle, info = safepath.open_source(src_vol.mountpoint, job["source"]["path"])
        directory = safepath.target_dir(dst_vol.mountpoint, job["target"]["path"])
    except safepath.UnsafePath as exc:
        raise TransferFailed(str(exc)) from exc
    except OSError as exc:
        raise TransferFailed(
            f"Zielordner nicht nutzbar: {exc.strerror or exc}", retry=True
        ) from exc
    note = ""
    with handle:
        if info.st_size != job.get("size"):
            note = "Die Datei hat sich seit der Indizierung geändert (Größe)."
        if shutil.disk_usage(directory).free < info.st_size + FREE_MARGIN:
            raise TransferFailed("Nicht genug freier Platz auf der Zielplatte.")
        part = safepath.part_path(directory, job["id"])
        digest, done = hashlib.sha256(), 0
        try:
            with open(part, "wb") as out:
                while chunk := handle.read(CHUNK):
                    if stop():
                        raise TransferAborted
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if not progress(done):
                        raise TransferAborted
                out.flush()
                os.fsync(out.fileno())
            mtime = _mtime(job) or info.st_mtime
            os.utime(part, (mtime, mtime))
            name = safepath.finalize(part, directory, job["name"])
        except OSError as exc:
            _drop(part)
            raise TransferFailed(f"Schreiben fehlgeschlagen: {exc.strerror or exc}",
                                 retry=True) from exc
        except BaseException:
            _drop(part)
            raise
    return digest.hexdigest(), done, name, note


def _drop(part: str) -> None:
    with contextlib.suppress(OSError):
        os.unlink(part)
