"""Sichere Pfade für Übertragungen.

Der Agent liest und schreibt Dateien auf Anweisung des Servers. Deshalb gilt: Pfade sind immer
relativ zu einem vom Agenten selbst erkannten Einhängepunkt und dürfen ihn nie verlassen – weder
über `..`, absolute Pfade, Laufwerksbuchstaben, NTFS-Streams noch über Symlinks/Junctions.
"""

from __future__ import annotations

import os
import re
import stat

MAX_NAME = 255
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CLOCK$", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
PART_PREFIX = ".diskatlas-"
PART_SUFFIX = ".part"


class UnsafePath(ValueError):
    """Der Pfad ist nicht zulässig (verlässt das Volume oder enthält verbotene Teile)."""


def clean_parts(path: str, *, allow_empty: bool = False) -> list[str]:
    """Zerlegt einen relativen Pfad in geprüfte Teile; sonst UnsafePath."""
    if not isinstance(path, str) or "\x00" in path:
        raise UnsafePath("ungültiger Pfad")
    if path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path):
        raise UnsafePath("absoluter Pfad")
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts and not allow_empty:
        raise UnsafePath("leerer Pfad")
    for part in parts:
        if part == ".." or len(part) > MAX_NAME:
            raise UnsafePath("ungültiger Pfadteil")
        if _BAD_CHARS.search(part) or part.endswith((".", " ")):
            raise UnsafePath("ungültiges Zeichen im Pfad")  # auch NTFS-Streams (`:`)
        if part.split(".")[0].upper() in _RESERVED:
            raise UnsafePath("reservierter Name")
    return parts


def _inside(root: str, candidate: str) -> bool:
    root_real = os.path.realpath(root)
    cand_real = os.path.realpath(candidate)
    try:
        return os.path.commonpath([root_real, cand_real]) == root_real
    except ValueError:  # z. B. verschiedene Laufwerke unter Windows
        return False


def source_path(root: str, rel: str) -> str:
    """Absoluter Pfad einer vorhandenen Quelldatei innerhalb von `root`."""
    parts = clean_parts(rel)
    path = os.path.join(root, *parts)
    if not _inside(root, path):
        raise UnsafePath("Pfad verlässt das Volume (Symlink?)")
    return path


def open_source(root: str, rel: str):
    """Öffnet die Quelle zum Lesen; nur reguläre Dateien, ohne Symlink am Ende, gleiche Platte."""
    path = source_path(root, rel)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)  # blockiert bei FIFOs nicht, sie werden unten abgelehnt
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise UnsafePath(f"Quelle nicht lesbar: {exc.strerror or exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise UnsafePath("Quelle ist keine reguläre Datei")
        if info.st_dev != os.stat(root).st_dev:
            raise UnsafePath("Quelle liegt auf einer anderen Platte")
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "rb"), info


def target_dir(root: str, rel: str) -> str:
    """Zielordner innerhalb von `root`; fehlende Ordner werden angelegt."""
    parts = clean_parts(rel, allow_empty=True)
    path = root
    for part in parts:
        path = os.path.join(path, part)
        if os.path.islink(path):
            raise UnsafePath("Zielordner enthält einen Symlink")
        if not os.path.isdir(path):
            os.mkdir(path)
    if not _inside(root, path):
        raise UnsafePath("Zielordner verlässt das Volume")
    return path


def part_path(directory: str, item_id: str) -> str:
    return os.path.join(directory, f"{PART_PREFIX}{item_id}{PART_SUFFIX}")


def finalize(part: str, directory: str, name: str) -> str:
    """Benennt die fertige Teildatei um, ohne etwas zu überschreiben (Kollision: `name (1).ext`).
    Gibt den endgültigen Namen zurück."""
    parts = clean_parts(name)
    if len(parts) != 1:
        raise UnsafePath("Dateiname darf keinen Pfad enthalten")
    safe = parts[0]
    stem, ext = os.path.splitext(safe)
    for n in range(0, 1000):
        candidate = safe if n == 0 else f"{stem} ({n}){ext}"
        final = os.path.join(directory, candidate)
        try:
            os.link(part, final)  # scheitert, wenn das Ziel existiert – überschreibt nie
        except FileExistsError:
            continue
        except (OSError, NotImplementedError):  # z. B. FAT/exFAT ohne Hardlinks, Windows-Freigaben
            if os.path.lexists(final):
                continue
            try:
                os.rename(part, final)
            except FileExistsError:
                continue
            return candidate
        os.unlink(part)
        return candidate
    raise UnsafePath("Kein freier Dateiname gefunden")
