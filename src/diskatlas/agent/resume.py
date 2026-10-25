"""Zustand für das Fortsetzen abgebrochener Relay-Übertragungen im Agenten.

Sender: je Datei der Einmal-Schlüssel samt Größe und Änderungszeit der Quelle. Fortgesetzt wird
nur mit demselben Schlüssel *und* unveränderter Quelle – sonst würde AES-GCM ein Stück mit
gleicher Nummer, aber anderem Inhalt verschlüsseln (Nonce-Wiederverwendung).
Empfänger: Pfad der Teildatei, damit Reste abgebrochener Übertragungen aufgeräumt werden.
Einträge älter als `MAX_AGE` werden verworfen (die Übertragung ist dann ohnehin abgelaufen).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
from pathlib import Path

MAX_AGE = 15 * 24 * 3600  # länger als die 14-Tage-Frist des Servers
_ID = re.compile(r"^[0-9a-f]{32}$")


class ResumeStore:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self._lock = threading.Lock()

    def _file(self, kind: str, item_id: str) -> Path:
        if not _ID.match(item_id or ""):
            raise ValueError("ungültige Auftrags-ID")
        return self.dir / f"{item_id}.{kind}.json"

    def save(self, kind: str, item_id: str, data: dict) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self._file(kind, item_id)
            tmp = path.with_name(path.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({**data, "saved": time.time()}, handle)
            os.replace(tmp, path)

    def load(self, kind: str, item_id: str) -> dict | None:
        try:
            data = json.loads(self._file(kind, item_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def drop(self, kind: str, item_id: str) -> None:
        with contextlib.suppress(OSError, ValueError):
            self._file(kind, item_id).unlink()

    def cleanup(self, now: float | None = None) -> int:
        """Alte Einträge löschen; bei Empfänger-Einträgen auch die liegengebliebene Teildatei."""
        now = now or time.time()
        removed = 0
        if not self.dir.is_dir():
            return 0
        for path in self.dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if now - float(data.get("saved", 0)) < MAX_AGE:
                    continue
                part = data.get("part")
                if part and Path(part).name.startswith(".diskatlas-"):
                    with contextlib.suppress(OSError):
                        Path(part).unlink()
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
        return removed
