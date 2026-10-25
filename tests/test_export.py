"""CSV/JSON-Export von Platten und Dateilisten (Roadmap M3)."""

import csv
import io
import json

import pytest
from test_authz import World

FILES = [["filme/a.mkv", 1000, 1_700_000_000.0], ["=HYPERLINK(1).txt", 5, 1_700_000_000.0],
         ["doc.txt", 7, 1_700_000_000.0]]


@pytest.fixture
def world(db, tmp_path) -> World:
    w = World(db, tmp_path)
    w.ingest_disk("anna", "sn:A1", "pc", label="Archiv")
    w.index("anna", "sn:A1", FILES)
    w.ingest_disk("bob", "sn:B1", "pc-bob")
    w.index("bob", "sn:B1", [["bob-geheim.mkv", 1, 1_700_000_000.0]])
    return w


def _csv(response):
    assert response.content.startswith("﻿".encode()), "BOM für Excel"
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))


def test_disks_csv_and_json_contain_only_visible_disks(world):
    r = world.web["anna"].get("/export/disks.csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = _csv(r)
    assert rows[0][:4] == ["Name", "Hersteller", "Modell", "Seriennummer"]
    assert [row[3] for row in rows[1:]] == ["A1"]
    data = world.web["anna"].get("/export/disks.json").json()
    assert [d["serial"] for d in data] == ["A1"] and data[0]["files"] == 3
    assert [d["serial"] for d in world.web["master"].get("/export/disks.json").json()] == [
        "A1", "B1"]


def test_disk_filters_apply(world):
    assert world.web["master"].get("/export/disks.json?q=B1").json()[0]["serial"] == "B1"
    assert world.web["master"].get("/export/disks.json?health=failed").json() == []


def test_files_export_streams_all_matches_and_defuses_formulas(world):
    rows = _csv(world.web["anna"].get(f"/export/files.csv?disk={world.disk_id('sn:A1')}"))
    assert rows[0] == ["Festplatte", "Partition", "Pfad", "Name", "Endung", "Größe (Bytes)",
                       "Geändert"]
    paths = sorted(row[2] for row in rows[1:])
    assert paths == ["'=HYPERLINK(1).txt", "doc.txt", "filme/a.mkv"], "Formel entschärft"
    data = world.web["anna"].get("/export/files.json?q=mkv").json()
    assert [d["path"] for d in data] == ["filme/a.mkv"], "Bobs Dateien nie"
    assert world.web["bob"].get(
        f"/export/files.json?disk={world.disk_id('sn:A1')}").json() == []


def test_files_export_needs_a_filter_and_valid_format(world):
    assert world.web["anna"].get("/export/files.csv").status_code == 400
    assert world.web["anna"].get("/export/files.xml?q=a").status_code == 404
    assert world.web["anna"].get("/export/disks.exe").status_code == 404


def test_export_needs_login(world):
    from fastapi.testclient import TestClient

    anonymous = TestClient(world.app, follow_redirects=False)
    assert anonymous.get("/export/disks.csv").status_code == 303


def test_large_export_is_streamed(world, monkeypatch):
    from diskatlas.web import export

    monkeypatch.setattr(export, "_BATCH", 1)  # stückweise aus der Datenbank
    data = world.web["anna"].get("/export/files.json?q=.").json()
    assert len(data) == 3
    assert json.loads(json.dumps(data))[0]["size"] in (1000, 5, 7)


def test_links_on_pages(world):
    anna = world.web["anna"]
    assert "/export/disks.csv?" in anna.get("/?health=ok").text
    assert "/export/files.csv?q=mkv" in anna.get("/files?q=mkv").text
    assert f"/export/files.csv?disk={world.disk_id('sn:A1')}" in anna.get(
        f"/disks/{world.disk_id('sn:A1')}").text
