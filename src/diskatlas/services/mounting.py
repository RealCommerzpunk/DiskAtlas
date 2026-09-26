"""Ein- und Aushängen von Datenträgern über udisks2 (`udisksctl`, Linux, ohne sudo).

udisks2 arbeitet mit polkit – dieselbe Berechtigung wie beim Anklicken im Dateimanager. Ein kleiner
Sperr-Marker im Datenverzeichnis verhindert, dass der Agent eine Platte automatisch wieder
einhängt, während sie für das Umbenennen absichtlich ausgehängt ist (auch zwischen Prozessen).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from diskatlas.config import default_data_dir

HOLD_MAX_AGE = 180.0  # Sekunden; ein hängengebliebener Marker läuft von selbst ab
MOUNTABLE_FS = {"ntfs", "ntfs3", "exfat", "vfat", "ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs"}


class MountError(Exception):
    """Fehlermeldung, die dem Benutzer direkt angezeigt werden kann."""


def available() -> bool:
    return sys.platform.startswith("linux") and shutil.which("udisksctl") is not None


def _holds_dir() -> Path:
    return default_data_dir() / "holds"


def _hold_path(device: str) -> Path:
    return _holds_dir() / re.sub(r"[^A-Za-z0-9_.-]", "_", device.removeprefix("/dev/"))


def is_held(device: str) -> bool:
    try:
        return time.time() - _hold_path(device).stat().st_mtime < HOLD_MAX_AGE
    except OSError:
        return False


@contextmanager
def hold(device: str):
    """Während des Blocks hängt der Agent dieses Gerät nicht automatisch ein."""
    path = _hold_path(device)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError:
        pass
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["udisksctl", *args, "--no-user-interaction"], capture_output=True, text=True, timeout=120
    )


def mount(device: str) -> str | None:
    """Hängt das Gerät ein und gibt den Einhängepunkt zurück (None, wenn nicht ermittelbar)."""
    if not available():
        raise MountError("udisksctl nicht gefunden – automatisches Einhängen nicht möglich.")
    try:
        proc = _run(["mount", "-b", device])
    except subprocess.TimeoutExpired as exc:
        raise MountError(f"Zeitüberschreitung beim Einhängen von {device}.") from exc
    text = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        already = re.search(r"already mounted at [`'\"]?([^`'\"\n]+)", text)
        if already:
            return already.group(1).rstrip(".")
        raise MountError(_explain(text, "Einhängen"))
    found = re.search(r" at (.+?)\.?$", text)
    return found.group(1) if found else None


def unmount(device: str) -> None:
    if not available():
        raise MountError("udisksctl nicht gefunden – Aushängen nicht möglich.")
    try:
        proc = _run(["unmount", "-b", device])
    except subprocess.TimeoutExpired as exc:
        raise MountError(f"Zeitüberschreitung beim Aushängen von {device}.") from exc
    text = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0 and "not mounted" not in text.lower():
        raise MountError(_explain(text, "Aushängen"))


def _explain(text: str, action: str) -> str:
    low = text.lower()
    if "busy" in low:
        return (
            f"{action} nicht möglich: Das Volume wird noch verwendet "
            "(geöffnete Dateien, Dateimanager, laufender Kopiervorgang?)."
        )
    if "not authorized" in low or "notauthorized" in low:
        return f"{action} nicht möglich: keine Berechtigung (polkit)."
    last = text.splitlines()[-1] if text else "unbekannter Fehler"
    return f"{action} fehlgeschlagen: {last[:300]}"
