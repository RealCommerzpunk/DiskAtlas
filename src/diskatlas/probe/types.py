"""Plattformunabhängige Beschreibung erkannter Hardware (auch das Übertragungsformat zum Server)."""

from __future__ import annotations

import hashlib
import re
from typing import NamedTuple

from pydantic import BaseModel, Field


class SmartInfo(BaseModel):
    available: bool = False
    health: str = "unknown"  # ok | warning | failed | unknown
    passed: bool | None = None
    temperature_c: int | None = None
    power_on_hours: int | None = None
    power_cycles: int | None = None
    reallocated_sectors: int | None = None
    pending_sectors: int | None = None
    uncorrectable_sectors: int | None = None
    percentage_used: int | None = None
    serial: str | None = None
    model: str | None = None
    error: str | None = None
    raw: dict | None = None


class VolumeInfo(BaseModel):
    key: str
    device: str | None = None
    fs_type: str | None = None
    label: str | None = None
    fs_uuid: str | None = None
    size_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    mountpoint: str | None = None
    is_system: bool = False


class DiskInfo(BaseModel):
    key: str
    device: str
    smart_device: str | None = None
    serial: str | None = None
    model: str | None = None
    vendor: str | None = None
    wwn: str | None = None
    transport: str | None = None
    size_bytes: int | None = None
    rotational: bool | None = None
    removable: bool = False
    is_system: bool = False
    volumes: list[VolumeInfo] = Field(default_factory=list)
    smart: SmartInfo | None = None

    def signature(self) -> tuple:
        """Ändert sich, sobald Partitionen oder Einhängepunkte wechseln."""
        return (self.key, tuple(sorted((v.key, v.mountpoint or "") for v in self.volumes)))


class FileRecord(NamedTuple):
    path: str  # relativ zum Einhängepunkt, Trenner "/"
    size: int
    mtime: float | None


def clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_serial(serial: str | None) -> str | None:
    serial = clean(serial)
    if not serial:
        return None
    serial = re.sub(r"\s+", "", serial).upper().rstrip(".")
    return serial or None


def make_disk_key(serial: str | None, wwn: str | None, fallback: list[object]) -> str:
    """Stabile, plattformübergreifende Identität einer Festplatte."""
    serial = normalize_serial(serial)
    if serial:
        return f"sn:{serial}"
    wwn = clean(wwn)
    if wwn:
        return f"wwn:{wwn.lower()}"
    digest = hashlib.sha1("|".join(str(p) for p in fallback).encode()).hexdigest()[:16]
    return f"fp:{digest}"


def partition_key(guid: str | None) -> str | None:
    """GPT-Partitions-GUID ist unter Linux und Windows identisch."""
    guid = clean(guid)
    if not guid:
        return None
    return "part:" + guid.strip("{}").lower()
