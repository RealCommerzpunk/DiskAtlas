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

    def __init__(self, message: str, retry: bool = False, notify: bool = False,
                 restart: bool = False):
        super().__init__(message)
        self.retry = retry
        self.notify = notify  # auch als Meldung am Rechner zeigen (Sicherheitsrelevantes)
        self.restart = restart  # Fortsetzen unmöglich: Relay-Stand verwerfen, von vorn beginnen


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


FINAL_STATES = ("done", "failed", "denied", "cancelled", "expired")


def _gone(exc: RelayGone) -> TransferAborted:
    return TransferAborted(str(exc))


def _finished_for_good(sink: Sink, item: str, exc: BaseException) -> bool:
    """Soll der Fortsetzen-Zustand (Schlüssel, Teildatei) weg? Nur wenn die Übertragung
    endgültig vorbei ist – nicht, wenn sie nur erneut eingereiht wurde."""
    if isinstance(exc, TransferFailed):
        return not exc.retry or exc.restart
    if isinstance(exc, TransferAborted):
        try:
            state = sink.transfer_state(item)
        except Exception:
            return False  # Server nicht erreichbar: lieber behalten (wird später aufgeräumt)
        return state is None or state in FINAL_STATES
    return False


def send_relay(
    job: dict, disks: list[DiskInfo], sink: Sink, progress: Callable[[int], bool],
    stop: Callable[[], bool] = lambda: False, sleep: Callable[[float], object] = time.sleep,
    known_peers=None, resume=None,
) -> tuple[int, str]:
    """Liest die Quelle, verschlüsselt sie stückweise und schickt sie über den Server.

    Es liegt immer nur ein Stück im Relay: das nächste geht erst hoch, wenn der Empfänger das vorige
    abgeholt hat. Nach einem Abbruch geht es ab `job["resume_from"]` weiter (gleicher Schlüssel,
    unveränderte Quelle). Gibt (Größe, Hinweis) zurück; „fertig“ meldet der Empfänger."""
    if not relaycrypto.AVAILABLE:
        raise TransferFailed("Verschlüsselung nicht verfügbar (Paket „cryptography“ fehlt).")
    if known_peers is not None and job.get("target_client_id") is not None:
        verdict = known_peers.check(job["target_client_id"], job.get("target_pubkey", ""),
                                    job.get("target_label", ""))
        if verdict == "changed":
            raise TransferFailed(
                f"Der Schlüssel des Zielrechners „{job.get('target_label', '?')}“ hat sich "
                "geändert. Nicht gesendet. Wurde der Agent dort neu eingerichtet, den alten "
                "Schlüssel in den Agent-Einstellungen (Sicherheit) vergessen.", notify=True)
    _, src_vol = find_volume(disks, job["source"]["disk_key"], job["source"]["volume_key"])
    try:
        handle, info = safepath.open_source(src_vol.mountpoint, job["source"]["path"])
    except safepath.UnsafePath as exc:
        raise TransferFailed(str(exc)) from exc
    note = "" if info.st_size == job.get("size") else (
        "Die Datei hat sich seit der Indizierung geändert (Größe).")
    item = job["id"]
    resume_from = max(0, int(job.get("resume_from") or 0))
    source_sig = {"size": info.st_size, "mtime_ns": info.st_mtime_ns,
                  "target_pubkey": job["target_pubkey"]}
    if resume_from:
        saved = resume.load("send", item) if resume is not None else None
        if (not saved or saved.get("epk") != job.get("epk")
                or any(saved.get(k) != v for k, v in source_sig.items())):
            handle.close()
            raise TransferFailed("Fortsetzen nicht möglich (Schlüssel fehlt oder Quelle "
                                 "geändert) – beginnt von vorn.", retry=True, restart=True)
        sealer = relaycrypto.Sealer(job["target_pubkey"], item, saved["key"])
    else:
        sealer = relaycrypto.Sealer(job["target_pubkey"], item)
        if resume is not None:
            resume.save("send", item, {"key": sealer.ephemeral_b64, "epk": sealer.epk,
                                       **source_sig})
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

    try:
        with handle:
            while index < resume_from:  # schon übertragene Stücke nur für die Prüfsumme lesen
                chunk = handle.read(CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                sent += len(chunk)
                index += 1
            if index >= resume_from:  # sonst liegt der Abschluss schon im Relay
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
                break
            if stop() or waited >= IDLE_TIMEOUT:
                raise TransferFailed("Der Empfänger hat die Datei nicht abgeschlossen.",
                                     retry=True)
            sleep(POLL)
            waited += POLL
    except BaseException as exc:
        if resume is not None and _finished_for_good(sink, item, exc):
            resume.drop("send", item)
        raise
    if resume is not None:
        resume.drop("send", item)
    return sent, note


def receive_relay(
    job: dict, disks: list[DiskInfo], sink: Sink, private_key, progress: Callable[[int], bool],
    stop: Callable[[], bool] = lambda: False, sleep: Callable[[float], object] = time.sleep,
    resume=None,
) -> tuple[str, int, str, str]:
    """Holt die verschlüsselten Stücke ab, prüft und schreibt die Datei ins Zielverzeichnis.

    Nach einem Abbruch geht es mit der behaltenen Teildatei ab `job["resume_from"]` weiter.
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
    resume_from = max(0, int(job.get("resume_from") or 0))
    opener, index, written, digest, waited = None, 0, 0, hashlib.sha256(), 0.0
    keep_part = False

    def remember() -> None:
        if resume is not None:
            resume.save("receive", item, {"part": part, "chunks": index, "bytes": written})

    try:
        if resume_from:
            saved = resume.load("receive", item) if resume is not None else None
            expected = None
            if saved and saved.get("part") == part:
                if resume_from == saved.get("chunks"):
                    expected = saved.get("bytes")
                elif resume_from < (saved.get("chunks") or 0):
                    expected = resume_from * CHUNK
            size_now = os.path.getsize(part) if os.path.isfile(part) else -1
            if expected is None or size_now < expected:
                raise TransferFailed("Fortsetzen nicht möglich (Teildatei fehlt) – beginnt "
                                     "von vorn.", retry=True, restart=True)
            out = open(part, "r+b")  # noqa: SIM115 - wird unten im with geschlossen
            out.truncate(expected)
            while block := out.read(CHUNK):  # Prüfsumme über das schon Geschriebene
                digest.update(block)
            out.seek(expected)
            index, written = resume_from, expected
        else:
            out = open(part, "wb")  # noqa: SIM115
        remember()
        with out:
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
                remember()
                if not progress(written):
                    raise TransferAborted
            out.flush()
            os.fsync(out.fileno())
        mtime = _mtime(job)
        if mtime is not None:
            os.utime(part, (mtime, mtime))
        name = safepath.finalize(part, directory, job["name"])
    except OSError as exc:
        keep_part = True
        raise TransferFailed(
            f"Schreiben fehlgeschlagen: {exc.strerror or exc}", retry=True
        ) from exc
    except BaseException as exc:
        keep_part = not _finished_for_good(sink, item, exc)
        raise
    finally:
        if not keep_part and os.path.exists(part):
            _drop(part)
        if not keep_part and resume is not None:
            resume.drop("receive", item)
    note = "" if written == job.get("size") else "Die Datei hat sich seit der Indizierung geändert."
    return digest.hexdigest(), written, name, note


def _ack(sink: Sink, item: str, index: int) -> None:
    try:
        sink.relay_ack(item, index)
    except RelayGone as exc:
        raise _gone(exc) from exc
