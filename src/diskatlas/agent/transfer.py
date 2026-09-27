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
import time
from collections.abc import Callable
from datetime import datetime

from diskatlas.agent import relaycrypto, safepath
from diskatlas.agent.sinks import RelayGone, Sink
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


# ------------------------------------------------------------------ Relay (verschlüsselt, seriell)
IDLE_TIMEOUT = 600.0  # so lange darf die Gegenseite schweigen, bevor wir aufgeben
POLL = 0.5


def _gone(exc: RelayGone) -> TransferAborted:
    return TransferAborted(str(exc))


def send_relay(
    job: dict, disks: list[DiskInfo], sink: Sink, progress: Callable[[int], bool],
    stop: Callable[[], bool] = lambda: False, sleep: Callable[[float], object] = time.sleep,
) -> tuple[int, str]:
    """Liest die Quelle, verschlüsselt sie stückweise und schickt sie über den Server.

    Es liegt immer nur ein Stück im Relay: das nächste geht erst hoch, wenn der Empfänger das vorige
    abgeholt hat. Gibt (Größe, Hinweis) zurück; „fertig“ meldet der Empfänger."""
    if not relaycrypto.AVAILABLE:
        raise TransferFailed("Verschlüsselung nicht verfügbar (Paket „cryptography“ fehlt).")
    _, src_vol = find_volume(disks, job["source"]["disk_key"], job["source"]["volume_key"])
    try:
        handle, info = safepath.open_source(src_vol.mountpoint, job["source"]["path"])
    except safepath.UnsafePath as exc:
        raise TransferFailed(str(exc)) from exc
    note = "" if info.st_size == job.get("size") else (
        "Die Datei hat sich seit der Indizierung geändert (Größe).")
    item = job["id"]
    sealer = relaycrypto.Sealer(job["target_pubkey"], item)
    digest, sent, index = hashlib.sha256(), 0, 0

    def put(blob: bytes, final: bool) -> None:
        waited = 0.0
        while True:
            if stop():
                raise TransferAborted
            try:
                result = sink.relay_put(item, index, blob, sealer.epk if index == 0 else None,
                                        final)
            except RelayGone as exc:
                raise _gone(exc) from exc
            if result == "ok":
                return
            if waited >= IDLE_TIMEOUT:
                raise TransferFailed("Der Empfänger holt die Datei nicht ab.", retry=True)
            sleep(POLL)
            waited += POLL

    with handle:
        while chunk := handle.read(CHUNK):
            put(sealer.seal(index, chunk), False)
            digest.update(chunk)
            sent += len(chunk)
            index += 1
            if not progress(sent):
                raise TransferAborted
        put(sealer.seal_trailer(index, digest.digest(), sent), True)
    waited = 0.0
    while True:  # auf das Quittieren des letzten Stücks warten (hält die Lease am Leben)
        try:
            state = sink.relay_status(item)
        except RelayGone as exc:
            raise _gone(exc) from exc
        if state.get("abort"):
            raise TransferAborted
        if state.get("next", 0) > index and not state.get("pending"):
            return sent, note
        if stop() or waited >= IDLE_TIMEOUT:
            raise TransferFailed("Der Empfänger hat die Datei nicht abgeschlossen.", retry=True)
        sleep(POLL)
        waited += POLL


def receive_relay(
    job: dict, disks: list[DiskInfo], sink: Sink, private_key, progress: Callable[[int], bool],
    stop: Callable[[], bool] = lambda: False, sleep: Callable[[float], object] = time.sleep,
) -> tuple[str, int, str, str]:
    """Holt die verschlüsselten Stücke ab, prüft und schreibt die Datei ins Zielverzeichnis.
    Ergebnis wie bei `copy_local`: (sha256, Größe, Name, Hinweis)."""
    if not relaycrypto.AVAILABLE:
        raise TransferFailed("Verschlüsselung nicht verfügbar (Paket „cryptography“ fehlt).")
    _, dst_vol = find_volume(disks, job["target"]["disk_key"], job["target"]["volume_key"])
    try:
        directory = safepath.target_dir(dst_vol.mountpoint, job["target"]["path"])
    except safepath.UnsafePath as exc:
        raise TransferFailed(str(exc)) from exc
    except OSError as exc:
        raise TransferFailed(
            f"Zielordner nicht nutzbar: {exc.strerror or exc}", retry=True
        ) from exc
    if shutil.disk_usage(directory).free < (job.get("size") or 0) + FREE_MARGIN:
        raise TransferFailed("Nicht genug freier Platz auf der Zielplatte.")
    item = job["id"]
    part = safepath.part_path(directory, item)
    opener, index, written, digest, waited = None, 0, 0, hashlib.sha256(), 0.0
    try:
        with open(part, "wb") as out:
            while True:
                if stop():
                    raise TransferAborted
                try:
                    got = sink.relay_get(item, index)
                except RelayGone as exc:
                    raise _gone(exc) from exc
                if got is None:
                    if waited >= IDLE_TIMEOUT:
                        raise TransferFailed("Der Sender liefert nichts mehr.", retry=True)
                    sleep(POLL)
                    waited += POLL
                    continue
                waited = 0.0
                blob, epk, final = got
                try:
                    if opener is None:
                        opener = relaycrypto.Opener(private_key, epk, item)
                    if final:
                        want, size = opener.open_trailer(index, blob)
                    else:
                        plain = opener.open(index, blob)
                except relaycrypto.CryptoError as exc:
                    raise TransferFailed(f"Entschlüsselung fehlgeschlagen: {exc}") from exc
                if final:
                    if want != digest.digest() or size != written:
                        raise TransferFailed("Prüfsumme stimmt nicht – Datei verworfen.")
                    _ack(sink, item, index)
                    break
                out.write(plain)
                digest.update(plain)
                written += len(plain)
                _ack(sink, item, index)
                index += 1
                if not progress(written):
                    raise TransferAborted
            out.flush()
            os.fsync(out.fileno())
        mtime = _mtime(job)
        if mtime is not None:
            os.utime(part, (mtime, mtime))
        name = safepath.finalize(part, directory, job["name"])
    except OSError as exc:
        _drop(part)
        raise TransferFailed(
            f"Schreiben fehlgeschlagen: {exc.strerror or exc}", retry=True
        ) from exc
    except BaseException:
        _drop(part)
        raise
    note = "" if written == job.get("size") else "Die Datei hat sich seit der Indizierung geändert."
    return digest.hexdigest(), written, name, note


def _ack(sink: Sink, item: str, index: int) -> None:
    try:
        sink.relay_ack(item, index)
    except RelayGone as exc:
        raise _gone(exc) from exc
