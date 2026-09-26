"""Kopierberechtigung je Freigabe (nie / nachfragen / immer)."""

import pytest
from sqlalchemy import select
from test_authz import World

from diskatlas.db.models import DiskShare


@pytest.fixture
def world(db, tmp_path) -> World:
    return World(db, tmp_path)


def _mode(world, disk_id, user):
    with world.db.session() as s:
        return s.scalar(select(DiskShare.copy_mode).where(
            DiskShare.disk_id == disk_id, DiskShare.viewer_user_id == world.ids[user]))


def _share(world, **extra):
    world.ingest_disk("anna", "sn:ANNA1", "pc-anna")
    disk_id = world.disk_id("sn:ANNA1")
    r = world.web["anna"].post(f"/disks/{disk_id}/shares",
                               data={"user_id": world.ids["bob"], **extra})
    assert r.status_code == 303
    return disk_id


def test_share_defaults_to_never(world):
    assert _mode(world, _share(world), "bob") == "never"


def test_share_can_start_with_a_mode_and_owner_can_change_it(world):
    disk_id = _share(world, copy_mode="ask")
    assert _mode(world, disk_id, "bob") == "ask"
    anna = world.web["anna"]
    assert anna.post(f"/disks/{disk_id}/shares/{world.ids['bob']}/copy-mode",
                     data={"copy_mode": "always"}).status_code == 303
    assert _mode(world, disk_id, "bob") == "always"
    assert "always" in anna.get(f"/disks/{disk_id}").text


def test_invalid_mode_is_refused_and_changes_nothing(world):
    disk_id = _share(world, copy_mode="ask")
    anna = world.web["anna"]
    r = anna.post(f"/disks/{disk_id}/shares/{world.ids['bob']}/copy-mode",
                  data={"copy_mode": "root"})
    assert "share_err" in r.headers["location"]
    assert _mode(world, disk_id, "bob") == "ask"
    r = anna.post(f"/disks/{disk_id}/shares", data={"user_id": world.ids["master"],
                                                     "copy_mode": "evil"})
    assert "share_err" in r.headers["location"]


def test_only_the_owner_changes_the_copy_mode(world):
    disk_id = _share(world)
    bob = world.web["bob"]  # Freigegebener: sieht die Platte, ändert aber nichts
    url = f"/disks/{disk_id}/shares/{world.ids['bob']}/copy-mode"
    assert bob.post(url, data={"copy_mode": "always"}).status_code == 403
    assert _mode(world, disk_id, "bob") == "never"
    via_token = world.agent("bob").post(url, data={"copy_mode": "always"})
    assert via_token.status_code in (401, 403), "Agenten-Token ist keine Sitzung"
    assert _mode(world, disk_id, "bob") == "never"
    # ein Fremder ohne Freigabe sieht die Platte nicht einmal
    world.ingest_disk("bob", "sn:BOB1", "pc-bob")
    other = world.web["master"]
    assert other.post(url, data={"copy_mode": "ask"}).status_code == 303, "Admin darf"
    assert _mode(world, disk_id, "bob") == "ask"
