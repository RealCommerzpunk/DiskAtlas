from conftest import make_disk
from sqlalchemy import func, select

from diskatlas.db.models import Disk, FileEntry, SmartSnapshot
from diskatlas.probe.types import FileRecord, SmartInfo, VolumeInfo
from diskatlas.services import ingest, queries


def test_upsert_creates_and_updates(db):
    with db.session() as s:
        ingest.upsert_disk(s, "pc1", make_disk())
    with db.session() as s:
        info = make_disk()
        info.volumes[0].used_bytes = 2_000_000_000_000
        info.volumes[0].free_bytes = 2_000_000_000_000
        ingest.upsert_disk(s, "pc2", info)
    with db.session() as s:
        (disk,) = queries.load_disks(s)
        assert disk.last_host == "pc2"
        assert disk.display_name == "Archiv"
        assert disk.usage_percent == 50
        assert disk.health == "ok"
        assert s.scalar(select(func.count(SmartSnapshot.id))) == 2


def test_unknown_values_do_not_overwrite(db):
    with db.session() as s:
        ingest.upsert_disk(s, "linux", make_disk())
    with db.session() as s:
        info = make_disk()
        # z. B. Windows sieht die ext4-Partition, kennt aber weder Dateisystem noch Label
        info.volumes[0] = VolumeInfo(key=info.volumes[0].key)
        info.smart = SmartInfo(available=False, error="keine Rechte")
        ingest.upsert_disk(s, "windows", info)
    with db.session() as s:
        (disk,) = queries.load_disks(s)
        vol = disk.volumes[0]
        assert vol.fs_type == "ext4"
        assert vol.label == "Archiv"
        assert vol.used_bytes == 1_000_000_000_000, "letzte bekannte Belegung bleibt erhalten"
        assert vol.mountpoint is None
        assert disk.health == "ok", "SMART-Ausfall überschreibt nicht den letzten Befund"
        assert disk.smart_error == "keine Rechte"


def test_missing_volume_is_kept_but_marked(db):
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk())
    with db.session() as s:
        info = make_disk()
        info.volumes = []
        ingest.upsert_disk(s, "pc", info)
    with db.session() as s:
        (disk,) = queries.load_disks(s)
        assert len(disk.volumes) == 1
        assert disk.volumes[0].present is False
        assert disk.used_bytes is None


def test_mark_connected(db):
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk("sn:A"))
        ingest.upsert_disk(s, "pc", make_disk("sn:B"))
        ingest.upsert_disk(s, "other", make_disk("sn:C"))
    with db.session() as s:
        ingest.mark_connected(s, "pc", ["sn:A"])
    with db.session() as s:
        state = {d.disk_key: d.is_connected for d in s.scalars(select(Disk))}
        assert state == {"sn:A": True, "sn:B": False, "sn:C": True}


def test_file_index_replaces_previous_scan(db):
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk())
    key, vol_key = "sn:TEST1", "part:sn:TEST1-1"

    def run_scan(records, success=True):
        with db.session() as s:
            scan = ingest.begin_index(s, key, vol_key)
        with db.session() as s:
            ingest.add_files(s, key, vol_key, scan, records)
        with db.session() as s:
            ingest.finish_index(s, key, vol_key, scan, errors=0, success=success)

    run_scan([FileRecord("Filme/a.mkv", 100, 1_700_000_000.0), FileRecord("b.txt", 5, None)])
    run_scan([FileRecord("Filme/c.mkv", 300, 1_700_000_000.0)])
    run_scan([FileRecord("kaputt.bin", 1, None)], success=False)

    with db.session() as s:
        names = sorted(s.scalars(select(FileEntry.name)))
        assert names == ["c.mkv"], "alter Scan ersetzt, fehlgeschlagener verworfen"
        (disk,) = queries.load_disks(s)
        vol = disk.volumes[0]
        assert vol.file_count == 1
        assert vol.file_bytes == 300
        assert vol.index_status == "failed"
        rows, total = queries.search_files(s, queries.FileQuery(q="filme"))
        assert total == 1
        assert rows[0][0].extension == "mkv"
        for pattern, expected in [("*.mkv", 1), ("c.m?v", 1), ("*.txt", 0), ("filme/*.mkv", 1)]:
            _, hits = queries.search_files(s, queries.FileQuery(q=pattern))
            assert hits == expected, pattern


def test_running_index_progress(db):
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk())
    key, vol_key = "sn:TEST1", "part:sn:TEST1-1"
    with db.session() as s:
        assert queries.running_indexes(s) == []
        scan = ingest.begin_index(s, key, vol_key)
    with db.session() as s:
        ingest.add_files(
            s, key, vol_key, scan, [FileRecord("a", 1, None), FileRecord("b", 2, None)]
        )
    with db.session() as s:
        (run,) = queries.running_indexes(s)
        assert run.files_so_far == 2
    with db.session() as s:
        ingest.finish_index(s, key, vol_key, scan)
    with db.session() as s:
        assert queries.running_indexes(s) == []
