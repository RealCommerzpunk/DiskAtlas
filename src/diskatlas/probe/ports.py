"""SATA-Ports (ata1, ata2, …) der angeschlossenen Platten unter Linux.

Der Port ist an den physischen Anschluss gebunden und ändert sich – anders als Gerätename
(`/dev/sdb`) oder Einhängepunkt – nicht, wenn eine Platte gewechselt wird. Damit lässt sich
ein Hot-Swap-Schacht eindeutig erkennen.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_ATA = re.compile(r"/(ata\d+)/")


@dataclass(frozen=True)
class SataPort:
    port: str  # "ata3"
    device: str  # "/dev/sdb"
    serial: str | None
    model: str | None = None


def all_ports(sys_root: str = "/sys") -> list[str]:
    """Alle SATA-Ports des Systems (auch unbelegte), z. B. ["ata1", …, "ata6"]."""
    base = Path(sys_root) / "class" / "ata_port"
    try:
        return sorted((p.name for p in base.iterdir() if p.name.startswith("ata")),
                      key=lambda n: int(n[3:]))
    except OSError:
        return []


def sata_ports(sys_root: str = "/sys") -> dict[str, SataPort]:
    """Belegte Ports: Port -> Platte. Leer unter Windows/macOS oder ohne SATA."""
    if not sys.platform.startswith("linux"):
        return {}
    block = Path(sys_root) / "block"
    try:
        entries = sorted(block.iterdir())
    except OSError:
        return {}
    found: dict[str, str] = {}  # Port -> Name (sdb)
    for entry in entries:
        if not entry.name.startswith("sd"):
            continue
        match = _ATA.search(str(entry.resolve()) + "/")
        if match:
            found[match.group(1)] = entry.name
    if not found:
        return {}
    info = _lsblk_info(list(found.values()))
    return {
        port: SataPort(port, f"/dev/{name}", *info.get(name, (None, None)))
        for port, name in found.items()
    }


def _lsblk_info(names: list[str]) -> dict[str, tuple[str | None, str | None]]:
    try:
        proc = subprocess.run(
            ["lsblk", "-dn", "-P", "-o", "NAME,SERIAL,MODEL", *[f"/dev/{n}" for n in names]],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    result = {}
    for line in proc.stdout.splitlines():
        fields = dict(re.findall(r'(\w+)="([^"]*)"', line))
        if "NAME" in fields:
            result[fields["NAME"]] = (fields.get("SERIAL") or None, fields.get("MODEL") or None)
    return result


def port_map(sys_root: str = "/sys") -> dict[str, str]:
    """Gerätename -> SATA-Port (z. B. {"sdb": "ata3"}); nur sysfs, kein Prozessaufruf."""
    if not sys.platform.startswith("linux"):
        return {}
    try:
        entries = sorted((Path(sys_root) / "block").iterdir())
    except OSError:
        return {}
    result = {}
    for entry in entries:
        match = _ATA.search(str(entry.resolve()) + "/") if entry.name.startswith("sd") else None
        if match:
            result[entry.name] = match.group(1)
    return result


def ports_info(disks: list, sys_root: str = "/sys") -> dict | None:
    """Heartbeat-Angaben für den Server: belegte Ports und alle Ports; None ohne SATA (Windows)."""
    all_p = all_ports(sys_root)
    if not all_p:
        return None
    return {
        "all_ports": all_p,
        "present": [
            {"port": d.port, "device": d.device, "serial": d.serial, "model": d.model,
             "disk_key": d.key}
            for d in disks
            if getattr(d, "port", None)
        ],
    }
