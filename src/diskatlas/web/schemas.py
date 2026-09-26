"""Pydantic-Schemas der REST-API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from diskatlas.probe.types import DiskInfo


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LabelOut(ORMModel):
    id: int
    name: str
    category: str | None
    color: str


class LabelIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: str | None = Field(default=None, max_length=100)
    color: str = Field(default="#4f7cff", pattern=r"^#[0-9a-fA-F]{6}$")


class LabelPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    category: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class VolumeOut(ORMModel):
    id: int
    volume_key: str
    device: str | None
    fs_type: str | None
    label: str | None
    size_bytes: int | None
    used_bytes: int | None
    free_bytes: int | None
    mountpoint: str | None
    is_system: bool
    present: bool
    last_seen: datetime | None
    index_status: str
    indexed_at: datetime | None
    file_count: int
    file_bytes: int


class DiskOut(ORMModel):
    id: int
    disk_key: str
    display_name: str
    custom_name: str | None
    notes: str | None
    serial: str | None
    model: str | None
    vendor: str | None
    product_line: str | None
    location: str | None
    transport: str | None
    media_type: str | None
    size_bytes: int | None
    used_bytes: int | None
    free_bytes: int | None
    usage_percent: float | None
    file_count: int
    health: str
    smart_passed: bool | None
    temperature_c: int | None
    power_on_hours: int | None
    smart_checked_at: datetime | None
    is_connected: bool
    is_system: bool
    last_host: str | None
    last_client_label: str | None  # „Benutzer / Client“, an dem die Platte zuletzt hing
    first_seen: datetime | None
    last_seen: datetime | None
    labels: list[LabelOut]


class DiskDetailOut(DiskOut):
    power_cycles: int | None
    reallocated_sectors: int | None
    pending_sectors: int | None
    uncorrectable_sectors: int | None
    percentage_used: int | None
    smart_error: str | None
    last_device: str | None
    volumes: list[VolumeOut]


class DiskPatch(BaseModel):
    custom_name: str | None = Field(default=None, max_length=200)
    notes: str | None = None
    location: str | None = Field(default=None, max_length=500)


class LabelAssignment(BaseModel):
    label_ids: list[int]


class FileOut(BaseModel):
    id: int
    path: str
    name: str
    extension: str | None
    size: int
    mtime: datetime | None
    disk_id: int
    disk_name: str
    disk_connected: bool
    volume_label: str | None
    mountpoint: str | None


class FileSearchResult(BaseModel):
    total: int
    items: list[FileOut]


class StatsOut(BaseModel):
    disk_count: int
    connected: int
    capacity: int
    used: int
    free: int
    unknown: int = 0
    files: int
    health: dict[str, int]


# ------------------------------------------------------------------ Ingest (Agent -> Server)
class IngestDisk(BaseModel):
    host: str
    disk: DiskInfo


class IngestConnected(BaseModel):
    host: str
    disk_keys: list[str]
    ports_info: dict | None = None  # {"present": [...], "all_ports": [...]} (Linux/SATA)


class CommandResult(BaseModel):
    ok: bool
    message: str = ""


class IngestVolumeRef(BaseModel):
    disk_key: str
    volume_key: str


class IngestFiles(IngestVolumeRef):
    scan_id: str
    files: list[tuple[str, int, float | None]]


class IngestFinish(IngestVolumeRef):
    scan_id: str
    errors: int = 0
    success: bool = True


class DuplicateLocation(BaseModel):
    disk_id: int
    disk_name: str
    disk_connected: bool
    volume_label: str | None
    path: str


class FileDuplicateGroup(BaseModel):
    name: str
    size: int
    wasted_bytes: int
    locations: list[DuplicateLocation]


class FileDuplicateResult(BaseModel):
    total_groups: int
    wasted_bytes: int
    items: list[FileDuplicateGroup]


class FolderDuplicateGroup(BaseModel):
    file_count: int
    size: int
    wasted_bytes: int
    locations: list[DuplicateLocation]


class FolderDuplicateResult(BaseModel):
    total_groups: int
    wasted_bytes: int
    items: list[FolderDuplicateGroup]


class RunningIndexOut(BaseModel):
    disk_id: int
    disk_name: str
    volume_label: str | None
    mountpoint: str | None
    files_so_far: int


class UserOut(BaseModel):
    """Was jeder angemeldete Benutzer über die anderen sieht: nur Namen (zum Freigeben)."""

    id: int
    nickname: str
    is_master: bool
    clients: list[str]


class LookupOut(BaseModel):
    id: int
    name: str
    brand: str | None
    model: str | None
    serial: str | None
    size_bytes: int | None
    health: str
    is_connected: bool
    state: str  # bay | connected | offline
    host: str | None
    bay: int | None
    location: str | None
