import os
import sys
from datetime import timedelta

import pytest
from conftest import make_disk
from sqlalchemy import select

from diskatlas.db.models import Disk, Label, Setting
from diskatlas.probe import ports
from diskatlas.probe.types import FileRecord
from diskatlas.services import bays, duplicates, hosts, ingest, queries


def _index(db, key, records):
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk(key))
    vol = f"part:{key}-1"
    with db.session() as s:
        scan = ingest.begin_index(s, key, vol)
    with db.session() as s:
        ingest.add_files(s, key, vol, scan, records)
    with db.session() as s:
        ingest.finish_index(s, key, vol, scan, errors=0, success=True)


def _tag_system(db, key):
    with db.session() as s:
        label = Label(name="System")
        s.add(label)
        disk = s.scalar(select(Disk).where(Disk.disk_key == key))
        disk.labels.append(label)


def test_system_disk_hidden_in_search_duplicates_and_dashboard(db, client):
    rec = [FileRecord("Film.mkv", 500, None)]
    _index(db, "sn:SYS", rec)
    _index(db, "sn:DATA", rec)
    _tag_system(db, "sn:SYS")

    with db.session() as s:
        ids = queries.system_disk_ids(s)
        assert len(ids) == 1
        _, total = queries.search_files(s, queries.FileQuery(q="film"))
        assert total == 2
        _, total = queries.search_files(s, queries.FileQuery(q="film", exclude_disk_ids=ids))
        assert total == 1
        groups, n, _ = duplicates.find_file_duplicates(s, duplicates.FileDupQuery())
        assert n == 1
        groups, n, _ = duplicates.find_file_duplicates(
            s, duplicates.FileDupQuery(exclude_disk_ids=ids)
        )
        assert n == 0
        folder_groups, _ = duplicates.find_folder_duplicates(
            s, duplicates.FolderDupQuery(min_files=1, exclude_disk_ids=ids)
        )
        assert folder_groups == []

    # Standard: ausgeblendet; per Cookie wieder sichtbar
    assert "1 ausgeblendet" in client.get("/").text
    assert "1 ausgeblendet" not in client.get("/", cookies={"diskatlas_hide_system": "0"}).text
    assert client.get("/files?q=film").text.count('class="filename"') == 1
    assert client.get(
        "/files?q=film", cookies={"diskatlas_hide_system": "0"}
    ).text.count('class="filename"') == 2


def test_hide_system_toggle_sets_cookie_and_stays_local(client):
    r = client.post(
        "/prefs/hide-system", data={"hide": ["0"]}, follow_redirects=False,
        headers={"referer": "http://testserver/files?q=a"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/files?q=a", "nur lokaler Pfad, kein Open-Redirect"
    foreign = client.post("/prefs/hide-system", data={"hide": ["0"]}, follow_redirects=False,
                          headers={"referer": "http://evil.example/files?q=a"})
    assert foreign.status_code == 403, "Fremdseiten-Anfragen werden gar nicht erst bearbeitet"
    assert "diskatlas_hide_system=0" in r.headers["set-cookie"]
    r = client.post("/prefs/hide-system", data={"hide": ["0", "1"]}, follow_redirects=False)
    assert "diskatlas_hide_system=1" in r.headers["set-cookie"]


def test_bay_config_roundtrip_and_legacy_import(db, tmp_path):
    with db.session() as s:
        assert bays.load_config(s) == bays.BayConfig()
        bays.save_config(s, bays.BayConfig(host="pc", ports=["ata3", None, "ata5"], reverse=True))
    with db.session() as s:
        cfg = bays.load_config(s)
        assert (cfg.host, cfg.ports, cfg.reverse) == ("pc", ["ata3", None, "ata5", None], True)
        try:
            bays.save_config(s, bays.BayConfig(ports=["ata3", "ata3"]))
        except ValueError:
            pass
        else:
            raise AssertionError("doppelter Port muss abgelehnt werden")

    legacy = tmp_path / "bays.json"
    legacy.write_text('{"ports": ["ata6", "ata5"], "reverse": true}', encoding="utf-8")
    with db.session() as s:
        assert not bays.import_legacy_file(s, legacy, "pc"), "vorhandene Einstellung bleibt"
        s.delete(s.get(Setting, "bays"))
    with db.session() as s:
        assert bays.import_legacy_file(s, legacy, "mypc")
    with db.session() as s:
        cfg = bays.load_config(s)
        assert (cfg.host, cfg.ports[:2], cfg.reverse) == ("mypc", ["ata6", "ata5"], True)


def test_build_bays_states_and_display_order(db):
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk("sn:TEST1"))
        hosts.record(s, "pc", {
            "all_ports": ["ata3", "ata4", "ata5"],
            "present": [
                {"port": "ata3", "device": "/dev/sdb", "serial": "TEST1", "disk_key": "sn:TEST1"},
                {"port": "ata4", "device": "/dev/sdc", "serial": "UNBEKANNT"},
            ],
        })
    with db.session() as s:
        snap = hosts.get(s, "pc")
        cfg = bays.BayConfig(host="pc", ports=["ata3", "ata4", "ata5", None])
        result = bays.build_bays(s, cfg, snap)
        assert [b.state for b in result] == ["disk", "unknown", "empty", "unassigned"]
        assert result[0].disk.serial == "TEST1"
        cfg.reverse = True
        assert [b.number for b in bays.build_bays(s, cfg, snap)] == [4, 3, 2, 1]
        # Agent meldet sich nicht mehr -> „offline“ statt „leer“
        snap.updated_at = snap.updated_at - timedelta(seconds=hosts.FRESH_SECONDS + 5)
        assert bays.build_bays(s, cfg, snap)[3].state == "offline"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="sysfs gibt es nur unter Linux")
def test_sata_ports_from_sysfs(tmp_path):
    dev = tmp_path / "devices/pci0000:00/0000:00:17.0/ata3/host2/target2:0:0/2:0:0:0/block/sdb"
    dev.mkdir(parents=True)
    (tmp_path / "block").mkdir()
    os.symlink(dev, tmp_path / "block" / "sdb")
    (tmp_path / "class/ata_port/ata3").mkdir(parents=True)
    (tmp_path / "class/ata_port/ata1").mkdir()
    (tmp_path / "class/ata_port/ata10").mkdir()
    found = ports.sata_ports(str(tmp_path))
    assert list(found) == ["ata3"] and found["ata3"].device == "/dev/sdb"
    assert ports.all_ports(str(tmp_path)) == ["ata1", "ata3", "ata10"], "numerisch sortiert"


def test_dashboard_stats_unknown_capacity(db):
    _index(db, "sn:A", [FileRecord("x", 1, None)])
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk("sn:B", mountpoint=None))
        for vol in queries.load_disks(s)[1].volumes:
            vol.used_bytes = vol.free_bytes = None
    with db.session() as s:
        stats = queries.dashboard_stats(queries.load_disks(s))
    assert stats.unknown == 4_000_000_000_000, "nur die Platte ohne bekannte Belegung"
    assert stats.used + stats.free + stats.unknown <= stats.capacity


def test_filesystem_filter_sort_group_and_usage(db):
    from diskatlas.probe.types import VolumeInfo

    def disk(key, fs, used):
        info = make_disk(key)
        info.volumes = [VolumeInfo(key=f"part:{key}", fs_type=fs, size_bytes=10,
                                   used_bytes=used, free_bytes=None if used is None else 5)]
        return info

    with db.session() as s:
        ingest.upsert_disk(s, "pc", disk("sn:N", "ntfs", 5))
        ingest.upsert_disk(s, "pc", disk("sn:E", "ext4", None))
        ingest.upsert_disk(s, "pc", disk("sn:X", None, None))
    with db.session() as s:
        disks = queries.load_disks(s)
        names = lambda flt: sorted(d.disk_key for d in queries.filter_disks(disks, flt))  # noqa: E731
        assert names(queries.DiskFilter(fs="ntfs")) == ["sn:N"]
        assert names(queries.DiskFilter(fs=queries.NO_FS)) == ["sn:X"]
        assert names(queries.DiskFilter(usage="unknown")) == ["sn:E", "sn:X"]
        assert names(queries.DiskFilter(usage="known")) == ["sn:N"]
        assert names(queries.DiskFilter(q="ext4")) == ["sn:E"], "Freitext findet Dateisysteme"
        order = [d.disk_key for d in queries.sort_disks(disks, "fs")]
        assert order == ["sn:E", "sn:N", "sn:X"], "ohne Dateisystem zuletzt"
        groups = dict(queries.group_disks(queries.sort_disks(disks, "fs"), "fs"))
        assert set(groups) == {"ext4", "ntfs", "(kein Dateisystem)"}
        assert queries.known_fs_types(disks) == ["ext4", "ntfs"]


def test_reformat_resets_file_index(db):
    from diskatlas.db.models import FileEntry, Volume

    _index(db, "sn:R", [FileRecord("alt.txt", 5, None)])
    key = "sn:R"
    with db.session() as s:
        vol = s.scalar(select(Volume))
        vol.fs_uuid = "OLD-UUID"
    info = make_disk(key)
    info.volumes[0].fs_uuid = "NEW-UUID"  # gleiche Partition, neues Dateisystem
    with db.session() as s:
        ingest.upsert_disk(s, "pc", info)
    with db.session() as s:
        vol = s.scalar(select(Volume))
        assert vol.index_status == "never" and vol.file_count == 0
        assert s.scalar(select(FileEntry.id)) is None, "alter Dateiindex ist verworfen"
        assert vol.fs_uuid == "NEW-UUID"
