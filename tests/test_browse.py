"""Dateibrowser: Ordnerindex, Auflistung, Zugriffsschutz."""

import pytest
from sqlalchemy import select
from test_authz import MARK, World

from diskatlas.db.models import Directory, FileEntry, Volume
from diskatlas.services import browse


@pytest.fixture
def world(db, tmp_path) -> World:
    return World(db, tmp_path)


FILES = [
    ["readme.txt", 10, 1_700_000_000.0],
    ["filme/a.mkv", 1000, 1_700_000_000.0],
    ["filme/b.mkv", 2000, 1_700_000_000.0],
    ["filme/serien/s1/e1.mkv", 500, 1_700_000_000.0],
    ["musik/x.mp3", 5, 1_700_000_000.0],
]


def _volume_id(world, key):
    with world.db.session() as s:
        return s.scalar(select(Volume.id).join(Volume.disk).where(Volume.disk.has(disk_key=key)))


def test_normalize_path_rejects_traversal_and_nul():
    assert browse.normalize_path("a//b/./c/") == "a/b/c"
    assert browse.normalize_path("a\\b") == "a/b"
    assert browse.normalize_path("") == ""
    assert browse.normalize_path("../etc") is None
    assert browse.normalize_path("a/../b") is None
    assert browse.normalize_path("a\0b") is None
    assert browse.normalize_path("/etc") == "etc", "führender Schrägstrich bleibt im Volume"


def test_crumbs():
    assert browse.crumbs("a/b") == [("a", "a"), ("b", "a/b")]
    assert browse.crumbs("") == []


def _setup(world):
    world.ingest_disk("anna", "sn:ANNA1", "pc-anna")
    world.index("anna", "sn:ANNA1", FILES)
    return _volume_id(world, "sn:ANNA1")


def test_ingest_computes_parent_and_directory_totals(world):
    vol = _setup(world)
    with world.db.session() as s:
        parents = dict(s.execute(select(FileEntry.path, FileEntry.parent)).all())
        assert parents["readme.txt"] == ""
        assert parents["filme/serien/s1/e1.mkv"] == "filme/serien/s1"
        dirs = {d.path: (d.parent, d.file_count, d.total_size)
                for d in s.scalars(select(Directory))}
    assert dirs["filme"] == ("", 3, 3500), "Summe zählt Unterordner mit"
    assert dirs["filme/serien"] == ("filme", 1, 500)
    assert dirs["filme/serien/s1"] == ("filme/serien", 1, 500)
    assert vol


def test_browse_lists_folders_and_files_and_walks_down(world):
    vol = _setup(world)
    anna = world.web["anna"]
    root = anna.get(f"/files?volume={vol}")
    assert root.status_code == 200
    assert "readme.txt" in root.text and "filme" in root.text and "a.mkv" not in root.text
    sub = anna.get(f"/files?volume={vol}&path=filme")
    assert "a.mkv" in sub.text and "b.mkv" in sub.text and "serien" in sub.text
    assert "e1.mkv" not in sub.text
    assert f'value="d:{vol}:filme/serien"' in sub.text
    assert f'value="f:{vol}:filme/a.mkv"' in sub.text
    assert "e1.mkv" in anna.get(f"/files?volume={vol}&path=filme/serien/s1").text


def test_browse_rebuilds_missing_directory_index_for_old_data(world):
    vol = _setup(world)
    with world.db.session() as s:
        s.query(Directory).delete()
        s.get(Volume, vol).dirs_scan_id = None
        s.commit()
    assert "filme" in world.web["anna"].get(f"/files?volume={vol}").text


def test_browse_paginates_by_name(world):
    world.ingest_disk("anna", "sn:ANNA1", "pc-anna")
    many = [[f"d/f{i:04d}.txt", 1, 1_700_000_000.0] for i in range(browse.PAGE + 30)]
    world.index("anna", "sn:ANNA1", many)
    vol = _volume_id(world, "sn:ANNA1")
    first = world.web["anna"].get(f"/files?volume={vol}&path=d")
    assert "f0000.txt" in first.text and f"f{browse.PAGE:04d}.txt" not in first.text
    assert "weitere Dateien" in first.text
    last = f"f{browse.PAGE - 1:04d}.txt"
    second = world.web["anna"].get(f"/files?volume={vol}&path=d&after={last}")
    assert f"f{browse.PAGE:04d}.txt" in second.text and last not in second.text


def test_browse_hides_foreign_disks_and_bad_paths(world):
    vol = _setup(world)
    bob = world.web["bob"]
    for url in (f"/files?volume={vol}", f"/files?volume={vol}&path=filme"):
        r = bob.get(url)
        assert r.status_code == 404 and "readme" not in r.text and "a.mkv" not in r.text
    anna = world.web["anna"]
    for bad in ("../x", "filme/../../x", "gibtsnicht", "a%00b"):
        assert anna.get(f"/files?volume={vol}&path={bad}").status_code == 404, bad
    assert anna.get("/files?volume=abc").status_code == 404
    assert anna.get("/files?volume=99999").status_code == 404
    assert world.web["master"].get(f"/files?volume={vol}").status_code == 200


def test_disk_list_offers_only_own_indexed_disks(world):
    _setup(world)
    assert "Durchblättern" in world.web["anna"].get("/files").text
    assert "Durchblättern" not in world.web["bob"].get("/files").text
    assert MARK not in world.web["bob"].get("/files").text


def test_search_results_have_checkboxes(world):
    _setup(world)
    html = world.web["anna"].get("/files?q=mkv").text
    assert 'name="sel"' in html and "Datei anfordern" in html
