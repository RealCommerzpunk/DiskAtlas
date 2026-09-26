"""Der Agent erkennt Festplatten, liest SMART, indiziert Dateien und überwacht Hot-Plug."""

from __future__ import annotations

import logging
import queue
import shutil
import socket
import threading
import time
from collections.abc import Callable

from diskatlas.agent.sinks import Sink
from diskatlas.config import AgentConfig
from diskatlas.probe import list_disks
from diskatlas.probe import ports as sata
from diskatlas.probe.files import iter_files
from diskatlas.probe.smart import read_smart
from diskatlas.probe.types import DiskInfo, SmartInfo, VolumeInfo
from diskatlas.services import fslabel, mounting

log = logging.getLogger(__name__)

BATCH_SIZE = 5000
HEARTBEAT_SECONDS = 60.0

Prober = Callable[[], list[DiskInfo]]
SmartReader = Callable[[str], SmartInfo]


class UsageWatcher:
    """Erkennt Datenänderungen an der Belegung eines Volumes (statvfs, ohne Dateien zu lesen).

    `update` liefert True, sobald sich die Belegung geändert hat und danach `settle` Sekunden
    stabil blieb – dann ist ein Kopier-/Verschiebevorgang vermutlich beendet.
    """

    def __init__(self, settle: float):
        self.settle = settle
        self._state: dict[str, tuple[int, float, bool]] = {}  # key -> (used, geändert_um, offen)

    def update(self, key: str, used: int, now: float) -> bool:
        prev = self._state.get(key)
        if prev is None:
            self._state[key] = (used, now, False)  # Ausgangswert, kein Auslöser
            return False
        last_used, changed_at, dirty = prev
        if used != last_used:
            self._state[key] = (used, now, True)
            return False
        if dirty and now - changed_at >= self.settle:
            self._state[key] = (used, changed_at, False)
            return True
        return False

    def forget(self, prefix: str) -> None:
        for key in [k for k in self._state if k.startswith(prefix)]:
            del self._state[key]


def _volume_used(mountpoint: str) -> int | None:
    try:
        return shutil.disk_usage(mountpoint).used
    except OSError:
        return None


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        sink: Sink,
        prober: Prober | None = None,
        smart_reader: SmartReader | None = None,
        host: str | None = None,
        mounter: Callable[[str], object] | None = None,
    ):
        self.config = config
        self.sink = sink
        self.prober = prober or list_disks
        self.smart_reader = smart_reader or (
            lambda dev: read_smart(dev, config.smartctl_path, config.smart_use_sudo)
        )
        self.host = host or config.host_name or socket.gethostname()
        self.stop_event = threading.Event()
        self.mounter = mounter or mounting.mount
        self._enqueue: Callable[[str, str], None] | None = None
        self._mount_enabled = config.auto_mount and (mounter is not None or mounting.available())

    # ------------------------------------------------------------------ Einmal-Scan
    def scan_all(self, index_files: bool | None = None, only: set[str] | None = None) -> int:
        """Scannt alle (bzw. die in `only` genannten) Festplatten einmal. Gibt die Anzahl zurück."""
        disks = self.prober()
        self.sink.report_connected(self.host, [d.key for d in disks], sata.ports_info(disks))
        count = 0
        for disk in disks:
            if only and not ({disk.key, disk.device, disk.serial} & only):
                continue
            self.scan_disk(disk, index_files=index_files)
            count += 1
        return count

    def scan_disk(
        self, disk: DiskInfo, index_files: bool | None = None, read_smart: bool = True
    ) -> None:
        if index_files is None:
            index_files = self.config.index_files
        log.info("Scanne %s (%s, %s)", disk.device, disk.model or "?", disk.key)
        if read_smart:
            disk.smart = self.smart_reader(disk.smart_device or disk.device)
            if not disk.smart.available:
                log.warning("SMART für %s nicht verfügbar: %s", disk.device, disk.smart.error)
        if self.sink.report_disk(self.host, disk) is False:
            log.warning(
                "%s gehört einem anderen Benutzer des Servers. Übernahme beantragt – der "
                "bisherige Besitzer muss zustimmen (Weboberfläche: Übernahmen).", disk.key)
            return
        if not index_files:
            return
        for volume in disk.volumes:
            if not volume.mountpoint:
                continue
            if volume.is_system and not self.config.index_system_volumes:
                log.info("Überspringe Systemvolume %s", volume.mountpoint)
                continue
            if self.stop_event.is_set():
                return
            self.index_volume(disk, volume)

    def index_volume(self, disk: DiskInfo, volume: VolumeInfo) -> None:
        errors = 0

        def on_error(path: str, exc: OSError) -> None:
            nonlocal errors
            errors += 1
            log.debug("Nicht lesbar: %s (%s)", path, exc)

        started = time.monotonic()
        log.info("Indiziere %s …", volume.mountpoint)
        scan_id = self.sink.begin_index(disk.key, volume.key)
        batch = []
        total = 0
        try:
            for record in iter_files(
                volume.mountpoint,
                self.config.exclude_dirs,
                on_error=on_error,
                should_stop=self.stop_event.is_set,
            ):
                batch.append(record)
                if len(batch) >= BATCH_SIZE:
                    self.sink.add_files(disk.key, volume.key, scan_id, batch)
                    total += len(batch)
                    batch = []
            if batch:
                self.sink.add_files(disk.key, volume.key, scan_id, batch)
                total += len(batch)
            aborted = self.stop_event.is_set()
            self.sink.finish_index(disk.key, volume.key, scan_id, errors, success=not aborted)
        except Exception:
            log.exception("Indizierung von %s fehlgeschlagen", volume.mountpoint)
            try:
                self.sink.finish_index(disk.key, volume.key, scan_id, errors, success=False)
            except Exception:
                log.exception("Konnte fehlgeschlagene Indizierung nicht abschließen")
            return
        log.info(
            "%s: %d Dateien in %.1f s indiziert (%d Fehler)",
            volume.mountpoint,
            total,
            time.monotonic() - started,
            errors,
        )

    # ------------------------------------------------------------------ Überwachung
    def watch(self) -> None:
        """Läuft bis `stop()`: erkennt neue/entfernte Festplatten und plant Scans."""
        jobs: queue.Queue[tuple[str, str]] = queue.Queue()
        pending: set[tuple[str, str]] = set()
        current: dict[str, DiskInfo] = {}
        lock = threading.Lock()

        def enqueue(key: str, kind: str) -> None:
            with lock:
                if (key, kind) in pending or (key, "full") in pending:
                    return
                pending.add((key, kind))
            jobs.put((key, kind))

        def worker() -> None:
            while not self.stop_event.is_set():
                try:
                    key, kind = jobs.get(timeout=1)
                except queue.Empty:
                    continue
                with lock:
                    pending.discard((key, kind))
                    disk = current.get(key)
                if disk is None:
                    continue  # inzwischen wieder abgesteckt
                try:
                    if kind == "mount":
                        self._mount_volumes(disk)
                        continue
                    self.scan_disk(disk, index_files=None if kind == "full" else False)
                except Exception:
                    log.exception("Scan von %s fehlgeschlagen", key)

        worker_thread = threading.Thread(target=worker, name="diskatlas-scan", daemon=True)
        worker_thread.start()
        self._enqueue = enqueue
        command_thread = threading.Thread(
            target=self._command_loop, name="diskatlas-commands", daemon=True
        )
        command_thread.start()

        signatures: dict[str, tuple] = {}
        last_full: dict[str, float] = {}
        last_smart: dict[str, float] = {}
        previous: set[str] | None = None
        last_heartbeat = 0.0
        usage = UsageWatcher(self.config.change_settle_seconds)
        mount_tried: dict[str, float] = {}
        rescan = self.config.rescan_interval_hours * 3600
        smart_every = self.config.smart_interval_minutes * 60
        log.info("Überwache Festplatten (alle %.0f s) auf %s", self.config.poll_interval, self.host)

        while not self.stop_event.is_set():
            try:
                disks = self.prober()
            except Exception:
                log.exception("Festplattenerkennung fehlgeschlagen")
                self.stop_event.wait(self.config.poll_interval)
                continue
            now = time.monotonic()
            with lock:
                current.clear()
                current.update({d.key: d for d in disks})
            keys = set(current)

            if previous is not None:
                for key in keys - previous:
                    log.info("Neue Festplatte erkannt: %s (%s)", current[key].device, key)
                for key in previous - keys:
                    log.info("Festplatte entfernt: %s", key)
                    signatures.pop(key, None)
                    usage.forget(key + "|")
                    mount_tried.pop(key, None)
            if keys != previous or now - last_heartbeat >= HEARTBEAT_SECONDS:
                try:
                    self.sink.report_connected(self.host, sorted(keys), sata.ports_info(disks))
                    last_heartbeat = now
                except Exception:
                    log.exception("Verbindungsstatus konnte nicht gemeldet werden")
            previous = keys

            for disk in disks:
                if self._wants_mount(disk) and now - mount_tried.get(disk.key, -1e9) >= 600:
                    mount_tried[disk.key] = now
                    enqueue(disk.key, "mount")
                sig = disk.signature()
                if signatures.get(disk.key) != sig or now - last_full.get(disk.key, 0) >= rescan:
                    signatures[disk.key] = sig
                    last_full[disk.key] = last_smart[disk.key] = now
                    enqueue(disk.key, "full")
                elif self._data_changed(disk, usage, now):
                    log.info("Datenänderung auf %s erkannt – aktualisiere Index", disk.device)
                    last_full[disk.key] = last_smart[disk.key] = now
                    enqueue(disk.key, "full")
                elif now - last_smart.get(disk.key, 0) >= smart_every:
                    last_smart[disk.key] = now
                    enqueue(disk.key, "smart")

            self.stop_event.wait(self.config.poll_interval)

        worker_thread.join(timeout=10)
        command_thread.join(timeout=10)

    # ------------------------------------------------------------------ Aufträge vom Server
    def _command_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                pending = self.sink.fetch_commands(self.host)
            except Exception:
                log.debug("Aufträge konnten nicht abgeholt werden", exc_info=True)
                pending = []
            for command in pending:
                try:
                    ok, message = self.execute_command(command)
                except Exception as exc:  # ein Fehler darf die Schleife nie beenden
                    log.exception("Auftrag %s fehlgeschlagen", command.get("id"))
                    ok, message = False, f"Unerwarteter Fehler: {exc}"
                try:
                    self.sink.report_command(command["id"], ok, message)
                except Exception:
                    log.exception("Ergebnis von Auftrag %s nicht übermittelt", command.get("id"))
            self.stop_event.wait(min(max(self.config.poll_interval, 1.0), 3.0))

    def _find_volume(self, disk_key: str, volume_key: str):
        for disk in self.prober():
            if disk.key == disk_key:
                for volume in disk.volumes:
                    if volume.key == volume_key:
                        return disk, volume
        return None, None

    def execute_command(self, command: dict) -> tuple[bool, str]:
        """Führt einen Auftrag aus. Geräte werden aus dem eigenen Stand ermittelt, nie aus dem
        Auftrag übernommen."""
        kind, payload = command.get("kind"), command.get("payload") or {}
        if kind == "rescan":
            key = payload.get("disk_key")
            if key not in {d.key for d in self.prober()}:
                return False, "Die Festplatte ist an diesem Rechner nicht angeschlossen."
            if self._enqueue is None:
                return False, "Der Agent überwacht gerade nicht."
            self._enqueue(key, "full")
            return True, "Neu-Scan eingeplant."
        if kind == "rename_label":
            disk, volume = self._find_volume(
                payload.get("disk_key", ""), payload.get("volume_key", "")
            )
            if volume is None:
                return False, "Das Volume wurde an diesem Rechner nicht gefunden."
            if volume.is_system:
                return False, "Systemvolumes werden nicht umbenannt."
            try:
                written = fslabel.set_label(
                    volume.device, volume.fs_type, volume.mountpoint, str(payload.get("label", ""))
                )
            except fslabel.LabelError as exc:
                return False, str(exc)
            refreshed, _ = self._find_volume(disk.key, volume.key)
            if refreshed is not None:  # neue Bezeichnung sofort an den Server melden
                self.scan_disk(refreshed, index_files=False, read_smart=False)
            done = f"Bezeichnung geändert: „{written}“." if written else "Bezeichnung entfernt."
            return True, done
        return False, f"Unbekannter Auftrag: {kind}"

    def _mountable(self, volume: VolumeInfo) -> bool:
        return bool(
            volume.device
            and volume.device.startswith("/dev/")
            and not volume.mountpoint
            and not volume.is_system
            and (volume.fs_type or "").lower() in mounting.MOUNTABLE_FS
            and not mounting.is_held(volume.device)
        )

    def _wants_mount(self, disk: DiskInfo) -> bool:
        return (
            self._mount_enabled
            and self.config.index_files
            and not disk.is_system
            and any(self._mountable(v) for v in disk.volumes)
        )

    def _mount_volumes(self, disk: DiskInfo) -> None:
        for volume in disk.volumes:
            if not self._mountable(volume):
                continue
            try:
                where = self.mounter(volume.device)
                log.info("Automatisch eingehängt: %s → %s", volume.device, where or "?")
            except mounting.MountError as exc:
                log.warning("%s: %s", volume.device, exc)

    def _data_changed(self, disk: DiskInfo, usage: UsageWatcher, now: float) -> bool:
        if not self.config.index_files or self.config.change_settle_seconds <= 0:
            return False
        changed = False
        for volume in disk.volumes:
            if not volume.mountpoint or (
                volume.is_system and not self.config.index_system_volumes
            ):
                continue
            used = _volume_used(volume.mountpoint)
            if used is not None and usage.update(f"{disk.key}|{volume.key}", used, now):
                changed = True
        return changed

    def stop(self) -> None:
        self.stop_event.set()
