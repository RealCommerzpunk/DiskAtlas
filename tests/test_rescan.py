"""Knopf „Jetzt neu scannen“ und Agenten-Aufträge im Client-Betrieb (Roadmap M3)."""

import pytest
from sqlalchemy import select
from test_authz import World

from diskatlas.db.models import Command, Volume


@pytest.fixture
def world(db, tmp_path) -> World:
    w = World(db, tmp_path)
    w.ingest_disk("anna", "sn:A1", "pc-anna")
    return w


def heartbeat(world, who="anna", host="pc-anna", keys=("sn:A1",)):
    r = world.agent(who).post("/api/v1/ingest/connected",
                              json={"host": host, "disk_keys": list(keys)})
    assert r.status_code == 200


def commands(world):
    with world.db.session() as s:
        return [(c.kind, c.host, c.status) for c in s.scalars(select(Command))]


def test_rescan_reaches_the_agent_of_the_disk(world):
    heartbeat(world)
    disk_id = world.disk_id("sn:A1")
    page = world.web["anna"].get(f"/disks/{disk_id}").text
    assert "Jetzt neu scannen" in page and "meldet sich gerade nicht" not in page
    r = world.web["anna"].post(f"/disks/{disk_id}/rescan")
    assert r.status_code == 303 and "gesendet" in r.headers["location"]
    again = world.web["anna"].post(f"/disks/{disk_id}/rescan")
    assert "bereits" in again.headers["location"]
    assert commands(world) == [("rescan", "pc-anna", "pending")]
    got = world.agent("anna").get("/api/v1/ingest/commands", params={"host": "pc-anna"}).json()
    assert [c["kind"] for c in got] == ["rescan"]
    assert world.agent("bob").get("/api/v1/ingest/commands",
                                  params={"host": "pc-anna"}).json() == []


def test_relabel_works_in_client_mode(world):
    """Regression: Seit 0.6.0 liegt der Agenten-Zustand je Client; die Plattenseite fragte ihn
    unter dem Rechnernamen ab und sperrte Umbenennen und Neu-Scan dauerhaft."""
    heartbeat(world)
    with world.db.session() as s:
        volume_id = s.scalar(select(Volume.id))
    r = world.web["anna"].post(f"/volumes/{volume_id}/label", data={"label": "Neu"})
    assert "gesendet" in r.headers["location"]
    assert commands(world) == [("rename_label", "pc-anna", "pending")]


def test_rescan_refused_when_offline_or_foreign(world):
    disk_id = world.disk_id("sn:A1")
    r = world.web["anna"].post(f"/disks/{disk_id}/rescan")
    assert "meldet+sich+gerade+nicht" in r.headers["location"], "noch kein Heartbeat"
    heartbeat(world, keys=())  # Platte abgezogen
    r = world.web["anna"].post(f"/disks/{disk_id}/rescan")
    assert "nicht+angeschlossen" in r.headers["location"]
    heartbeat(world)
    assert world.web["bob"].post(f"/disks/{disk_id}/rescan").status_code == 404
    world.web["anna"].post(f"/disks/{disk_id}/shares", data={"user_id": world.ids["bob"]})
    assert world.web["bob"].post(f"/disks/{disk_id}/rescan").status_code == 403
    assert "Jetzt neu scannen" not in world.web["bob"].get(f"/disks/{disk_id}").text
    assert commands(world) == []


def test_another_users_agent_with_the_same_host_name_does_not_count(world):
    world.ingest_disk("bob", "sn:B1", "pc-anna")  # Bob nennt seinen Rechner genauso
    heartbeat(world, who="bob", host="pc-anna", keys=("sn:B1",))
    r = world.web["anna"].post(f"/disks/{world.disk_id('sn:A1')}/rescan")
    assert "meldet+sich+gerade+nicht" in r.headers["location"]
