"""Ändert die Dateisystem-Bezeichnung (Label) eines Volumes – die, die auch der Dateimanager zeigt.

Linux: über udisks2 (D-Bus, `gdbus`). Die Berechtigung regelt polkit wie beim Dateimanager,
es ist keine sudo-Regel nötig. Windows: `Set-Volume` (benötigt Administratorrechte).
Das ist ein schreibender Zugriff auf den Datenträger und läuft daher nur lokal, nie über den
Ingest-Weg.
"""

from __future__ import annotations

import re
import subprocess
import sys

from diskatlas.services import mounting

# Diese Dateisysteme lassen sich nur ausgehängt umbenennen (bei den anderen erst direkt versuchen).
_UNMOUNT_FIRST = {"ntfs", "ntfs3", "exfat"}

# Dateisystem -> (maximale Länge, Einheit, nur Großbuchstaben)
_LIMITS: dict[str, tuple[int, str, bool]] = {
    "ext2": (16, "bytes", False),
    "ext3": (16, "bytes", False),
    "ext4": (16, "bytes", False),
    "vfat": (11, "chars", True),
    "fat": (11, "chars", True),
    "exfat": (15, "chars", False),
    "ntfs": (128, "chars", False),
    "ntfs3": (128, "chars", False),
    "btrfs": (255, "bytes", False),
    "xfs": (12, "bytes", False),
}
_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f]")


class LabelError(Exception):
    """Fehlermeldung, die dem Benutzer direkt angezeigt werden kann."""

    def __init__(self, message: str, needs_unmount: bool = False):
        super().__init__(message)
        self.needs_unmount = needs_unmount


def normalize_label(fs_type: str | None, label: str) -> str:
    """Prüft die Bezeichnung gegen die Regeln des Dateisystems; gibt sie ggf. angepasst zurück."""
    label = label.strip()
    if _FORBIDDEN.search(label):
        raise LabelError("Die Bezeichnung enthält unzulässige Steuerzeichen.")
    fs = (fs_type or "").lower()
    if fs not in _LIMITS:
        raise LabelError(
            f"Für das Dateisystem „{fs_type or 'unbekannt'}“ wird das Umbenennen nicht unterstützt."
        )
    limit, unit, upper = _LIMITS[fs]
    if upper:
        label = label.upper()
        if re.search(r'[*?.,;:/\\|+=<>\[\]"]', label):
            raise LabelError(
                "FAT-Bezeichnungen dürfen keine Sonderzeichen wie * ? . , ; : / \\ enthalten."
            )
    length = len(label.encode("utf-8")) if unit == "bytes" else len(label)
    if length > limit:
        raise LabelError(
            f"Die Bezeichnung ist zu lang: {fs} erlaubt höchstens {limit} "
            f"{'Bytes' if unit == 'bytes' else 'Zeichen'} (aktuell {length})."
        )
    return label


def _gvariant_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def set_label(device: str | None, fs_type: str | None, mountpoint: str | None, label: str) -> str:
    """Setzt die Bezeichnung und gibt die tatsächlich geschriebene zurück."""
    label = normalize_label(fs_type, label)
    if sys.platform == "win32":
        _set_label_windows(mountpoint, label)
    else:
        _set_label_linux(device, fs_type, mountpoint, label)
    return label


def _set_label_linux(
    device: str | None, fs_type: str | None, mountpoint: str | None, label: str
) -> None:
    if not device or not device.startswith("/dev/"):
        raise LabelError("Das Gerät des Volumes ist unbekannt.")
    if not re.fullmatch(r"[A-Za-z0-9_]+", device.removeprefix("/dev/")):
        raise LabelError(f"Unerwarteter Gerätename: {device}")
    if not mountpoint:
        with mounting.hold(device):
            _udisks_set_label(device, label)
        return
    with mounting.hold(device):  # der Agent hängt es währenddessen nicht wieder ein
        if (fs_type or "").lower() not in _UNMOUNT_FIRST:
            try:
                _udisks_set_label(device, label)
                return
            except LabelError as exc:
                if not exc.needs_unmount:
                    raise
        _rename_unmounted(device, label)


def _rename_unmounted(device: str, label: str) -> None:
    """Aushängen → umbenennen → wieder einhängen (auch wenn das Umbenennen scheitert)."""
    try:
        mounting.unmount(device)
    except mounting.MountError as exc:
        raise LabelError(str(exc)) from exc
    failure: LabelError | None = None
    try:
        _udisks_set_label(device, label)
    except LabelError as exc:
        failure = exc
    try:
        mounting.mount(device)
    except mounting.MountError as exc:
        if failure is None:
            raise LabelError(
                f"Bezeichnung geändert, aber das Wiedereinhängen ist fehlgeschlagen: {exc}"
            ) from exc
    if failure is not None:
        raise failure


def _udisks_set_label(device: str, label: str) -> None:
    name = device.removeprefix("/dev/")
    cmd = [
        "gdbus", "call", "--system", "--dest", "org.freedesktop.UDisks2",
        "--object-path", f"/org/freedesktop/UDisks2/block_devices/{name}",
        "--method", "org.freedesktop.UDisks2.Filesystem.SetLabel",
        _gvariant_string(label), "@a{sv} {}",
    ]
    try:
        proc = _run(cmd)
    except FileNotFoundError as exc:
        raise LabelError("`gdbus`/udisks2 nicht gefunden – Umbenennen nicht möglich.") from exc
    except subprocess.TimeoutExpired as exc:
        raise LabelError("Zeitüberschreitung beim Umbenennen.") from exc
    if proc.returncode != 0:
        raise LabelError(_explain(proc.stderr or proc.stdout), needs_unmount=_mounted_hint(proc))


def _mounted_hint(proc: subprocess.CompletedProcess) -> bool:
    low = (proc.stderr or proc.stdout or "").lower()
    return "mounted" in low or "busy" in low


def _explain(message: str) -> str:
    text = message.strip().splitlines()[-1] if message.strip() else "unbekannter Fehler"
    low = message.lower()
    if "not authorized" in low or "notauthorized" in low:
        return "Keine Berechtigung (polkit hat das Umbenennen abgelehnt)."
    if "mounted" in low or "busy" in low:
        return "Das Dateisystem ist eingehängt und lässt sich in diesem Zustand nicht umbenennen."
    if "no such interface" in low or "unknownobject" in low.replace(" ", ""):
        return "udisks2 kennt dieses Gerät nicht als Dateisystem."
    return f"Umbenennen fehlgeschlagen: {text[:300]}"


def _set_label_windows(mountpoint: str | None, label: str) -> None:
    match = re.match(r"^([A-Za-z]):", mountpoint or "")
    if not match:
        raise LabelError("Ohne Laufwerksbuchstaben kann das Volume nicht umbenannt werden.")
    script = (
        f"Set-Volume -DriveLetter {match.group(1).upper()} "
        f"-NewFileSystemLabel '{label.replace(chr(39), chr(39) * 2)}'"
    )
    try:
        proc = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise LabelError("PowerShell konnte nicht ausgeführt werden.") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise LabelError(
            "Umbenennen fehlgeschlagen (Administratorrechte nötig?): "
            + (detail[0][:200] if detail else "unbekannter Fehler")
        )
