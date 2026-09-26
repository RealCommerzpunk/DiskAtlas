from conftest import make_disk

from diskatlas.probe.types import FileRecord
from diskatlas.services import duplicates, ingest


def _index(db, disk_key, records):
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk(disk_key))
    vol_key = f"part:{disk_key}-1"
    with db.session() as s:
        scan = ingest.begin_index(s, disk_key, vol_key)
    with db.session() as s:
        ingest.add_files(s, disk_key, vol_key, scan, records)
    with db.session() as s:
        ingest.finish_index(s, disk_key, vol_key, scan, errors=0, success=True)


def _rec(path, size):
    return FileRecord(path, size, 1_700_000_000.0)


def test_file_duplicates(db):
    _index(db, "sn:A", [_rec("x/Film.mkv", 500), _rec("x/klein.txt", 1), _rec("leer.bin", 0)])
    _index(db, "sn:B", [_rec("y/film.MKV", 500), _rec("y/klein.txt", 2), _rec("leer.bin", 0)])
    with db.session() as s:
        groups, total, wasted = duplicates.find_file_duplicates(s, duplicates.FileDupQuery())
    assert total == 1, "gleicher Name+Größe; andere Größe und leere Dateien zählen nicht"
    assert wasted == 500
    assert {e.path for e, _, _ in groups[0].members} == {"x/Film.mkv", "y/film.MKV"}


def test_folder_duplicates_reports_only_outermost(db):
    tree = [_rec("Serie/S1/e1.mkv", 100), _rec("Serie/S1/e2.mkv", 200), _rec("Serie/info.txt", 5)]
    _index(db, "sn:A", tree + [_rec("Nur-hier/a.bin", 9), _rec("Nur-hier/b.bin", 9)])
    _index(db, "sn:B", [_rec("Kopie/" + r.path.split("/", 1)[1], r.size) for r in tree]
           + [_rec("Kopie/S1/extra.txt", 1)])
    _index(db, "sn:C", [_rec("Backup/" + r.path, r.size) for r in tree])
    with db.session() as s:
        groups, wasted = duplicates.find_folder_duplicates(s, duplicates.FolderDupQuery())
    # sn:B/Kopie hat eine Zusatzdatei und ist daher keine Doublette von Serie; Serie und
    # Backup/Serie (A und C) sind identisch, ihr Unterordner S1 wird deshalb nicht extra gemeldet.
    sigs = {(g.file_count, tuple(sorted((r.disk.disk_key, r.path) for r in g.folders)))
            for g in groups}
    assert (3, (("sn:A", "Serie"), ("sn:C", "Backup/Serie"))) in sigs
    assert not any(g.file_count == 2 and len(g.folders) == 2
                   and {r.path for r in g.folders} == {"Serie/S1", "Backup/Serie/S1"}
                   for g in groups), "Unterordner der gemeldeten Doublette wird ausgeblendet"
    assert wasted > 0
