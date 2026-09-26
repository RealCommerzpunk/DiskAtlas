"""Wer darf welche Platten sehen und ändern?

- Herr über eine Platte ist ihr Besitzer (`Disk.owner_user_id`) und der Master.
- Lesen darf zusätzlich, wem der Besitzer die Platte freigegeben hat (`DiskShare`).
- Herrenlose Platten (kein Besitzer) sehen nur der Master.
- Ohne Anmeldung (lokaler Betrieb) gibt es keine Beschränkung.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, or_, select

from diskatlas.db.models import Disk, DiskShare, Label


@dataclass(frozen=True)
class Viewer:
    """Wer gerade fragt. `user_id` None = ohne Anmeldung."""

    user_id: int | None = None
    is_master: bool = False

    @property
    def unrestricted(self) -> bool:
        return self.user_id is None or self.is_master

    @property
    def scope_id(self) -> int | None:
        """Benutzer-ID für Abfragen, die nach Besitzer einschränken (None = alles)."""
        return None if self.unrestricted else self.user_id


OPEN = Viewer()


def visible_ids(viewer: Viewer) -> Select | None:
    """Unterabfrage mit den sichtbaren Platten-IDs; None = alle."""
    if viewer.unrestricted:
        return None
    shared = select(DiskShare.disk_id).where(DiskShare.viewer_user_id == viewer.user_id)
    return select(Disk.id).where(or_(Disk.owner_user_id == viewer.user_id, Disk.id.in_(shared)))


def can_write(viewer: Viewer, disk: Disk) -> bool:
    return viewer.unrestricted or disk.owner_user_id == viewer.user_id


def can_write_label(viewer: Viewer, label: Label) -> bool:
    return viewer.unrestricted or label.owner_user_id == viewer.user_id


def label_fits_disk(label: Label, disk: Disk) -> bool:
    """Ein Label wird nur Platten seines Besitzers angeheftet (fremde Labels bleiben privat)."""
    return label.owner_user_id is None or disk.owner_user_id is None or (
        label.owner_user_id == disk.owner_user_id
    )
