"""Text und Datum aus hochgeladenen PDFs lesen (für die Zuordnung von Löschprotokollen).

Das PDF stammt vom Benutzer und ist damit nicht vertrauenswürdig: gelesen werden nur die ersten
Seiten, der Text ist begrenzt, jeder Fehler führt zu „nichts erkannt“ statt zum Abbruch.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import UTC, datetime

log = logging.getLogger(__name__)
logging.getLogger("pypdf").setLevel(logging.ERROR)  # Warnungen zu kaputten PDFs nicht ins Log

MAX_PAGES = 5
MAX_CHARS = 200_000
MIN_SERIAL = 6  # kürzere Seriennummern wären als Teilstring zu unsicher
_NON_ALNUM = re.compile(r"[^0-9A-Z]")


def normalize(text: str) -> str:
    """Nur Buchstaben und Ziffern, groß: „WD-WCC4E 123“ und „wdwcc4e123“ sind gleich."""
    return _NON_ALNUM.sub("", (text or "").upper())


def read(data: bytes) -> tuple[str, datetime | None]:
    """(Text der ersten Seiten, Erstellungsdatum aus den Metadaten) – bei Fehlern ("", None)."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        parts, size = [], 0
        for page in reader.pages[:MAX_PAGES]:
            text = page.extract_text() or ""
            parts.append(text)
            size += len(text)
            if size >= MAX_CHARS:
                break
        created = None
        try:
            created = reader.metadata.creation_date if reader.metadata else None
        except Exception:  # kaputte Metadaten: Datum einfach weglassen
            created = None
        if created is not None and created.tzinfo is not None:
            created = created.astimezone(UTC).replace(tzinfo=None)
        return "\n".join(parts)[:MAX_CHARS], created
    except Exception:
        log.info("PDF ließ sich nicht lesen", exc_info=True)
        return "", None


def mentions(text: str, serial: str | None) -> bool:
    """Kommt die Seriennummer im Text vor (ohne Leer- und Sonderzeichen verglichen)?"""
    wanted = normalize(serial or "")
    return len(wanted) >= MIN_SERIAL and wanted in normalize(text)
