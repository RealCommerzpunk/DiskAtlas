"""„Datei anfordern“: Berechtigung, Zustandsmaschine, Web-Ablauf."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from test_authz import World

from diskatlas.db.models import CopyItem, CopyRequest, Disk, DiskShare
from diskatlas.services import copies
from diskatlas.services.ingest import utcnow

FILES = [
    ["film/a.mkv", 1000, 1_700_000_000.0],
    ["film/b.mkv", 2000, 1_700_000_000.0],
    ["film/sub/c.mkv", 300, 1_700_000_000.0],
    ["doc.txt", 5, 1_700_000_000.0],
]


@pytest.fixture
def world(db, tmp_path) -> World:
    w = World(db, tmp_path)
    w.app.state.config.server.copy_enabled = True
    return w


def connect(world, who, host, *keys):
    r = world.agent(who).post("/api/v1/ingest/connected", json={"host": host, "disk_keys": keys})
    assert r.status_code == 200, r.text


def volume_id(world, key):
    with world.db.session() as s:
        return s.scalar(select(Disk).where(Disk.disk_key == key)).volumes[0].id


def setup_anna(world):
    """Annas Quellplatte A1 (mit Dateien) und Zielplatte A2, beide an ihrem Rechner."""
    world.ingest_disk("anna", "sn:A1", "pc-anna", label="Quelle")
    world.index("anna", "sn:A1", FILES)
    world.ingest_disk("anna", "sn:A2", "pc-anna", label="Ziel")
    connect(world, "anna", "pc-anna", "sn:A1", "sn:A2")
    return volume_id(world, "sn:A1"), volume_id(world, "sn:A2")


def setup_bob_target(world):
    world.ingest_disk("bob", "sn:B1", "pc-bob", label="BobsZiel")
    connect(world, "bob", "pc-bob", "sn:B1")
    return volume_id(world, "sn:B1")


def target_value(world, who, vol):
    with world.db.session() as s:
        client_id = s.scalar(select(Disk.last_client_id).join(Disk.volumes.property.mapper.class_)
                             .where(Disk.volumes.any(id=vol)))
    return f"{client_id}:{vol}"


def request_files(world, who, sel, target_vol, path="Anforderungen", **extra):
    return world.web[who].post("/requests", data={
        "sel": sel, "target": target_value(world, who, target_vol), "path": path, **extra})


def items(world):
    with world.db.session() as s:
        return {(i.source_path): (i.state, i.phase) for i in s.scalars(select(CopyItem))}


def set_online(world, key, online):
    with world.db.session() as s:
        s.scalar(select(Disk).where(Disk.disk_key == key)).is_connected = online
        s.commit()


def share(world, key, user, mode):
    disk_id = world.disk_id(key)
    world.web["anna"].post(f"/disks/{disk_id}/shares", data={"user_id": world.ids[user],
                                                              "copy_mode": mode})


# ------------------------------------------------------------------ eigene Dateien
def test_own_file_between_own_disks_is_queued_as_local_copy(world):
    src, dst = setup_anna(world)
    r = request_files(world, "anna", [f"f:{src}:doc.txt"], dst)
    assert r.status_code == 303 and r.headers["location"].startswith("/requests/")
    assert items(world) == {"doc.txt": ("queued", "local")}


def test_folder_is_expanded_including_subfolders(world):
    src, dst = setup_anna(world)
    request_files(world, "anna", [f"d:{src}:film"], dst)
    assert sorted(items(world)) == ["film/a.mkv", "film/b.mkv", "film/sub/c.mkv"]
    request_files(world, "anna", [f"d:{src}:"], dst, path="Ganz")
    with world.db.session() as s:
        assert s.query(CopyItem).count() == 3 + 4, "zweites Ziel, alle vier Dateien"


def test_offline_source_waits_and_continues_when_connected(world):
    src, dst = setup_anna(world)
    set_online(world, "sn:A1", False)
    request_files(world, "anna", [f"f:{src}:doc.txt"], dst)
    assert items(world)["doc.txt"][0] == "waiting_disk"
    connect(world, "anna", "pc-anna", "sn:A1", "sn:A2")  # der Heartbeat stößt die Neubewertung an
    assert items(world)["doc.txt"][0] == "queued"


def test_target_going_offline_moves_item_back_to_waiting(world):
    src, dst = setup_anna(world)
    request_files(world, "anna", [f"f:{src}:doc.txt"], dst)
    set_online(world, "sn:A2", False)
    with world.db.session() as s:
        copies.sweep(s)
        s.commit()
    assert items(world)["doc.txt"][0] == "waiting_disk"


def test_duplicate_request_is_refused_while_open(world):
    src, dst = setup_anna(world)
    request_files(world, "anna", [f"f:{src}:doc.txt"], dst)
    again = request_files(world, "anna", [f"f:{src}:doc.txt"], dst)
    assert again.status_code == 200 and "schon angefordert" in again.text
    with world.db.session() as s:
        assert s.query(CopyItem).count() == 1 and s.query(CopyRequest).count() == 1


def test_too_many_files_are_refused(world, monkeypatch):
    src, dst = setup_anna(world)
    monkeypatch.setattr(copies, "MAX_FILES", 2)
    r = request_files(world, "anna", [f"d:{src}:film"], dst)
    assert r.status_code == 400 and "Zu viele Dateien" in r.text
    assert items(world) == {}


def test_target_must_be_own_connected_disk(world):
    src, dst = setup_anna(world)
    bob_vol = setup_bob_target(world)
    # Bob nimmt Annas Zielplatte (Client von Anna) als Ziel
    with world.db.session() as s:
        anna_client = s.scalar(select(Disk.last_client_id).where(Disk.disk_key == "sn:A2"))
    share(world, "sn:A1", "bob", "always")
    r = world.web["bob"].post("/requests", data={
        "sel": [f"f:{src}:doc.txt"], "target": f"{anna_client}:{dst}", "path": "x"})
    assert r.status_code == 200 and "nicht (mehr) verfügbar" in r.text
    assert items(world) == {}
    assert bob_vol


def test_target_path_traversal_is_refused(world):
    src, dst = setup_anna(world)
    for bad in ("../x", "a/../../b", "a\0b"):
        r = request_files(world, "anna", [f"f:{src}:doc.txt"], dst, path=bad)
        assert r.status_code == 200 and "ungültig" in r.text, bad
    assert items(world) == {}


def test_target_must_be_connected_and_not_a_system_disk(world):
    src, dst = setup_anna(world)
    set_online(world, "sn:A2", False)
    assert "nicht (mehr) verfügbar" in request_files(world, "anna", [f"f:{src}:doc.txt"], dst).text
    set_online(world, "sn:A2", True)
    world.web["anna"].post(f"/disks/{world.disk_id('sn:A2')}/labels", data={"new_name": "System"})
    assert "nicht (mehr) verfügbar" in request_files(world, "anna", [f"f:{src}:doc.txt"], dst).text


# ------------------------------------------------------------------ fremde Platten
def test_foreign_disk_without_share_is_invisible_to_requests(world):
    src, _ = setup_anna(world)
    bob_vol = setup_bob_target(world)
    r = request_files(world, "bob", [f"f:{src}:doc.txt"], bob_vol)
    assert r.status_code == 400 and "Auswahl nicht gefunden" in r.text
    assert items(world) == {}


@pytest.mark.parametrize(("mode", "expected"), [
    ("never", "denied"), ("ask", "waiting_approval"), ("always", "queued"),
])
def test_share_copy_mode_decides_initial_state(world, mode, expected):
    src, _ = setup_anna(world)
    bob_vol = setup_bob_target(world)
    share(world, "sn:A1", "bob", mode)
    request_files(world, "bob", [f"f:{src}:doc.txt"], bob_vol)
    state, phase = items(world)["doc.txt"]
    assert state == expected
    if mode == "always":
        assert phase == "upload", "verschiedene Clients: über den Server"


def test_owner_approves_or_denies_and_nobody_else_can(world):
    src, _ = setup_anna(world)
    bob_vol = setup_bob_target(world)
    share(world, "sn:A1", "bob", "ask")
    request_files(world, "bob", [f"f:{src}:doc.txt", f"f:{src}:film/a.mkv"], bob_vol)
    with world.db.session() as s:
        ids = {i.source_path: i.id for i in s.scalars(select(CopyItem))}
    assert "Zustimmung nötig (2)" in world.web["anna"].get("/requests").text
    assert "doc.txt" not in world.web["bob"].get("/requests").text.split("Deine Anfragen")[0]
    for who in ("bob", "master"):  # weder der Anfordernde noch der Admin darf zustimmen
        world.web[who].post("/requests-decide", data={"item": list(ids.values()),
                                                       "action": "approve"})
    assert {v[0] for v in items(world).values()} == {"waiting_approval"}
    world.web["anna"].post("/requests-decide", data={"item": [ids["doc.txt"]],
                                                      "action": "approve"})
    world.web["anna"].post("/requests-decide", data={"item": [ids["film/a.mkv"]],
                                                      "action": "deny"})
    assert items(world) == {"doc.txt": ("queued", "upload"), "film/a.mkv": ("denied", "local")}


def test_revoking_the_share_stops_queued_items(world):
    src, _ = setup_anna(world)
    bob_vol = setup_bob_target(world)
    share(world, "sn:A1", "bob", "always")
    request_files(world, "bob", [f"f:{src}:doc.txt"], bob_vol)
    assert items(world)["doc.txt"][0] == "queued"
    world.web["anna"].post(f"/disks/{world.disk_id('sn:A1')}/shares/{world.ids['bob']}/copy-mode",
                           data={"copy_mode": "never"})
    with world.db.session() as s:
        copies.sweep(s)
        s.commit()
    assert items(world)["doc.txt"][0] == "denied"
    world.web["anna"].post(f"/disks/{world.disk_id('sn:A1')}/shares/{world.ids['bob']}/remove")
    with world.db.session() as s:
        assert s.query(DiskShare).count() == 0


def test_admin_has_no_bypass_for_foreign_disks(world):
    src, _ = setup_anna(world)
    with world.db.session() as s:
        assert copies.permission(s, world.ids["master"], s.scalar(
            select(Disk).where(Disk.disk_key == "sn:A1"))) == "never"


def test_unowned_disk_cannot_be_copied_by_anyone(world):
    with world.db.session() as s:
        from diskatlas.db.models import Disk as D
        s.add(D(disk_key="sn:OLD", size_bytes=1))
        s.commit()
        assert copies.permission(s, world.ids["anna"], s.scalar(
            select(D).where(D.disk_key == "sn:OLD"))) == "never"


# ------------------------------------------------------------------ Ablauf und Fristen
def test_requests_are_private_and_only_the_requester_can_cancel(world):
    src, dst = setup_anna(world)
    request_files(world, "anna", [f"f:{src}:doc.txt"], dst)
    with world.db.session() as s:
        rid = s.scalar(select(CopyRequest.id))
    assert world.web["anna"].get(f"/requests/{rid}").status_code == 200
    for who in ("bob", "master"):
        assert world.web[who].get(f"/requests/{rid}").status_code == 404
        assert world.web[who].post(f"/requests/{rid}/cancel").status_code == 404
    assert items(world)["doc.txt"][0] == "queued"
    world.web["anna"].post(f"/requests/{rid}/cancel")
    assert items(world)["doc.txt"][0] == "cancelled"


def test_items_expire(world):
    src, dst = setup_anna(world)
    set_online(world, "sn:A1", False)
    request_files(world, "anna", [f"f:{src}:doc.txt"], dst)
    with world.db.session() as s:
        copies.sweep(s, utcnow() + copies.WAIT_TTL + timedelta(hours=1))
        s.commit()
    assert items(world)["doc.txt"][0] == "expired"


def test_running_item_with_expired_lease_is_retried_then_fails(world):
    src, dst = setup_anna(world)
    request_files(world, "anna", [f"f:{src}:doc.txt"], dst)
    for expected in ("queued", "queued", "failed"):
        with world.db.session() as s:
            item = s.scalar(select(CopyItem))
            item.state, item.lease_until = "running", utcnow() - timedelta(minutes=1)
            s.commit()
            copies.sweep(s)
            s.commit()
        assert items(world)["doc.txt"][0] == expected


def test_feature_switch_and_login(world):
    src, dst = setup_anna(world)
    world.app.state.config.server.copy_enabled = False
    for method, url in (("get", "/requests"), ("post", "/requests/preview")):
        assert getattr(world.web["anna"], method)(url).status_code == 404
    from fastapi.testclient import TestClient
    assert TestClient(world.app, follow_redirects=False).post("/requests/preview").status_code in (
        401, 303, 404)
    assert src and dst


def test_preview_page_lists_what_will_happen(world):
    src, _ = setup_anna(world)
    bob_vol = setup_bob_target(world)
    share(world, "sn:A1", "bob", "ask")
    html = world.web["bob"].post("/requests/preview", data={"sel": [f"d:{src}:film"]}).text
    assert "Der Besitzer wird gefragt" in html and "BobsZiel" in html
    assert bob_vol
    denied = world.web["bob"].post("/requests/preview", data={"sel": ["f:999:x"]})
    assert denied.status_code == 400


def test_progress_tells_the_agent_to_stop_when_permission_is_revoked(world):
    from diskatlas.db.models import Client
    from diskatlas.services import transfers

    src, _ = setup_anna(world)
    bob_vol = setup_bob_target(world)
    share(world, "sn:A1", "bob", "always")
    request_files(world, "bob", [f"f:{src}:doc.txt"], bob_vol)
    with world.db.session() as s:
        bob_client = s.scalar(select(Client).where(Client.user_id == world.ids["bob"]))
        item = s.scalar(select(CopyItem))
        item.state, item.claimed_by_client_id = "running", bob_client.id
        s.commit()
        assert transfers.progress(s, bob_client, item.id, 1) == {"abort": False}
        s.commit()
        share_row = s.get(DiskShare, (world.disk_id("sn:A1"), world.ids["bob"]))
        share_row.copy_mode = "never"
        s.commit()
        assert transfers.progress(s, bob_client, item.id, 2) == {"abort": True}
        s.commit()
    assert items(world)["doc.txt"][0] == "cancelled"
