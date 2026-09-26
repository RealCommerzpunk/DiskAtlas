"""Datenmodell. Änderungen hier immer mit einer Alembic-Migration begleiten."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# SQLite kennt Autoincrement nur für INTEGER PRIMARY KEY.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")

HEALTH_ORDER = {"failed": 0, "warning": 1, "unknown": 2, "ok": 3}


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


disk_labels = Table(
    "disk_labels",
    Base.metadata,
    Column("disk_id", ForeignKey("disks.id", ondelete="CASCADE"), primary_key=True),
    Column("label_id", ForeignKey("labels.id", ondelete="CASCADE"), primary_key=True),
)


class Disk(Base):
    __tablename__ = "disks"

    id: Mapped[int] = mapped_column(primary_key=True)
    disk_key: Mapped[str] = mapped_column(String(200), unique=True)
    serial: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(200))
    vendor: Mapped[str | None] = mapped_column(String(200))
    # Verkaufsbezeichnung, z. B. „IronWolf“ oder „WD Red“
    product_line: Mapped[str | None] = mapped_column(String(200))
    wwn: Mapped[str | None] = mapped_column(String(200))
    transport: Mapped[str | None] = mapped_column(String(50))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    rotational: Mapped[bool | None]
    removable: Mapped[bool] = mapped_column(default=False)
    is_system: Mapped[bool] = mapped_column(default=False)

    # Vom Benutzer gepflegt
    custom_name: Mapped[str | None] = mapped_column(String(200))
    # Lagerort der abgesteckten Platte, z. B. „Karton 3, Regal B“
    location: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)

    # Letzter bekannter SMART-Zustand
    health: Mapped[str] = mapped_column(String(20), default="unknown")
    smart_passed: Mapped[bool | None]
    temperature_c: Mapped[int | None]
    power_on_hours: Mapped[int | None]
    power_cycles: Mapped[int | None]
    reallocated_sectors: Mapped[int | None]
    pending_sectors: Mapped[int | None]
    uncorrectable_sectors: Mapped[int | None]
    percentage_used: Mapped[int | None]
    smart_checked_at: Mapped[datetime | None]
    smart_error: Mapped[str | None] = mapped_column(Text)

    # Besitzer; NULL = herrenlos (nur der Master sieht und verwaltet solche Platten)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    # Verbindungsstatus
    is_connected: Mapped[bool] = mapped_column(default=False)
    last_host: Mapped[str | None] = mapped_column(String(200))
    last_device: Mapped[str | None] = mapped_column(String(200))
    first_seen: Mapped[datetime | None]
    last_seen: Mapped[datetime | None]

    labels: Mapped[list[Label]] = relationship(
        secondary=disk_labels, back_populates="disks", order_by="Label.name"
    )
    volumes: Mapped[list[Volume]] = relationship(
        back_populates="disk",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Volume.id",
    )
    smart_snapshots: Mapped[list[SmartSnapshot]] = relationship(
        back_populates="disk",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SmartSnapshot.taken_at.desc()",
        lazy="noload",
    )

    @property
    def present_volumes(self) -> list[Volume]:
        return [v for v in self.volumes if v.present]

    @property
    def brand(self) -> str | None:
        """„Seagate IronWolf“, „Western Digital WD Red“ … (soweit bekannt)."""
        parts = [self.vendor, self.product_line]
        if self.product_line and self.vendor and self.product_line.startswith(("WD ", self.vendor)):
            parts = [self.product_line]  # „WD Red“ statt „Western Digital WD Red“
        return " ".join(p for p in parts if p) or None

    @property
    def fs_types(self) -> list[str]:
        """Dateisysteme der vorhandenen Volumes (klein geschrieben, ohne Duplikate)."""
        return sorted({v.fs_type.lower() for v in self.present_volumes if v.fs_type})

    @property
    def has_unmounted_fs(self) -> bool:
        """Angeschlossen, Dateisystem erkannt, aber nicht eingehängt (Belegung nicht messbar)."""
        return self.is_connected and any(
            v.fs_type and not v.mountpoint for v in self.present_volumes
        )

    @property
    def usage_known(self) -> bool:
        return self.used_bytes is not None and self.free_bytes is not None

    @property
    def display_name(self) -> str:
        if self.custom_name:
            return self.custom_name
        names = [v.label for v in self.present_volumes if v.label]
        if names:
            return ", ".join(dict.fromkeys(names))
        return self.model or self.serial or self.disk_key

    @property
    def used_bytes(self) -> int | None:
        values = [v.used_bytes for v in self.present_volumes if v.used_bytes is not None]
        return sum(values) if values else None

    @property
    def free_bytes(self) -> int | None:
        values = [v.free_bytes for v in self.present_volumes if v.free_bytes is not None]
        return sum(values) if values else None

    @property
    def usage_percent(self) -> float | None:
        used, free = self.used_bytes, self.free_bytes
        if used is None or free is None or used + free == 0:
            return None
        return used * 100.0 / (used + free)

    @property
    def file_count(self) -> int:
        return sum(v.file_count or 0 for v in self.volumes)

    @property
    def health_rank(self) -> int:
        return HEALTH_ORDER.get(self.health, 2)

    @property
    def media_type(self) -> str | None:
        if self.transport == "nvme":
            return "NVMe"
        if self.rotational is True:
            return "HDD"
        if self.rotational is False:
            return "SSD"
        return None


class Volume(Base):
    __tablename__ = "volumes"
    __table_args__ = (UniqueConstraint("disk_id", "volume_key", name="uq_volume_disk_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    disk_id: Mapped[int] = mapped_column(ForeignKey("disks.id", ondelete="CASCADE"), index=True)
    volume_key: Mapped[str] = mapped_column(String(300))
    device: Mapped[str | None] = mapped_column(String(200))
    fs_type: Mapped[str | None] = mapped_column(String(50))
    label: Mapped[str | None] = mapped_column(String(200))
    fs_uuid: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    used_bytes: Mapped[int | None] = mapped_column(BigInteger)
    free_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mountpoint: Mapped[str | None] = mapped_column(String(1000))
    is_system: Mapped[bool] = mapped_column(default=False)
    # False, wenn die Partition beim letzten Scan der Festplatte nicht mehr gemeldet wurde.
    present: Mapped[bool] = mapped_column(default=True)
    last_seen: Mapped[datetime | None]

    active_scan_id: Mapped[str | None] = mapped_column(String(36))
    index_status: Mapped[str] = mapped_column(String(20), default="never")
    indexed_at: Mapped[datetime | None]
    index_errors: Mapped[int] = mapped_column(default=0)
    file_count: Mapped[int] = mapped_column(default=0)
    file_bytes: Mapped[int] = mapped_column(BigInteger, default=0)

    disk: Mapped[Disk] = relationship(back_populates="volumes")

    @property
    def usage_percent(self) -> float | None:
        if self.used_bytes is None or self.free_bytes is None:
            return None
        total = self.used_bytes + self.free_bytes
        return self.used_bytes * 100.0 / total if total else None


class FileEntry(Base):
    __tablename__ = "files"
    __table_args__ = (
        Index("ix_files_volume_scan", "volume_id", "scan_id"),
        Index("ix_files_name", "name"),
        Index("ix_files_extension", "extension"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    volume_id: Mapped[int] = mapped_column(ForeignKey("volumes.id", ondelete="CASCADE"))
    scan_id: Mapped[str] = mapped_column(String(36))
    path: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(1024))
    extension: Mapped[str | None] = mapped_column(String(50))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mtime: Mapped[datetime | None]

    volume: Mapped[Volume] = relationship()


class SmartSnapshot(Base):
    __tablename__ = "smart_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    disk_id: Mapped[int] = mapped_column(ForeignKey("disks.id", ondelete="CASCADE"), index=True)
    taken_at: Mapped[datetime]
    health: Mapped[str] = mapped_column(String(20))
    passed: Mapped[bool | None]
    temperature_c: Mapped[int | None]
    power_on_hours: Mapped[int | None]
    reallocated_sectors: Mapped[int | None]
    pending_sectors: Mapped[int | None]
    uncorrectable_sectors: Mapped[int | None]
    percentage_used: Mapped[int | None]
    raw_json: Mapped[str | None] = mapped_column(Text)

    disk: Mapped[Disk] = relationship(back_populates="smart_snapshots")


class Label(Base):
    """Label eines Benutzers (`owner_user_id` NULL: ohne Anmeldung oder aus früherer Zeit)."""

    __tablename__ = "labels"
    __table_args__ = (UniqueConstraint("owner_user_id", "name", name="uq_labels_owner_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(100))
    color: Mapped[str] = mapped_column(String(20), default="#4f7cff")

    disks: Mapped[list[Disk]] = relationship(secondary=disk_labels, back_populates="labels")


class HostState(Base):
    """Zuletzt gemeldeter Zustand eines Agenten-Rechners (Heartbeat): welche Platten stecken an
    welchem SATA-Port. Grundlage der Schachtansicht und des Assistenten."""

    __tablename__ = "host_states"

    host: Mapped[str] = mapped_column(String(200), primary_key=True)
    # Der erste Client, der einen Rechnernamen meldet, besitzt ihn (Namen sind frei wählbar).
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime]
    # JSON: {"present": [...], "ports": [...]}
    data: Mapped[str] = mapped_column(Text, default="{}")


class Setting(Base):
    """Einfache Schlüssel/Wert-Einstellungen des Servers (z. B. Schachtzuordnung)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Command(Base):
    """Auftrag vom Server an einen Agenten (z. B. Bezeichnung ändern, neu scannen)."""

    __tablename__ = "commands"
    __table_args__ = (Index("ix_commands_host_status", "host", "status"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    host: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(50))
    payload: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    # pending | running | done | failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    result: Mapped[str | None] = mapped_column(Text)
    disk_key: Mapped[str | None] = mapped_column(String(200))
    # Benutzer, dessen Client den Auftrag ausführen darf (Besitzer der Platte)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class User(Base):
    """Benutzerkonto. `pending` = Antrag wartet auf Freischaltung durch den Master."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    nickname: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_master: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | active
    note: Mapped[str | None] = mapped_column(Text)  # Nachricht beim Antrag an den Master
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    clients: Mapped[list[Client]] = relationship(
        back_populates="user", cascade="all, delete-orphan", order_by="Client.id"
    )


class Client(Base):
    """Eine Agent-Installation eines Benutzers; weist sich mit einem eigenen Token aus."""

    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("user_id", "nickname"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    nickname: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256, nie der Klartext
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    last_seen: Mapped[datetime | None]

    user: Mapped[User] = relationship(back_populates="clients")


class DiskShare(Base):
    """Lesefreigabe: `viewer_user_id` darf die Platte sehen (nie ändern)."""

    __tablename__ = "disk_shares"

    disk_id: Mapped[int] = mapped_column(
        ForeignKey("disks.id", ondelete="CASCADE"), primary_key=True
    )
    viewer_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class DiskTransferRequest(Base):
    """Antrag auf eine schon vergebene Platte; nur der bisherige Besitzer entscheidet."""

    __tablename__ = "disk_transfer_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    disk_id: Mapped[int] = mapped_column(
        ForeignKey("disks.id", ondelete="CASCADE"), index=True
    )
    from_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    requested_by_client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    resolved_at: Mapped[datetime | None]

    disk: Mapped[Disk] = relationship()
    from_user: Mapped[User | None] = relationship(foreign_keys=[from_user_id])
    to_user: Mapped[User] = relationship(foreign_keys=[to_user_id])
