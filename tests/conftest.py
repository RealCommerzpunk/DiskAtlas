from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from diskatlas.config import Config
from diskatlas.db import Database
from diskatlas.probe import catalog
from diskatlas.probe.types import DiskInfo, SmartInfo, VolumeInfo
from diskatlas.web.app import create_app

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _isolated_drivedb(monkeypatch):
    """Die Laufwerksdatenbank von smartmontools (drivedb.h) liegt nur auf manchen Rechnern. Die
    Tests dürfen nicht davon abhängen (z. B. fehlt sie auf den CI-Rechnern)."""
    fake = tuple(
        (re.compile(pattern, re.IGNORECASE), family)
        for pattern, family in [
            ("ST8000VN004-.*", "Seagate IronWolf"),
            ("WDC WD(20|40)EFRX-.*", "Western Digital Red"),
        ]
    )
    monkeypatch.setattr(catalog, "_drivedb", lambda: fake)


@pytest.fixture
def db(tmp_path) -> Database:
    database = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.upgrade()
    yield database
    database.dispose()


@pytest.fixture
def config() -> Config:
    return Config()


@pytest.fixture
def client(config, db, tmp_path) -> TestClient:
    app = create_app(config, db, bays_path=tmp_path / "bays.json")
    with TestClient(app) as c:
        yield c


def make_disk(
    key: str = "sn:TEST1",
    mountpoint: str | None = "/media/test",
    label: str = "Archiv",
    smart_health: str = "ok",
) -> DiskInfo:
    return DiskInfo(
        key=key,
        device="/dev/sdx",
        serial=key.removeprefix("sn:"),
        model="Test HDD 4TB",
        transport="usb",
        size_bytes=4_000_000_000_000,
        rotational=True,
        removable=True,
        volumes=[
            VolumeInfo(
                key=f"part:{key}-1",
                device="/dev/sdx1",
                fs_type="ext4",
                label=label,
                size_bytes=4_000_000_000_000,
                used_bytes=1_000_000_000_000,
                free_bytes=3_000_000_000_000,
                mountpoint=mountpoint,
            )
        ],
        smart=SmartInfo(
            available=True, health=smart_health, passed=smart_health != "failed",
            temperature_c=35, power_on_hours=1000,
        ),
    )
