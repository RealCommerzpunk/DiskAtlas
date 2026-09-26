import json

from conftest import make_disk
from sqlalchemy import select

from diskatlas.db.models import Disk, SmartSnapshot
from diskatlas.probe import catalog
from diskatlas.services import ingest


def test_family_to_vendor_and_product_line():
    f = catalog.product_line_from_family
    assert f("Seagate IronWolf") == ("Seagate", "IronWolf")
    assert f("Western Digital Red Pro") == ("Western Digital", "WD Red Pro")
    assert f("Western Digital Caviar Green (AF)") == ("Western Digital", "WD Caviar Green")
    assert f("SandForce Driven SSDs") == (None, None), "Controller-Familie ist kein Produkt"
    assert f("Samsung based SSDs") == ("Samsung", None)
    assert f(None) == (None, None)


def test_vendor_from_model_prefix():
    v = catalog.vendor_from_model
    assert v("ST8000VN004-3CP101") == "Seagate"
    assert v("WDC WD20EFRX-68AX9N0") == "Western Digital"
    assert v("KINGSTON SMS200S3120G") == "Kingston"
    assert v("TOSHIBA MG04ACA200E") == "Toshiba"
    assert v("Unbekanntes Ding") is None


def test_drivedb_parser_handles_comments_and_split_literals():
    text = '''
    { "Seagate IronWolf", // tested with ST4000VN008
        // weiterer Kommentar mit "Anführungszeichen"
      "ST(1|2|4)000VN00"
      "8-.*",
      "", "", "-v 1,raw48 // kein Kommentar"
    },
    { "DEFAULT", "", "", "", "" },
    '''
    entries = catalog._parse_drivedb(text)
    assert [family for _, family in entries] == ["Seagate IronWolf"]
    assert entries[0][0].fullmatch("ST4000VN008-2DR166")


def test_identify_prefers_smart_family_then_drivedb(monkeypatch):
    fake = ((catalog.re.compile("FOO.*"), "Seagate Foo"),)
    monkeypatch.setattr(catalog, "_drivedb", lambda: fake)
    assert catalog.identify("FOO1", "Western Digital Red") == catalog.Identity(
        "Western Digital", "WD Red"
    )
    assert catalog.identify("FOO1") == catalog.Identity("Seagate", "Foo")
    assert catalog.identify("WDC WD99") == catalog.Identity("Western Digital", None)


def test_upsert_sets_identity_and_backfill_fills_old_rows(db):
    info = make_disk()
    info.model = "ST8000VN004-3CP101"
    with db.session() as s:
        disk = ingest.upsert_disk(s, "pc", info)
        assert disk.vendor == "Seagate"
    with db.session() as s:
        disk = s.scalar(select(Disk))
        assert disk.vendor == "Seagate" and disk.product_line == "IronWolf"
        assert disk.brand == "Seagate IronWolf"
        # Altbestand: Felder leeren, Familie nur noch in den SMART-Rohdaten
        disk.vendor = disk.product_line = None
        s.add(SmartSnapshot(disk_id=disk.id, taken_at=disk.last_seen, health="ok",
                            raw_json=json.dumps({"model_family": "Seagate IronWolf Pro"})))
    with db.session() as s:
        assert ingest.backfill_identity(s) == 1
    with db.session() as s:
        disk = s.scalar(select(Disk))
        assert (disk.vendor, disk.product_line) == ("Seagate", "IronWolf Pro")
