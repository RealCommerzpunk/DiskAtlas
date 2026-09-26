"""Festplattenerkennung unter Linux über lsblk."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator

from diskatlas.probe.ports import port_map
from diskatlas.probe.types import DiskInfo, VolumeInfo, clean, make_disk_key, partition_key

LSBLK_COLUMNS = (
    "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,VENDOR,WWN,TRAN,RM,HOTPLUG,ROTA,"
    "MOUNTPOINTS,FSTYPE,LABEL,UUID,PARTUUID,FSSIZE,FSUSED,FSAVAIL"
)
SYSTEM_MOUNTPOINTS = {"/", "/boot", "/boot/efi", "/efi", "/usr", "/var"}
SKIP_FSTYPES = {"LVM2_member", "crypto_LUKS", "linux_raid_member", "swap", "zfs_member"}
SKIP_NAME_PREFIXES = ("loop", "ram", "zram")
GENERIC_VENDORS = {"ATA", "USB", "SCSI"}


def list_disks() -> list[DiskInfo]:
    proc = subprocess.run(
        ["lsblk", "--json", "--bytes", "--output", LSBLK_COLUMNS],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    disks = parse_lsblk(json.loads(proc.stdout))
    ports = port_map()
    for disk in disks:
        disk.port = ports.get(disk.device.removeprefix("/dev/"))
    return disks


def parse_lsblk(data: dict) -> list[DiskInfo]:
    disks = []
    for node in data.get("blockdevices", []):
        if node.get("type") != "disk" or str(node.get("name", "")).startswith(SKIP_NAME_PREFIXES):
            continue
        disks.append(_disk_from_node(node))
    return disks


def _disk_from_node(node: dict) -> DiskInfo:
    volumes = [_volume_from_node(n) for n in _volume_nodes(node)]
    serial = clean(node.get("serial"))
    wwn = clean(node.get("wwn"))
    model = clean(node.get("model"))
    size = _int(node.get("size"))
    path = node.get("path") or f"/dev/{node['name']}"
    return DiskInfo(
        key=make_disk_key(serial, wwn, [model, size, *[v.key for v in volumes]]),
        device=path,
        smart_device=path,
        serial=serial,
        model=model,
        vendor=_vendor(node.get("vendor")),
        wwn=wwn,
        transport=clean(node.get("tran")),
        size_bytes=size,
        rotational=_bool(node.get("rota")),
        removable=bool(_bool(node.get("rm")) or _bool(node.get("hotplug"))),
        is_system=any(v.is_system for v in volumes),
        volumes=volumes,
    )


def _volume_nodes(node: dict) -> Iterator[dict]:
    """Alle Knoten (inkl. der Disk selbst) mit einem nutzbaren Dateisystem, in Baumreihenfolge."""
    queue = [node]
    while queue:
        current = queue.pop(0)
        fstype = current.get("fstype")
        if fstype and fstype not in SKIP_FSTYPES:
            yield current
        queue.extend(current.get("children") or [])


def _volume_from_node(node: dict) -> VolumeInfo:
    mountpoint = _first_mountpoint(node)
    used = _int(node.get("fsused"))
    free = _int(node.get("fsavail"))
    size = _int(node.get("fssize")) or _int(node.get("size"))
    if mountpoint and (used is None or free is None):
        try:
            usage = shutil.disk_usage(mountpoint)
            used, free = usage.used, usage.free
            size = size or usage.total
        except OSError:
            pass
    fs_uuid = clean(node.get("uuid"))
    key = partition_key(node.get("partuuid"))
    if not key:
        key = f"fs:{fs_uuid}" if fs_uuid else f"dev:{node.get('name')}"
    return VolumeInfo(
        key=key,
        device=node.get("path") or f"/dev/{node.get('name')}",
        fs_type=clean(node.get("fstype")),
        label=clean(node.get("label")),
        fs_uuid=fs_uuid,
        size_bytes=size,
        used_bytes=used,
        free_bytes=free,
        mountpoint=mountpoint,
        is_system=mountpoint in SYSTEM_MOUNTPOINTS,
    )


def _vendor(value: object) -> str | None:
    vendor = clean(value)
    return None if vendor is None or vendor.upper() in GENERIC_VENDORS else vendor


def _first_mountpoint(node: dict) -> str | None:
    points = node.get("mountpoints")
    if points is None and node.get("mountpoint"):
        points = [node["mountpoint"]]
    for point in points or []:
        if point and point.startswith("/"):
            return point
    return None


def _int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip() in ("1", "true")
