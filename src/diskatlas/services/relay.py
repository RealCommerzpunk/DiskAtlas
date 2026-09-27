"""Relay: verschlüsselte Stücke laufen für Kopien zwischen Rechnern kurz über den Server.

* Ende-zu-Ende verschlüsselt: Der Server sieht nur Zufallsnamen und Chiffretext, nie Klartext,
  Dateinamen oder Schlüssel (die Verschlüsselung geschieht in den Agenten).
* Strikt seriell: Höchstens `SLOTS` Dateien gleichzeitig, je Datei liegt immer nur ein Stück da, das
  nächste darf erst hoch, wenn der Empfänger das vorige quittiert (und der Server es gelöscht) hat.
  Damit kann der Relay nie volllaufen; `max_bytes` ist eine zusätzliche Obergrenze.
"""

from __future__ import annotations

import contextlib
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from diskatlas.db.models import Client, CopyItem
from diskatlas.services import copies, transfers
from diskatlas.services.ingest import utcnow

CHUNK_PLAIN = 8 * 1024 * 1024  # Klartext je Stück (Agenten)
MAX_CHUNK = CHUNK_PLAIN + 1024  # Chiffretext je Stück (mit Etikett), sonst 413
SLOTS = 1  # gleichzeitig übertragene Dateien im Relay
_NAME = re.compile(r"^[0-9a-f]{32}$")


class RelayError(Exception):
    """Fehler mit HTTP-Status: 404 (nicht dein Auftrag), 409 (warten), 413, 507 (Relay voll)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class RelayStore:
    """Ablage der Stücke unter Zufallsnamen in einem Ordner."""

    def __init__(self, path: Path | str, max_bytes: int):
        self.path = Path(path)
        self.max_bytes = max_bytes  # der Ordner entsteht erst beim ersten Stück

    def _file(self, name: str) -> Path:
        if not _NAME.match(name or ""):
            raise ValueError("ungültiger Relay-Name")
        return self.path / name

    def write(self, data: bytes) -> str:
        self.path.mkdir(parents=True, exist_ok=True)
        name = uuid.uuid4().hex
        tmp = self.path / f"{name}.tmp"
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self._file(name))
        return name

    def read(self, name: str) -> bytes:
        return self._file(name).read_bytes()

    def delete(self, name: str | None) -> None:
        if name:
            with contextlib.suppress(OSError, ValueError):
                self._file(name).unlink()

    def names(self) -> set[str]:
        if not self.path.is_dir():
            return set()
        return {p.name for p in self.path.iterdir() if _NAME.match(p.name)}

    def purge_tmp(self) -> None:
        for p in self.path.glob("*.tmp") if self.path.is_dir() else ():
            with contextlib.suppress(OSError):
                p.unlink()


STORE: RelayStore | None = None


def configure(store: RelayStore | None) -> None:
    global STORE
    STORE = store


def default_dir(config) -> Path:
    if config.server.relay_dir:
        return Path(config.server.relay_dir)
    url = config.effective_database_url
    if url.startswith("sqlite:///") and ":memory:" not in url:
        return Path(url.removeprefix("sqlite:///")).parent / "relay"
    from diskatlas.config import default_data_dir

    return default_data_dir() / "relay"


def _store() -> RelayStore:
    if STORE is None:
        raise RelayError(503, "Relay ist nicht eingerichtet.")
    return STORE


# ------------------------------------------------------------------ Zustand
def reset(item: CopyItem) -> None:
    """Relay-Zustand einer Datei zurücksetzen und das Stück löschen (bei Ende, Fehler, Neustart)."""
    if STORE is not None:
        STORE.delete(item.relay_blob)
    item.relay_blob = None
    item.relay_epk = None
    item.relay_next = 0
    item.relay_pending = False
    item.relay_bytes = 0
    item.relay_final = False


def used_bytes(session: Session) -> int:
    return int(session.scalar(
        select(func.coalesce(func.sum(CopyItem.relay_bytes), 0)).where(CopyItem.relay_pending)
    ) or 0)


def active_uploads(session: Session) -> int:
    return int(session.scalar(
        select(func.count()).select_from(CopyItem)
        .where(CopyItem.state == "running", CopyItem.phase == "upload")
    ) or 0)


def recover(session: Session) -> None:
    """Beim Start: Dateien ohne Datensatz löschen, Datensätze ohne Datei wieder einreihen."""
    store = _store()
    store.purge_tmp()
    known = set()
    for item in session.scalars(select(CopyItem).where(CopyItem.relay_blob.is_not(None))):
        if item.relay_blob in store.names() and item.relay_pending:
            known.add(item.relay_blob)
        else:
            reset(item)
            if item.state == "running":
                copies.set_state(item, "queued", utcnow())
    for name in store.names() - known:
        store.delete(name)


# ------------------------------------------------------------------ Rollen
def _item(session: Session, item_id: str) -> CopyItem:
    item = session.get(CopyItem, item_id)
    if item is None or item.state != "running" or item.phase != "upload":
        raise RelayError(404, "Auftrag nicht gefunden oder nicht mehr aktuell")
    return item


def _sender(session: Session, client: Client, item_id: str) -> CopyItem:
    item = _item(session, item_id)
    if item.claimed_by_client_id != client.id:
        raise RelayError(404, "Auftrag nicht gefunden oder nicht mehr aktuell")
    return item


def _receiver(session: Session, client: Client, item_id: str) -> CopyItem:
    item = _item(session, item_id)
    if item.request.target_client_id != client.id:
        raise RelayError(404, "Auftrag nicht gefunden oder nicht mehr aktuell")
    return item


def _still_ok(session: Session, item: CopyItem, now: datetime) -> None:
    """Zurückgezogen oder Berechtigung entzogen: Übertragung beenden."""
    if item.request.cancelled_at is not None or not transfers.allowed(session, item):
        copies.set_state(item, "cancelled", now, "Abgebrochen (zurückgezogen oder nicht erlaubt).")
        raise RelayError(410, "abgebrochen")


# ------------------------------------------------------------------ Sender
def put_chunk(
    session: Session, client: Client, item_id: str, n: int, data: bytes, epk: str | None,
    final: bool, now: datetime | None = None,
) -> None:
    now = now or utcnow()
    store = _store()
    item = _sender(session, client, item_id)
    _still_ok(session, item, now)
    if not data or len(data) > MAX_CHUNK:
        raise RelayError(413, "Stück zu groß oder leer")
    if n != item.relay_next:
        raise RelayError(409, "falsche Stücknummer")
    if item.relay_pending:
        raise RelayError(409, "warten: das vorige Stück ist noch nicht abgeholt")
    if item.relay_final:
        raise RelayError(409, "die Datei ist schon vollständig hochgeladen")
    if n == 0:
        if not epk or len(epk) > 64:
            raise RelayError(400, "Schlüsselangabe fehlt")
        item.relay_epk = epk
    if used_bytes(session) + len(data) > store.max_bytes:
        raise RelayError(507, "Relay voll – bitte später erneut versuchen")
    item.relay_blob = store.write(data)
    item.relay_bytes = len(data)
    item.relay_pending = True
    item.relay_final = bool(final)
    item.lease_until = now + transfers.LEASE
    item.updated_at = now


def status(session: Session, client: Client, item_id: str, now: datetime | None = None) -> dict:
    """Für den Sender: Stand des Relays dieser Datei."""
    now = now or utcnow()
    item = _sender(session, client, item_id)
    try:
        _still_ok(session, item, now)
    except RelayError:
        return {"abort": True, "next": item.relay_next, "pending": False}
    item.lease_until = now + transfers.LEASE
    return {"abort": False, "next": item.relay_next, "pending": item.relay_pending}


# ------------------------------------------------------------------ Empfänger
def get_chunk(
    session: Session, client: Client, item_id: str, n: int, now: datetime | None = None
) -> tuple[bytes, str, bool] | None:
    """Das anstehende Stück oder None (noch nichts da). Bleibt liegen, bis es quittiert wird."""
    now = now or utcnow()
    item = _receiver(session, client, item_id)
    _still_ok(session, item, now)
    item.lease_until = now + transfers.LEASE
    if n != item.relay_next or not item.relay_pending or not item.relay_blob:
        return None
    try:
        data = _store().read(item.relay_blob)
    except OSError:
        return None
    return data, item.relay_epk or "", item.relay_final


def ack_chunk(
    session: Session, client: Client, item_id: str, n: int, now: datetime | None = None
) -> None:
    now = now or utcnow()
    item = _receiver(session, client, item_id)
    if n != item.relay_next or not item.relay_pending:
        raise RelayError(409, "nichts zu quittieren")
    _store().delete(item.relay_blob)
    item.relay_blob = None
    item.relay_bytes = 0
    item.relay_pending = False
    item.relay_next = n + 1
    item.lease_until = now + transfers.LEASE
    item.updated_at = now




