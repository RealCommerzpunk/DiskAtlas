"""Betreibt den Agenten im Hintergrund und hält den Verbindungsstatus aktuell."""

from __future__ import annotations

import functools
import logging
import os
import threading
import time
from dataclasses import replace
from pathlib import Path

import httpx

from diskatlas.config import default_config_path, load_config
from diskatlas.runtime import make_agent
from diskatlas.tray.status import TrayStatus, strip_credentials

log = logging.getLogger("diskatlas.tray")

# Aufrufe des Agenten, die nichts über die Verbindung aussagen.
_UNTRACKED = {"close"}


def resolve_config_path(path: str | Path | None = None) -> Path:
    return Path(path or os.environ.get("DISKATLAS_CONFIG") or default_config_path())


class StatusTracker:
    """Sammelt den Verbindungsstatus (thread-sicher)."""

    def __init__(self, server_url: str = "", host: str = "", config_path: str = ""):
        self._lock = threading.Lock()
        self._status = TrayStatus(
            server_url=strip_credentials(server_url), host=host, config_path=config_path
        )

    def set(self, state: str, detail: str = "") -> None:
        with self._lock:
            self._status.state, self._status.detail = state, detail

    def ok(self, disks: int | None = None) -> None:
        with self._lock:
            self._status.state, self._status.detail = "online", ""
            self._status.last_ok = time.time()
            if disks is not None:
                self._status.disks = disks

    def failed(self, exc: Exception) -> None:
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if code in (401, 403):
                self.set("auth_error", "Der Server lehnt den API-Token ab.")
            else:
                self.set("offline", f"Der Server antwortet mit Fehler {code}.")
        elif isinstance(exc, httpx.TransportError):
            self.set("offline", f"Server nicht erreichbar ({type(exc).__name__}).")
        else:
            self.set("error", str(exc) or type(exc).__name__)

    def snapshot(self) -> TrayStatus:
        with self._lock:
            return replace(self._status)


class StatusSink:
    """Reicht alle Aufrufe an den echten Sink weiter und merkt sich, ob sie gelingen."""

    def __init__(self, inner, tracker: StatusTracker):
        self._inner = inner
        self._tracker = tracker

    def __getattr__(self, name: str):
        attr = getattr(self._inner, name)
        if not callable(attr) or name in _UNTRACKED:
            return attr

        @functools.wraps(attr)
        def call(*args, **kwargs):
            try:
                result = attr(*args, **kwargs)
            except Exception as exc:
                self._tracker.failed(exc)
                raise
            disks = None
            if name == "report_connected":
                keys = kwargs.get("disk_keys", args[1] if len(args) > 1 else None)
                disks = len(keys) if keys is not None else None
            self._tracker.ok(disks)
            return result

        return call


class AgentController:
    def __init__(self, config_path: str | Path | None = None):
        self.config_path = resolve_config_path(config_path)
        self.tracker = StatusTracker(config_path=str(self.config_path))
        self._agent = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            config = load_config(self.config_path if self.config_path.is_file() else None)
        except (ValueError, OSError) as exc:
            self.tracker = StatusTracker(config_path=str(self.config_path))
            self.tracker.set("config_error", str(exc))
            log.error("Konfiguration nicht lesbar: %s", exc)
            return
        server_url = config.agent.server_url
        if not server_url:
            self.tracker = StatusTracker("", config.agent.host_name, str(self.config_path))
            self.tracker.set("unconfigured", "Server-Adresse in den Einstellungen eintragen.")
            return
        agent = make_agent(config)
        self.tracker = StatusTracker(server_url, agent.host, str(self.config_path))
        agent.sink = StatusSink(agent.sink, self.tracker)
        self._agent = agent
        self._thread = threading.Thread(target=self._run, name="diskatlas-agent", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._agent.watch()
        except Exception as exc:
            log.exception("Agent beendet")
            self.tracker.failed(exc)

    def stop(self) -> None:
        agent, thread = self._agent, self._thread
        self._agent = self._thread = None
        if agent is not None:
            agent.stop()
            if thread is not None:
                thread.join(timeout=10)
            agent.sink.close()
        self.tracker.set("stopped", "Der Agent wurde beendet.")

    def restart(self) -> None:
        log.info("Konfiguration geändert – starte Agent neu")
        self.stop()
        self.start()

    def status(self) -> TrayStatus:
        return self.tracker.snapshot()
