"""Aufräumen im Hintergrund: alte Index-Zeilen löschen und Ordnerindex vorbereiten.

SQLite erlaubt nur einen Schreiber zur Zeit. Große Löschungen (z. B. 850 000 Zeilen des
vorherigen Index) würden die Datenbank minutenlang sperren – Agenten bekämen dann „database is
locked“. Deshalb geschieht das hier in kleinen Häppchen mit Pausen, unabhängig von Anfragen.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy import select

from diskatlas.db import Database
from diskatlas.db.models import Volume
from diskatlas.services import browse, ingest

log = logging.getLogger(__name__)

BATCH = 5_000  # Zeilen je Transaktion
PAUSE = 0.25  # Sekunden zwischen zwei Transaktionen (andere Schreiber kommen dazwischen)
MAX_PER_ROUND = 200_000  # danach erst die nächste Runde (andere Volumes kommen auch dran)
INTERVAL = 20.0


def run_once(db: Database, pause: float = PAUSE) -> int:
    """Eine Runde: Reste löschen, fehlende Ordnerindizes bauen; gibt gelöschte Zeilen zurück."""
    removed = 0
    with db.session() as session:
        ids = list(session.scalars(select(Volume.id).where(Volume.purge_pending.is_(True))))
    for volume_id in ids:
        with db.session() as session:
            volume = session.get(Volume, volume_id)
            if volume is None:
                continue
            count, done = ingest.purge_step(session, volume, BATCH, MAX_PER_ROUND, pause)
            removed += count
            if done:
                volume.purge_pending = False
        if count:
            log.info("Index-Reste aufgeräumt: %d Zeilen (Volume %d)", count, volume_id)
    with db.session() as session:  # Ordnerindex vorbereiten, damit der Browser sofort antwortet
        pending = list(session.scalars(select(Volume.id).where(
            Volume.active_scan_id.is_not(None), Volume.index_status == "done",
            (Volume.dirs_scan_id.is_(None)) | (Volume.dirs_scan_id != Volume.active_scan_id))))
    for volume_id in pending:
        with db.session() as session:
            volume = session.get(Volume, volume_id)
            if volume is not None:
                browse.ensure_directories(session, volume)
    return removed


class Worker:
    def __init__(self, db: Database, interval: float = INTERVAL):
        self.db = db
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="diskatlas-maintenance",
                                       daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=10)

    def _loop(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                run_once(self.db)
            except Exception:  # z. B. gerade gesperrt: nächste Runde
                log.warning("Aufräumen im Hintergrund unterbrochen", exc_info=True)
