"""Löschprotokolle (PDF) an Platten (Roadmap M2.2)."""

import pytest
from sqlalchemy import select
from test_authz import World

from diskatlas.db.models import Disk, DiskAttachment
from diskatlas.services import attachments, ownership

PDF = b"%PDF-1.7\n% nwipe certificate\n1 0 obj <<>> endobj\ntrailer <<>>\n%%EOF\n"


@pytest.fixture
def world(db, tmp_path) -> World:
    w = World(db, tmp_path)
    w.ingest_disk("anna", "sn:A1", "pc")
    return w


def upload(world, who, data=PDF, name="nwipe_report_A1.pdf", disk_key="sn:A1"):
    return world.web[who].post(f"/disks/{world.disk_id(disk_key)}/attachments",
                               files={"file": (name, data, "application/pdf")})


def rows(world):
    with world.db.session() as s:
        return list(s.scalars(select(DiskAttachment)))


def test_upload_download_and_delete(world):
    r = upload(world, "anna")
    assert r.status_code == 303 and "pmsg" in r.headers["location"]
    (row,) = rows(world)
    assert (row.filename, row.size, row.uploaded_by_user_id) == (
        "nwipe_report_A1.pdf", len(PDF), world.ids["anna"])
    stored = attachments.STORE.file(row.id)
    assert stored.read_bytes() == PDF and "nwipe" not in stored.name, "Zufallsname auf der Platte"
    page = world.web["anna"].get(f"/disks/{world.disk_id('sn:A1')}").text
    assert "nwipe_report_A1.pdf" in page and f"/attachments/{row.id}" in page
    got = world.web["anna"].get(f"/attachments/{row.id}")
    assert got.status_code == 200 and got.content == PDF
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"].startswith("attachment")
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in got.headers["content-security-policy"]
    assert world.web["anna"].post(f"/attachments/{row.id}/delete").status_code == 303
    assert rows(world) == [] and not stored.exists()


def test_several_reports_per_disk_newest_first(world):
    upload(world, "anna", name="erster.pdf")
    upload(world, "anna", name="zweiter.pdf")
    page = world.web["anna"].get(f"/disks/{world.disk_id('sn:A1')}").text
    assert page.index("zweiter.pdf") < page.index("erster.pdf")


@pytest.mark.parametrize(("data", "message"), [
    (b"<html><script>alert(1)</script>", "Nur PDF"),
    (b"", "Nur PDF"),
    (b"%PDF-" + b"x" * attachments.MAX_BYTES, "zu groß"),
])
def test_only_pdfs_within_the_size_limit(world, data, message):
    r = upload(world, "anna", data=data)
    assert "perr" in r.headers["location"]
    assert message in world.web["anna"].get(r.headers["location"]).text
    assert rows(world) == []
    assert attachments.STORE.names() == set()


def test_display_name_is_cleaned(world):
    upload(world, "anna", name="../../etc/bericht")
    (row,) = rows(world)
    assert row.filename == "bericht.pdf"
    assert attachments.clean_filename("C:\\x\\pass\x00wd\u202e.pdf") == "passwd.pdf"
    assert attachments.clean_filename("...") == "protokoll.pdf"


def test_shared_users_can_download_but_not_change(world):
    upload(world, "anna")
    (row,) = rows(world)
    disk_id = world.disk_id("sn:A1")
    bob = world.web["bob"]
    assert bob.get(f"/attachments/{row.id}").status_code == 404, "ohne Freigabe unsichtbar"
    assert upload(world, "bob").status_code == 404
    world.web["anna"].post(f"/disks/{disk_id}/shares", data={"user_id": world.ids["bob"]})
    assert bob.get(f"/attachments/{row.id}").content == PDF
    assert upload(world, "bob").status_code == 403
    assert bob.post(f"/attachments/{row.id}/delete").status_code == 403
    assert "Hochladen" not in bob.get(f"/disks/{disk_id}").text
    assert len(rows(world)) == 1


def test_foreign_tokens_and_anonymous_get_nothing(world):
    upload(world, "anna")
    (row,) = rows(world)
    assert world.agent("bob").get(f"/attachments/{row.id}").status_code == 404
    assert world.agent("anna").get(f"/attachments/{row.id}").status_code == 200, \
        "Client-Token gilt wie die übrige API für den eigenen Benutzer"
    from fastapi.testclient import TestClient

    anonymous = TestClient(world.app, follow_redirects=False).get(f"/attachments/{row.id}")
    assert anonymous.status_code == 303 and "/login" in anonymous.headers["location"]
    assert world.web["anna"].get("/attachments/../etc").status_code == 404
    assert world.web["anna"].get("/attachments/" + "0" * 32).status_code == 404


def test_reports_stay_with_the_disk_on_transfer(world):
    upload(world, "anna")
    world.ingest_disk("bob", "sn:A1", "pc-bob")  # Bob meldet Annas Platte: Übernahmeantrag
    with world.db.session() as s:
        (request,) = ownership.pending_transfers(s, world.ids["anna"])
        ownership.approve_transfer(s, request)
        s.commit()
    (row,) = rows(world)
    assert world.web["bob"].get(f"/attachments/{row.id}").content == PDF
    assert world.web["anna"].get(f"/attachments/{row.id}").status_code == 404


def test_deleting_the_disk_removes_its_files(world):
    upload(world, "anna")
    (row,) = rows(world)
    path = attachments.STORE.file(row.id)
    world.web["anna"].post(f"/disks/{world.disk_id('sn:A1')}/delete")
    with world.db.session() as s:
        assert s.scalar(select(Disk).where(Disk.disk_key == "sn:A1")) is None
    assert rows(world) == [] and not path.exists()


def test_orphans_are_purged_at_start(world):
    upload(world, "anna")
    (row,) = rows(world)
    orphan = attachments.STORE.file("f" * 32)
    orphan.write_bytes(b"%PDF-")
    (attachments.STORE.path / "halb.tmp").write_bytes(b"x")
    with world.db.session() as s:
        assert attachments.purge_orphans(s) == 1
    assert not orphan.exists() and attachments.STORE.file(row.id).exists()
    assert not list(attachments.STORE.path.glob("*.tmp"))


def test_storage_lives_next_to_the_database_in_use(world, db):
    from pathlib import Path

    db_file = Path(db.url.removeprefix("sqlite:///"))
    assert attachments.STORE.path == db_file.parent / "attachments"
    from diskatlas.services import relay

    assert relay.STORE.path == db_file.parent / "relay"


def test_local_mode_without_login(client, db):
    from conftest import make_disk

    from diskatlas.agent.sinks import DatabaseSink

    DatabaseSink(db).report_disk("pc", make_disk("sn:LOC"))
    with db.session() as s:
        disk_id = s.scalar(select(Disk.id))
    r = client.post(f"/disks/{disk_id}/attachments", files={"file": ("p.pdf", PDF)},
                    follow_redirects=False)
    assert r.status_code == 303
    page = client.get(f"/disks/{disk_id}").text
    assert "p.pdf" in page and "<th>Von</th>" not in page
