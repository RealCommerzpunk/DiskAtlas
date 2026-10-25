"""Automatisches Label für komplett leere Platten (Roadmap M2.1)."""

import pytest
from conftest import make_disk
from sqlalchemy import select
from test_authz import World

from diskatlas.db.models import Disk, FileEntry, Label, User
from diskatlas.probe.types import VolumeInfo
from diskatlas.services import emptylabel


@pytest.fixture
def world(db, tmp_path) -> World:
    return World(db, tmp_path)


def report(world, who, info, host="pc"):
    r = world.agent(who).post("/api/v1/ingest/disk",
                              json={"host": host, "disk": info.model_dump(mode="json")})
    assert r.status_code == 200, r.text


def wiped(key="sn:W1"):
    """So sieht eine Platte nach nwipe aus: keine Partition, kein Dateisystem."""
    info = make_disk(key)
    info.volumes = []
    return info


def fresh_fs(key):
    """Frisch formatiertes Dateisystem: belegt nur Verwaltungsdaten."""
    info = make_disk(key)
    info.volumes[0].used_bytes = 200 * 1024**2
    return info


def labels_of(world, key):
    with world.db.session() as s:
        disk = s.scalar(select(Disk).where(Disk.disk_key == key))
        return sorted(lab.name for lab in disk.labels)


def label_owner(world, name):
    with world.db.session() as s:
        return [lab.owner_user_id for lab in s.scalars(select(Label).where(Label.name == name))]


# ------------------------------------------------------------------ Wann ist eine Platte leer?
def test_wiped_disk_gets_the_owners_label(world):
    report(world, "anna", wiped())
    assert labels_of(world, "sn:W1") == ["leer"]
    assert label_owner(world, "leer") == [world.ids["anna"]], "Label gehört der Besitzerin"


def test_partition_without_filesystem_counts_as_empty(world):
    info = make_disk("sn:P1")
    info.volumes = [VolumeInfo(key="part:P1-1", device="/dev/sdx1", size_bytes=10)]
    report(world, "anna", info)
    assert labels_of(world, "sn:P1") == ["leer"]


def test_unreadable_or_unindexed_filesystem_is_not_empty(world):
    report(world, "anna", make_disk("sn:U1"))  # ext4, aber nie indiziert
    luks = make_disk("sn:L1", mountpoint=None)
    luks.volumes[0].fs_type = "crypto_LUKS"
    report(world, "anna", luks)
    assert labels_of(world, "sn:U1") == [] and labels_of(world, "sn:L1") == []


def test_indexed_but_empty_gets_label_and_loses_it_when_files_arrive(world):
    report(world, "anna", fresh_fs("sn:E1"))
    assert world.index("anna", "sn:E1", []) == 200
    assert labels_of(world, "sn:E1") == ["leer"]
    assert world.index("anna", "sn:E1", [["film.mkv", 10, 1_700_000_000.0]]) == 200
    assert labels_of(world, "sn:E1") == [], "Automatik nimmt ihr eigenes Label wieder ab"
    assert world.index("anna", "sn:E1", []) == 200
    assert labels_of(world, "sn:E1") == ["leer"], "wieder leer: wieder dran"


def test_zero_files_but_much_used_space_is_not_called_empty(world):
    """Index mit 0 Dateien, aber 1 TB belegt: Indizierung war unvollständig, also kein „leer“."""
    world.ingest_disk("anna", "sn:R1", "pc")  # make_disk: 1 TB von 4 TB belegt
    world.index("anna", "sn:R1", [])
    assert labels_of(world, "sn:R1") == []
    report(world, "anna", fresh_fs("sn:R2"))
    world.index("anna", "sn:R2", [])
    assert labels_of(world, "sn:R2") == ["leer"]


def test_wiping_drops_the_old_index_from_search(world):
    world.ingest_disk("anna", "sn:X1", "pc")
    world.index("anna", "sn:X1", [["geheim/alt.txt", 10, 1_700_000_000.0]])
    assert world.web["anna"].get("/api/v1/files?q=alt").json()["total"] == 1
    report(world, "anna", wiped("sn:X1"))
    assert world.web["anna"].get("/api/v1/files?q=alt").json()["total"] == 0
    from diskatlas.services import maintenance

    maintenance.run_once(world.db, pause=0)
    with world.db.session() as s:
        assert s.query(FileEntry).count() == 0
    assert labels_of(world, "sn:X1") == ["leer"]


# ------------------------------------------------------------------ Von Hand gesetzt / abgenommen
def test_manual_label_is_never_removed(world):
    world.ingest_disk("anna", "sn:M1", "pc")
    disk_id = world.disk_id("sn:M1")
    world.web["anna"].post(f"/disks/{disk_id}/labels", data={"new_name": "leer"})
    world.index("anna", "sn:M1", [["a.txt", 1, 1_700_000_000.0]])
    assert labels_of(world, "sn:M1") == ["leer"], "von Hand gesetzt: bleibt"


def test_manually_removed_auto_label_is_not_forced_back(world):
    report(world, "anna", wiped())
    disk_id = world.disk_id("sn:W1")
    with world.db.session() as s:
        label_id = s.scalar(select(Label.id).where(Label.name == "leer"))
    world.web["anna"].post(f"/disks/{disk_id}/labels/{label_id}/remove")
    report(world, "anna", wiped())
    assert labels_of(world, "sn:W1") == []


# ------------------------------------------------------------------ Einstellung
def test_setting_renames_the_label_and_can_switch_off(world):
    report(world, "anna", wiped())
    anna = world.web["anna"]
    assert anna.post("/labels/empty-setting", data={"name": "frei"}).status_code == 303
    assert labels_of(world, "sn:W1") == ["frei"], "bestehendes Label umbenannt"
    with world.db.session() as s:
        assert s.get(User, world.ids["anna"]).empty_label == "frei"
    anna.post("/labels/empty-setting", data={"name": ""})
    report(world, "anna", wiped("sn:W2"))
    assert labels_of(world, "sn:W2") == [], "aus"
    anna.post("/labels/empty-setting", data={"name": "frei"})
    assert labels_of(world, "sn:W2") == ["frei"], "beim Einschalten werden Platten nachbewertet"


def test_setting_is_per_user(world):
    world.web["bob"].post("/labels/empty-setting", data={"name": "Bob-leer"})
    report(world, "anna", wiped("sn:A"))
    report(world, "bob", wiped("sn:B"))
    assert labels_of(world, "sn:A") == ["leer"] and labels_of(world, "sn:B") == ["Bob-leer"]
    assert "Bob-leer" not in world.web["anna"].get("/labels").text


def test_rename_clash_is_refused(world):
    report(world, "anna", wiped())
    world.web["anna"].post("/labels", data={"name": "frei"})
    r = world.web["anna"].post("/labels/empty-setting", data={"name": "frei"})
    assert "error" in r.headers["location"]
    assert labels_of(world, "sn:W1") == ["leer"]


def test_renaming_on_the_labels_page_updates_the_setting(world):
    report(world, "anna", wiped())
    with world.db.session() as s:
        label_id = s.scalar(select(Label.id).where(Label.name == "leer"))
    world.web["anna"].post(f"/labels/{label_id}", data={"name": "Leer (gelöscht)"})
    report(world, "anna", wiped("sn:W2"))
    assert labels_of(world, "sn:W2") == ["Leer (gelöscht)"]
    assert len(label_owner(world, "leer")) == 0


def test_unowned_disks_get_no_label_when_users_exist(world):
    with world.db.session() as s:
        disk = Disk(disk_key="sn:OLD", size_bytes=1, volumes=[])
        s.add(disk)
        s.flush()
        emptylabel.apply(s, disk)
        assert disk.labels == []


def test_previous_owners_auto_label_does_not_follow_the_disk(world):
    report(world, "anna", wiped())
    with world.db.session() as s:
        disk = s.scalar(select(Disk).where(Disk.disk_key == "sn:W1"))
        disk.owner_user_id = world.ids["bob"]
        disk.labels = []
        s.flush()
        emptylabel.apply(s, disk)
        assert [(lab.name, lab.owner_user_id) for lab in disk.labels] == [
            ("leer", world.ids["bob"])]


# ------------------------------------------------------------------ Lokaler Betrieb
def test_local_mode_without_login(client):
    info = wiped("sn:LOC")
    assert client.post("/api/v1/ingest/disk",
                       json={"host": "pc", "disk": info.model_dump(mode="json")}).status_code == 200
    page = client.get("/labels").text
    assert 'value="leer"' in page
    client.post("/labels/empty-setting", data={"name": "Leer-lokal"})
    assert "Leer-lokal" in client.get("/").text


def test_server_start_labels_disks_reported_before_the_feature(db, tmp_path):
    """Platten aus älteren Versionen (offline im Regal) bekommen das Label beim Serverstart."""
    from diskatlas.config import Config
    from diskatlas.web.app import create_app

    with db.session() as s:
        s.add(Disk(disk_key="sn:REGAL", size_bytes=1, volumes=[]))
    create_app(Config(), db, bays_path=tmp_path / "none.json")
    with db.session() as s:
        disk = s.scalar(select(Disk).where(Disk.disk_key == "sn:REGAL"))
        assert [lab.name for lab in disk.labels] == ["leer"]
