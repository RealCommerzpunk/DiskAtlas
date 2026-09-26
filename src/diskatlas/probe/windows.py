"""Festplattenerkennung unter Windows über PowerShell (Storage-Modul)."""

from __future__ import annotations

import json
import os
import subprocess

from diskatlas.probe.types import DiskInfo, VolumeInfo, clean, make_disk_key, partition_key

POWERSHELL_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$physical = @{}
foreach ($p in Get-PhysicalDisk) { $physical["$($p.DeviceId)"] = $p }
$result = foreach ($d in Get-Disk) {
  $pd = $physical["$($d.Number)"]
  $parts = @(foreach ($p in (Get-Partition -DiskNumber $d.Number)) {
    $v = $p | Get-Volume
    $letter = [string]$p.DriveLetter
    if ($letter -notmatch '^[A-Za-z]$') { $letter = $null }
    [pscustomobject]@{
      PartitionNumber = $p.PartitionNumber
      DriveLetter     = $letter
      AccessPaths     = @($p.AccessPaths)
      Size            = $p.Size
      Guid            = $p.Guid
      Type            = [string]$p.Type
      IsSystem        = [bool]$p.IsSystem
      IsBoot          = [bool]$p.IsBoot
      FileSystem      = $v.FileSystem
      Label           = $v.FileSystemLabel
      VolumeSize      = $v.Size
      SizeRemaining   = $v.SizeRemaining
      VolumeId        = $v.UniqueId
    }
  })
  [pscustomobject]@{
    Number       = $d.Number
    Model        = $d.Model
    FriendlyName = $d.FriendlyName
    Manufacturer = $d.Manufacturer
    SerialNumber = $d.SerialNumber
    Size         = $d.Size
    BusType      = [string]$d.BusType
    MediaType    = [string]$pd.MediaType
    IsSystem     = [bool]$d.IsSystem
    IsBoot       = [bool]$d.IsBoot
    Partitions   = $parts
  }
}
ConvertTo-Json -InputObject @($result) -Depth 6 -Compress
"""

TRANSPORT_MAP = {
    "usb": "usb",
    "sata": "sata",
    "ata": "ata",
    "nvme": "nvme",
    "sas": "sas",
    "scsi": "scsi",
    "raid": "raid",
    "sd": "sd",
    "mmc": "mmc",
}


def list_disks() -> list[DiskInfo]:
    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            POWERSHELL_SCRIPT,
        ],
        capture_output=True,
        check=True,
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    text = proc.stdout.decode("utf-8-sig", errors="replace").strip()
    return parse_windows_disks(json.loads(text) if text else [])


def parse_windows_disks(data: list | dict, system_drive: str | None = None) -> list[DiskInfo]:
    if isinstance(data, dict):
        data = [data]
    system_drive = (system_drive or os.environ.get("SYSTEMDRIVE", "C:"))[:1].upper()
    return [_disk(d, system_drive) for d in data]


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _disk(d: dict, system_drive: str) -> DiskInfo:
    number = d.get("Number")
    volumes = []
    for p in _as_list(d.get("Partitions")):
        vol = _volume(p, number, system_drive)
        if vol is not None:
            volumes.append(vol)
    serial = clean(d.get("SerialNumber"))
    model = clean(d.get("Model")) or clean(d.get("FriendlyName"))
    size = d.get("Size")
    media = (clean(d.get("MediaType")) or "").upper()
    bus = (clean(d.get("BusType")) or "").lower()
    return DiskInfo(
        key=make_disk_key(serial, None, [model, size, *[v.key for v in volumes]]),
        device=rf"\\.\PhysicalDrive{number}",
        smart_device=f"/dev/pd{number}",
        serial=serial,
        model=model,
        vendor=clean(d.get("Manufacturer")),
        transport=TRANSPORT_MAP.get(bus, bus or None),
        size_bytes=size,
        rotational=True if media == "HDD" else False if media == "SSD" else None,
        removable=bus == "usb",
        is_system=bool(d.get("IsSystem") or d.get("IsBoot")),
        volumes=volumes,
    )


def _volume(p: dict, disk_number, system_drive: str) -> VolumeInfo | None:
    fs = clean(p.get("FileSystem"))
    guid = clean(p.get("Guid"))
    if clean(p.get("Type")) == "Reserved" or (not fs and not guid):
        return None  # MSR u. ä.: unter Linux ohnehin unsichtbar
    letter = clean(p.get("DriveLetter"))
    letter = letter.upper() if letter and len(letter) == 1 and letter.isalpha() else None
    mountpoint = f"{letter}:\\" if letter else None
    if mountpoint is None:
        for path in _as_list(p.get("AccessPaths")):
            if path and not str(path).startswith("\\\\?\\"):
                mountpoint = str(path)
                break
    volume_id = clean(p.get("VolumeId"))
    key = partition_key(guid)
    if not key:
        key = f"winvol:{volume_id}" if volume_id else f"pn:{disk_number}-{p.get('PartitionNumber')}"
    size = p.get("VolumeSize") or p.get("Size")
    free = p.get("SizeRemaining") if fs else None
    used = size - free if (size is not None and free is not None) else None
    return VolumeInfo(
        key=key,
        device=f"Disk {disk_number} Partition {p.get('PartitionNumber')}",
        fs_type=fs.lower() if fs else None,
        label=clean(p.get("Label")),
        fs_uuid=volume_id,
        size_bytes=size,
        used_bytes=used,
        free_bytes=free,
        mountpoint=mountpoint,
        is_system=bool(p.get("IsSystem") or p.get("IsBoot")) or letter == system_drive,
    )
