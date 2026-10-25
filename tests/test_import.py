"""Altbestand importieren: Platten einer alten DiskAtlas-Datenbank in die laufende übernehmen."""

import time

import pytest
from alembic import command
from conftest import make_disk
from sqlalchemy import func, select, text
from test_authz import World

from diskatlas.agent.sinks import DatabaseSink
from diskatlas.db import Database, alembic_config
from diskatlas.db.models import Disk, FileEntry, Label, Setting, SmartSnapshot, Volume
from diskatlas.services import ingest
from diskatlas.tools import importer

FILES = [[f"ordner/datei{i}.txt", i, 1_700_000_000.0] for i in range(25)]


def make_source(path, keys=("sn:OLD1", "sn:OLD2", "sn:SHARED")):
    """Alte Einzelplatz-Datenbank (ohne Benutzer) mit Platten, Dateien, SMART und Labels."""
    db = Database(f"sqlite:///{path.as_posix()}")
    db.upgrade()
    sink = DatabaseSink(db)
    for key in keys:
        sink.report_disk("altpc", make_disk(key))
    with db.session() as s:
        scan = ingest.begin_index(s, "sn:OLD1", "part:sn:OLD1-1")
        ingest.add_files(s, "sn:OLD1", "part:sn:OLD1-1", scan, FILES)
        ingest.finish_index(s, "sn:OLD1", "part:sn:OLD1-1", scan)
        disk = s.scalar(select(Disk).where(Disk.disk_key == "sn:OLD1"))
        label = Label(name="Daten Papa", category="Inhalt", color="#123456")
        s.add(label)
        disk.labels.append(label)
        disk.notes, disk.location, disk.custom_name = "Notiz", "Keller", "Papas Platte"
    db.dispose()
    return path


@pytest.fixture
def world(db, tmp_path) -> World:
    w = World(db, tmp_path)
    w.ingest_disk("bob", "sn:SHARED", "pc-bob")  # gehört schon Bob: bleibt unangetastet
    return w


def disks(world, who):
    return {d["serial"]: d for d in world.web[who].get("/api/v1/disks").json()}


def test_import_gives_disks_with_everything_to_the_chosen_user(world, tmp_path):
    source = make_source(tmp_path / "alt.db")
    report = importer.import_database(world.db, source, "Anna")
    assert (report.disks, report.files, report.skipped) == (2, 25, ["Archiv"])
    anna = disks(world, "anna")
    assert set(anna) == {"OLD1", "OLD2"} and "SHARED" not in anna
    assert set(disks(world, "bob")) == {"SHARED"}, "Bobs Platte unverändert, nichts Neues"
    with world.db.session() as s:
        old1 = s.scalar(select(Disk).where(Disk.disk_key == "sn:OLD1"))
        assert (old1.owner_user_id, old1.notes, old1.location, old1.custom_name) == (
            world.ids["anna"], "Notiz", "Keller", "Papas Platte")
        assert old1.is_connected is False, "steht im Regal, bis ein Agent sie meldet"
        assert [(lab.name, lab.owner_user_id, lab.color) for lab in old1.labels] == [
            ("Daten Papa", world.ids["anna"], "#123456")]
        assert s.scalar(select(func.count()).select_from(SmartSnapshot)
                        .where(SmartSnapshot.disk_id == old1.id)) == 1
        assert s.scalar(select(func.count()).select_from(Setting)
                        .where(Setting.key.like("import_incomplete:%"))) == 0
    found = world.web["anna"].get("/api/v1/files?q=datei1").json()
    assert found["total"] > 0, "Dateiindex aktiv und durchsuchbar"
    vol = world.web["anna"].get(f"/files?volume={old1_volume(world)}&path=ordner")
    assert "datei7.txt" in vol.text, "Ordneransicht entsteht beim ersten Aufruf"


def old1_volume(world):
    with world.db.session() as s:
        return s.scalar(select(Volume.id).join(Disk).where(Disk.disk_key == "sn:OLD1"))


def test_existing_labels_are_reused_and_empty_disks_get_the_empty_label(world, tmp_path):
    world.web["anna"].post("/labels", data={"name": "Daten Papa"})
    importer.import_database(world.db, make_source(tmp_path / "alt.db"), "Anna")
    with world.db.session() as s:
        names = sorted(n for (n,) in s.execute(
            select(Label.name).where(Label.owner_user_id == world.ids["anna"])))
    assert names.count("Daten Papa") == 1


def test_second_run_skips_everything(world, tmp_path):
    source = make_source(tmp_path / "alt.db")
    importer.import_database(world.db, source, "Anna")
    again = importer.import_database(world.db, source, "Anna")
    assert again.disks == 0 and len(again.skipped) == 3
    with world.db.session() as s:
        assert s.scalar(select(func.count()).select_from(FileEntry)) == 25


def test_interrupted_import_resumes_without_duplicates(world, tmp_path, monkeypatch):
    source = make_source(tmp_path / "alt.db")
    monkeypatch.setattr(importer, "BATCH", 10)
    real = importer._flush
    calls = {"n": 0}

    def flaky(target, rows):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("Container neu gestartet")
        return real(target, rows)

    monkeypatch.setattr(importer, "_flush", flaky)
    with pytest.raises(OSError):
        importer.import_database(world.db, source, "Anna")
    assert world.web["anna"].get("/api/v1/files?q=datei").json()["total"] == 0, \
        "halb übernommene Partition ist noch nicht aktiv"
    report = importer.import_database(world.db, source, "Anna")
    assert report.files == 25
    with world.db.session() as s:
        assert s.scalar(select(func.count()).select_from(FileEntry)) == 25
        assert s.scalar(select(Volume.file_count).join(Disk)
                        .where(Disk.disk_key == "sn:OLD1")) == 25
    assert world.web["anna"].get("/api/v1/files?q=datei").json()["total"] == 25


def test_old_schema_source_is_upgraded_on_a_copy(world, tmp_path):
    """Quelle im Stand von Version 0.3 (Schema 0003), wie die alte lokale Datenbank."""
    path = tmp_path / "uralt.db"
    old = Database(f"sqlite:///{path.as_posix()}")
    with old.engine.begin() as conn:
        command.upgrade(alembic_config(conn), "0003")
    with old.engine.begin() as conn:
        conn.execute(text("INSERT INTO disks (id, disk_key, serial, model, removable, is_system,"
                          " health, is_connected, first_seen) VALUES (1, 'sn:URALT', 'URALT',"
                          " 'Alt', 0, 0, 'ok', 1, '2026-09-23 10:00:00')"))
        conn.execute(text("INSERT INTO volumes (id, disk_id, volume_key, fs_type, is_system,"
                          " present, active_scan_id, index_status, index_errors, file_count,"
                          " file_bytes) VALUES (1, 1, 'v1', 'ntfs', 0, 1, 's1', 'done', 0, 1, 5)"))
        conn.execute(text("INSERT INTO files (volume_id, scan_id, path, name, size)"
                          " VALUES (1, 's1', 'a/b.txt', 'b.txt', 5)"))
    old.dispose()
    before = path.read_bytes()
    report = importer.import_database(world.db, path, "Anna")
    assert (report.disks, report.files) == (1, 1)
    assert path.read_bytes() == before, "die Quelle bleibt unverändert"
    assert world.web["anna"].get("/api/v1/files?q=b.txt").json()["total"] == 1


@pytest.mark.parametrize(("content", "message"), [
    (b"kein sqlite", "keine SQLite-Datenbank"),
])
def test_rejects_wrong_files(world, tmp_path, content, message):
    path = tmp_path / "x.db"
    path.write_bytes(content)
    with pytest.raises(importer.ImportError_, match=message):
        importer.import_database(world.db, path, "Anna")


def test_rejects_foreign_sqlite_and_unknown_user(world, tmp_path):
    import sqlite3

    other = tmp_path / "fremd.db"
    sqlite3.connect(other).execute("create table x (a)").connection.commit()
    with pytest.raises(importer.ImportError_, match="keine DiskAtlas-Daten"):
        importer.import_database(world.db, other, "Anna")
    with pytest.raises(importer.ImportError_, match="nicht gefunden"):
        importer.import_database(world.db, make_source(tmp_path / "alt.db"), "Niemand")


def test_admin_upload_runs_in_background(world, tmp_path):
    source = make_source(tmp_path / "alt.db")
    importer.JOB.state = "idle"
    with open(source, "rb") as handle:
        r = world.web["master"].post("/admin/import", data={"owner": "Anna"},
                                     files={"file": ("alt.db", handle.read())})
    assert r.status_code == 303
    for _ in range(200):
        if importer.JOB.state in ("done", "failed"):
            break
        time.sleep(0.05)
    assert importer.JOB.state == "done", importer.JOB.message
    assert set(disks(world, "anna")) == {"OLD1", "OLD2"}
    page = world.web["master"].get("/admin/users").text
    assert "Fertig:" in page and "2 Platte(n)" in page
    upload_dir = tmp_path / "import"
    assert not upload_dir.exists() or not list(upload_dir.iterdir()), "Upload gelöscht"
    with open(source, "rb") as handle:
        assert world.web["anna"].post("/admin/import", data={"owner": "Anna"},
                                      files={"file": ("alt.db", handle.read())}).status_code == 403


def test_cli_import(tmp_path, monkeypatch):
    from diskatlas.cli import main
    from diskatlas.services import users

    target = tmp_path / "server.db"
    db = Database(f"sqlite:///{target.as_posix()}")
    db.upgrade()
    with db.session() as s:
        users.bootstrap_master(s, "geheimgeheim")
    db.dispose()
    monkeypatch.setenv("DISKATLAS_DATABASE_URL", f"sqlite:///{target.as_posix()}")
    monkeypatch.setenv("DISKATLAS_CONFIG", str(tmp_path / "keine.toml"))
    (tmp_path / "keine.toml").write_text("")
    source = make_source(tmp_path / "alt.db")
    assert main(["db", "import", str(source), "--user", "Admin"]) == 0
    db = Database(f"sqlite:///{target.as_posix()}")
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Disk)) == 3
    db.dispose()
    assert main(["db", "import", str(source), "--user", "Niemand"]) == 1
