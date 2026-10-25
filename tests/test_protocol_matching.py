"""Löschprotokolle per Seriennummer zuordnen (Roadmap M2.3)."""

import pytest
from conftest import make_disk
from pdfmaker import text_pdf
from sqlalchemy import select
from test_authz import World

from diskatlas.db.models import DiskAttachment
from diskatlas.services import attachments, pdftext


@pytest.fixture
def world(db, tmp_path) -> World:
    w = World(db, tmp_path)
    for key in ("sn:WD-WCC4E1111111", "sn:ZA2222222", "sn:ZA22222229"):
        w.ingest_disk("anna", key, "pc")
    w.ingest_disk("bob", "sn:BOB3333333", "pc-bob")
    return w


def report(serial: str, status: str = "ERASED") -> bytes:
    return text_pdf(["nwipe - certificate of erasure", f"Serial number: {serial}",
                     "Method: DoD Short", f"Status: {status}"])


def bulk(world, who, *files):
    return world.web[who].post("/protocols", files=[
        ("files", (name, data, "application/pdf")) for name, data in files])


def rows(world):
    with world.db.session() as s:
        return {a.filename: (a.disk.serial if a.disk else None, a.detected_serial)
                for a in s.scalars(select(DiskAttachment))}


def test_serials_are_compared_without_spaces_and_dashes():
    assert pdftext.mentions("Serial: WD - WCC4E 1111111", "WD-WCC4E1111111")
    assert pdftext.mentions("serial wdwcc4e1111111", "WD-WCC4E1111111")
    assert not pdftext.mentions("Serial: ABC12", "ABC12"), "zu kurz, um sicher zu sein"
    assert not pdftext.mentions("", "WD-WCC4E1111111")


def test_bulk_upload_assigns_by_serial_and_keeps_the_rest_open(world):
    r = bulk(world, "anna",
             ("a.pdf", report("WD-WCC4E1111111")),
             ("b.pdf", report("ZA22222229")),  # ZA2222222 steckt darin, die längere gewinnt
             ("fremd.pdf", report("BOB3333333")),  # Bobs Platte: nie automatisch
             ("ohne.pdf", report("UNBEKANNT99")),
             ("kaputt.pdf", b"%PDF-1.4 kaputt"),
             ("bild.png", b"\x89PNG..."))
    location = r.headers["location"]
    assert r.status_code == 303 and "2+zugeordnet" in location and "3+offen" in location
    assert "bild.png.pdf" in location or "bild.png" in location, "abgelehnte Datei wird genannt"
    got = rows(world)
    assert got["a.pdf"] == ("WD-WCC4E1111111", "WD-WCC4E1111111")
    assert got["b.pdf"] == ("ZA22222229", "ZA22222229")
    assert got["fremd.pdf"] == got["ohne.pdf"] == got["kaputt.pdf"] == (None, None)
    page = world.web["anna"].get("/protocols").text
    assert "Offen (3)" in page and "fremd.pdf" in page and "BOB3333333" not in page


def test_document_date_is_read_from_the_pdf(world):
    bulk(world, "anna", ("a.pdf", report("WD-WCC4E1111111")))
    with world.db.session() as s:
        (row,) = s.scalars(select(DiskAttachment))
        assert row.document_date.isoformat() == "2026-10-20T14:30:00"


def test_open_reports_are_private_and_can_be_assigned(world):
    bulk(world, "anna", ("ohne.pdf", report("UNBEKANNT99")))
    with world.db.session() as s:
        (att,) = s.scalars(select(DiskAttachment))
    bob, anna = world.web["bob"], world.web["anna"]
    assert bob.get(f"/attachments/{att.id}").status_code == 404
    assert "ohne.pdf" not in bob.get("/protocols").text
    assert bob.post(f"/attachments/{att.id}/assign",
                    data={"disk_id": world.disk_id("sn:BOB3333333")}).status_code == 404
    # Anna darf nur an eigene Platten hängen
    assert anna.post(f"/attachments/{att.id}/assign",
                     data={"disk_id": world.disk_id("sn:BOB3333333")}).status_code == 404
    assert anna.get(f"/attachments/{att.id}").content.startswith(b"%PDF-")
    r = anna.post(f"/attachments/{att.id}/assign", data={"disk_id": world.disk_id("sn:ZA2222222")})
    assert r.status_code == 303
    assert rows(world)["ohne.pdf"] == ("ZA2222222", None)
    again = anna.post(f"/attachments/{att.id}/assign",
                      data={"disk_id": world.disk_id("sn:ZA22222229")})
    assert again.status_code == 409


def test_open_report_can_be_discarded(world):
    bulk(world, "anna", ("ohne.pdf", report("UNBEKANNT99")))
    with world.db.session() as s:
        (att,) = s.scalars(select(DiskAttachment))
    assert world.web["bob"].post(f"/attachments/{att.id}/delete").status_code == 404
    assert world.web["anna"].post(f"/attachments/{att.id}/delete").status_code == 303
    assert rows(world) == {} and attachments.STORE.names() == set()


def test_single_upload_warns_when_the_serial_is_missing(world):
    disk_id = world.disk_id("sn:ZA2222222")
    anna = world.web["anna"]
    ok = anna.post(f"/disks/{disk_id}/attachments",
                   files={"file": ("r.pdf", report("ZA2222222"), "application/pdf")})
    assert "richtige+Platte" not in ok.headers["location"]
    wrong = anna.post(f"/disks/{disk_id}/attachments",
                      files={"file": ("x.pdf", report("WD-WCC4E1111111"), "application/pdf")})
    assert "richtige+Platte" in wrong.headers["location"], "trotzdem angehängt, aber mit Hinweis"
    assert set(rows(world)) == {"r.pdf", "x.pdf"}
    assert "✓ Seriennummer" in anna.get(f"/disks/{disk_id}").text


def test_too_many_files_at_once(world, monkeypatch):
    from diskatlas.web import protocols

    monkeypatch.setattr(protocols, "MAX_FILES", 1)
    r = bulk(world, "anna", ("a.pdf", report("X1234567")), ("b.pdf", report("X1234567")))
    assert "H%C3%B6chstens" in r.headers["location"] and rows(world) == {}


def test_local_mode_bulk(client, db):
    from diskatlas.agent.sinks import DatabaseSink

    DatabaseSink(db).report_disk("pc", make_disk("sn:LOCAL123456"))
    r = client.post("/protocols", files=[("files", ("l.pdf", report("LOCAL123456")))],
                    follow_redirects=False)
    assert "1+zugeordnet" in r.headers["location"]
    assert "Löschprotokolle" in client.get("/").text
