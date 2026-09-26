"""Start des Tray-Agenten bei der Anmeldung (Linux: XDG-Autostart, Windows: Run-Schlüssel)."""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ENTRY_NAME = "DiskAtlasAgent"
DESKTOP_FILE = "diskatlas-agent.desktop"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> list[str]:
    """Kommando, mit dem das laufende Programm erneut gestartet wird."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "diskatlas.tray.app"]


def autostart_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"


def desktop_icon() -> str:
    """Symbol für den Autostart-Eintrag an einem festen Ort (Datenverzeichnis)."""
    from diskatlas.config import default_data_dir

    source = Path(__file__).resolve().parent.parent / "web" / "static" / "icon_256.png"
    target = default_data_dir() / "diskatlas.png"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    except OSError:
        return "drive-harddisk"  # Standardsymbol des Systems
    return str(target)


def _desktop_entry(command: list[str]) -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=DiskAtlas Agent",
        "Comment=Meldet Festplatten an den DiskAtlas-Server",
        f"Exec={shlex.join(command)}",
        f"Icon={desktop_icon()}",
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
        "",
    ])


def is_enabled() -> bool:
    if sys.platform == "win32":
        return _win_read() is not None
    return (autostart_dir() / DESKTOP_FILE).is_file()


def set_enabled(enabled: bool) -> None:
    if sys.platform == "win32":
        _win_write(subprocess.list2cmdline(launch_command()) if enabled else None)
        return
    path = autostart_dir() / DESKTOP_FILE
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_desktop_entry(launch_command()), encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


def _win_read() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, ENTRY_NAME)[0]
    except OSError:
        return None


def _win_write(command: str | None) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if command is None:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, ENTRY_NAME)
        else:
            winreg.SetValueEx(key, ENTRY_NAME, 0, winreg.REG_SZ, command)
