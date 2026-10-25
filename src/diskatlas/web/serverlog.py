"""Letzte Fehler des Servers für den Admin (ohne `docker logs`).

Ein Logging-Handler merkt sich im Speicher die letzten Warnungen und Fehler samt Ursache. Nach
einem Neustart ist die Liste leer; das vollständige Protokoll bleibt `docker logs`.
"""

from __future__ import annotations

import logging
import threading
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime

MAX_ENTRIES = 50
MAX_TEXT = 2000


@dataclass
class Entry:
    at: datetime
    level: str
    logger: str
    message: str
    detail: str  # letzte Zeilen der Fehlerursache


class MemoryHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.entries: deque[Entry] = deque(maxlen=MAX_ENTRIES)
        self._lock_entries = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            detail = ""
            if record.exc_info and record.exc_info[1] is not None:
                lines = traceback.format_exception(*record.exc_info)
                detail = "".join(lines[-6:])[-MAX_TEXT:]
            entry = Entry(datetime.now(), record.levelname, record.name,
                          record.getMessage()[:MAX_TEXT], detail)
            with self._lock_entries:
                self.entries.appendleft(entry)
        except Exception:  # Protokollieren darf nie selbst scheitern
            self.handleError(record)

    def recent(self) -> list[Entry]:
        with self._lock_entries:
            return list(self.entries)


HANDLER = MemoryHandler()


def install() -> None:
    """Einmalig an den Wurzel-Logger hängen (auch uvicorn meldet Fehler dorthin)."""
    root = logging.getLogger()
    if HANDLER not in root.handlers:
        root.addHandler(HANDLER)
    for name in ("uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        if not logger.propagate and HANDLER not in logger.handlers:
            logger.addHandler(HANDLER)
