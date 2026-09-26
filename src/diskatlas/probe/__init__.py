"""Hardware-Erkennung. `list_disks()` wählt die Implementierung passend zum Betriebssystem."""

from __future__ import annotations

import sys

from diskatlas.probe.types import DiskInfo, FileRecord, SmartInfo, VolumeInfo

__all__ = ["DiskInfo", "FileRecord", "SmartInfo", "VolumeInfo", "list_disks"]


def list_disks() -> list[DiskInfo]:
    if sys.platform == "win32":
        from diskatlas.probe import windows

        return windows.list_disks()
    if sys.platform.startswith("linux"):
        from diskatlas.probe import linux

        return linux.list_disks()
    raise NotImplementedError(f"Plattform {sys.platform} wird (noch) nicht unterstützt")
