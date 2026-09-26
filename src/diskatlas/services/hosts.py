"""Zustand der Agenten-Rechner: wer meldet sich, welche Platten stecken an welchem SATA-Port."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from diskatlas.db.models import HostState
from diskatlas.services.ingest import utcnow

FRESH_SECONDS = 180  # so lange gilt ein Agent als online (Heartbeat alle 60 s)


@dataclass
class PortInfo:
    port: str
    device: str | None = None
    serial: str | None = None
    model: str | None = None
    disk_key: str | None = None


@dataclass
class HostSnapshot:
    host: str
    updated_at: datetime
    present: list[PortInfo] = field(default_factory=list)  # belegte Ports
    all_ports: list[str] = field(default_factory=list)  # alle SATA-Ports des Rechners

    @property
    def is_online(self) -> bool:
        return utcnow() - self.updated_at < timedelta(seconds=FRESH_SECONDS)

    def by_port(self) -> dict[str, PortInfo]:
        return {p.port: p for p in self.present}


def record(
    session: Session, host: str, ports_info: dict | None, now: datetime | None = None
) -> None:
    """Speichert den Heartbeat. Ohne Portangaben (z. B. Windows) bleibt nur der Zeitstempel."""
    now = now or utcnow()
    state = session.get(HostState, host)
    if state is None:
        state = HostState(host=host, updated_at=now, data="{}")
        session.add(state)
    state.updated_at = now
    if ports_info is not None:
        state.data = json.dumps(
            {"present": ports_info.get("present", []), "ports": ports_info.get("all_ports", [])}
        )


def get(session: Session, host: str | None) -> HostSnapshot | None:
    state = session.get(HostState, host) if host else None
    if state is None:
        return None
    try:
        data = json.loads(state.data or "{}")
    except ValueError:
        data = {}
    present = [
        PortInfo(**{k: p.get(k) for k in ("port", "device", "serial", "model", "disk_key")})
        for p in data.get("present", [])
        if isinstance(p, dict) and p.get("port")
    ]
    return HostSnapshot(state.host, state.updated_at, present, list(data.get("ports", [])))


def known_hosts(session: Session) -> list[HostSnapshot]:
    return [
        snap
        for (host,) in session.query(HostState.host).order_by(HostState.host)
        if (snap := get(session, host))
    ]
