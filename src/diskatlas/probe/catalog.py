"""Hersteller und Verkaufsbezeichnung (z. B. „IronWolf“, „WD Red“) zu einer Modellnummer.

Quellen, in dieser Reihenfolge:
1. `model_family` aus der SMART-Ausgabe von smartctl (kommt aus der Laufwerksdatenbank
   `drivedb.h` von smartmontools, die von der Community gepflegt wird),
2. dieselbe `drivedb.h`, direkt auf dem Rechner nachgeschlagen (für Platten ohne SMART-Daten),
3. für den Hersteller zusätzlich eine kleine Präfix-Tabelle der Modellnummern.

Aktualisieren der Herstellerliste: `sudo update-smart-drivedb` (smartmontools).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DRIVEDB_PATHS = (
    Path("/var/lib/smartmontools/drivedb/drivedb.h"),
    Path("/usr/share/smartmontools/drivedb.h"),
    Path("/usr/local/share/smartmontools/drivedb.h"),
    Path(r"C:\Program Files\smartmontools\bin\drivedb.h"),
)

# (Regex auf die Modellnummer, Hersteller); die erste passende Zeile gewinnt.
VENDOR_PREFIXES: tuple[tuple[str, str], ...] = (
    (r"^(SEAGATE\b|ST\d|STM\d|STZ)", "Seagate"),
    (r"^(WDC\b|WD\d|WD-|WDS\d|WDBNCE)", "Western Digital"),
    (r"^(HGST\b|HUS|HUH|HUC|HDN|HDS\d|HTS|HTE|HMS)", "HGST"),
    (r"^(TOSHIBA\b|MG\d\d|DT\d\d|HDW|HDE|MQ0|MK\d)", "Toshiba"),
    (r"^(SAMSUNG\b|MZ|SSD 8\d\d|HD\d{3}[A-Z]{2}\b)", "Samsung"),
    (r"^(KINGSTON\b|SA400|SUV\d|SKC|SNV|SMS\d|SHFS|SV\d)", "Kingston"),
    (r"^(CRUCIAL\b|CT\d|MTFD|Micron\b)", "Crucial"),
    (r"^(INTEL\b|SSDSC|SSDPE)", "Intel"),
    (r"^(SANDISK\b|SD[A-Z]{2}\d)", "SanDisk"),
    (r"^(HITACHI\b|HDT|HDP|HUA|HCS)", "Hitachi"),
    (r"^(MAXTOR\b|\d?[A-Z]\d{3}[A-Z]\d)", "Maxtor"),
    (r"^(FUJITSU\b|MHZ|MHY)", "Fujitsu"),
    (r"^(ADATA\b|ASU|SU\d{3})", "ADATA"),
    (r"^(CORSAIR\b|Force )", "Corsair"),
    (r"^(OCZ\b)", "OCZ"),
    (r"^(PNY\b)", "PNY"),
    (r"^(LITEON\b|LITE-ON\b)", "Lite-On"),
    (r"^(TRANSCEND\b|TS\d+G)", "Transcend"),
    (r"^(PLEXTOR\b|PX-)", "Plextor"),
    (r"^(APPLE\b)", "Apple"),
)
_KNOWN_VENDORS = (
    "Seagate", "Western Digital", "WD", "HGST", "Toshiba", "Samsung", "Kingston", "Crucial",
    "Micron", "Intel", "SanDisk", "Hitachi", "Maxtor", "Fujitsu", "ADATA", "Corsair", "OCZ",
    "PNY", "Lite-On", "Transcend", "Plextor",
)
_TECH_TAG = re.compile(r"\s*\((?:AF|CMR)\)")


@dataclass(frozen=True)
class Identity:
    vendor: str | None = None
    product_line: str | None = None


def vendor_from_model(model: str | None) -> str | None:
    text = (model or "").strip()
    for pattern, vendor in VENDOR_PREFIXES:
        if re.match(pattern, text, re.IGNORECASE):
            return vendor
    return None


def _vendor_of_family(family: str) -> tuple[str, str] | None:
    """(kanonischer Hersteller, Rest der Familie), wenn die Familie mit einem Hersteller beginnt."""
    for name in _KNOWN_VENDORS:
        if re.match(rf"{re.escape(name)}\b", family, re.IGNORECASE):
            rest = family[len(name):].strip(" -")
            canonical = "Western Digital" if name == "WD" else name
            return canonical, rest
    return None


def product_line_from_family(family: str | None) -> tuple[str | None, str | None]:
    """Zerlegt eine smartctl-Familie in (Hersteller, Verkaufsbezeichnung).

    Familien, die nicht mit einem Hersteller beginnen (z. B. „SandForce Driven SSDs“ – ein
    Controller), sind keine Verkaufsbezeichnung und ergeben (None, None).
    """
    text = _TECH_TAG.sub("", (family or "").strip())
    found = _vendor_of_family(text) if text else None
    if not found:
        return None, None
    vendor, rest = found
    rest = rest.replace("...", "").strip()
    if not rest or rest.lower().startswith(("based", "driven")):  # Controller-Familie, kein Produkt
        return vendor, None
    return vendor, f"WD {rest}" if vendor == "Western Digital" else rest


@lru_cache(maxsize=1)
def _drivedb() -> tuple[tuple[re.Pattern[str], str], ...]:
    for path in DRIVEDB_PATHS:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        return _parse_drivedb(text)
    return ()


def _strip_comments(text: str) -> str:
    """Entfernt C-Kommentare (// und /* */), lässt Zeichenketten unangetastet."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _parse_drivedb(text: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    literal = r'"(?:[^"\\]|\\.)*"'
    entry = re.compile(rf"\{{\s*({literal})\s*,\s*((?:{literal}\s*)+),")
    unquote = re.compile(r'"((?:[^"\\]|\\.)*)"')
    result = []
    for family_lit, model_lits in entry.findall(_strip_comments(text)):
        family = unquote.match(family_lit).group(1)
        model_regex = "".join(unquote.findall(model_lits)).replace("\\\\", "\\")
        if family.startswith(("VERSION", "DEFAULT", "USB:")) or not model_regex:
            continue
        try:
            result.append((re.compile(model_regex, re.IGNORECASE), family))
        except re.error:
            continue
    return tuple(result)


def drivedb_family(model: str | None) -> str | None:
    text = (model or "").strip()
    if not text:
        return None
    for pattern, family in _drivedb():
        if pattern.fullmatch(text):
            return family
    return None


def identify(model: str | None, family: str | None = None) -> Identity:
    """Hersteller und Verkaufsbezeichnung; `family` = smartctl-`model_family`, falls bekannt."""
    vendor, line = product_line_from_family(family or drivedb_family(model))
    return Identity(vendor=vendor or vendor_from_model(model), product_line=line)
